"""SQLite utilities for the c_pipeline story queue.

Schema is defined in docs/c_pipeline_plan.md §3.2 and consists of three tables:

- ``stories`` — story records with multi-phase state (work_dir, current_phase,
  final_content_path, ai_review_score, ai_review_attempts, ...).
- ``daily_publish_plan`` — per-day publish slots produced by plan_today_publishes.
- ``pipeline_cost_log`` — per-call cost telemetry consumed by cost_tracker.

Old columns (score / retry_count / review_notes / is_dry_run / published_at)
are deleted; the legacy ``content`` column stays as a nullable compatibility
field but new rows leave it NULL.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from config_loader import LoadedConfig
from review_queue.models import DailyPublishPlan, PipelineCostLogEntry, Story

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    pipeline_version TEXT NOT NULL DEFAULT 'c1',
    work_dir TEXT NOT NULL DEFAULT '',
    current_phase TEXT NOT NULL DEFAULT 'phase_0',
    final_content_path TEXT,
    pipeline_cost_cny REAL DEFAULT 0,
    target_length INTEGER,
    emotion TEXT,
    genre TEXT,
    hint_title TEXT,
    summary TEXT,
    ai_review_score REAL,
    ai_review_attempts INTEGER DEFAULT 0,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stories_status ON stories(status);
CREATE INDEX IF NOT EXISTS idx_stories_current_phase ON stories(current_phase);

CREATE TABLE IF NOT EXISTS daily_publish_plan (
    date DATE PRIMARY KEY,
    planned_count INTEGER NOT NULL,
    slots_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipeline_cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER,
    phase TEXT,
    model TEXT,
    input_tokens INTEGER,
    cached_tokens INTEGER,
    output_tokens INTEGER,
    cost_cny REAL,
    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(story_id) REFERENCES stories(id)
);

CREATE INDEX IF NOT EXISTS idx_cost_log_occurred_at ON pipeline_cost_log(occurred_at);
"""

REVIEWABLE_STATUSES: tuple[str, ...] = ("pending", "needs_human")
TERMINAL_STATUSES: tuple[str, ...] = ("approved", "published", "rejected", "failed")


def initialize_database(config: LoadedConfig) -> Path:
    """Create the SQLite database and the c_pipeline schema if missing."""

    db_path = get_database_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
    try:
        from review_queue.metrics import ensure_metrics_schema

        ensure_metrics_schema(db_path)
    except Exception:  # pragma: no cover - metrics is best-effort
        pass
    return db_path


def get_database_path(config: LoadedConfig) -> Path:
    """Return the configured SQLite path."""

    return Path(str(config.data.get("database", {}).get("sqlite_path", "data/anp.sqlite3")))


def insert_story(db_path: str | Path, story: Story) -> int:
    """Insert a c_pipeline story record and return its id."""

    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            INSERT INTO stories (
                title, status, pipeline_version, work_dir, current_phase,
                final_content_path, pipeline_cost_cny, target_length,
                emotion, genre, hint_title, summary,
                ai_review_score, ai_review_attempts, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                story.title,
                story.status,
                story.pipeline_version,
                story.work_dir,
                story.current_phase,
                story.final_content_path,
                float(story.pipeline_cost_cny or 0.0),
                story.target_length,
                story.emotion,
                story.genre,
                story.hint_title,
                story.summary,
                story.ai_review_score,
                int(story.ai_review_attempts or 0),
                story.content,
            ),
        )
        return int(cursor.lastrowid)


def get_story(db_path: str | Path, story_id: int) -> Story | None:
    """Fetch one story by id, or None if missing."""

    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(_SELECT_STORY_SQL + " WHERE id = ?", (story_id,)).fetchone()
    return story_from_row(row) if row is not None else None


def list_reviewable_stories(db_path: str | Path) -> list[Story]:
    """Return stories awaiting human or AI review (pending / needs_human)."""

    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            _SELECT_STORY_SQL + " WHERE status IN (?, ?) ORDER BY created_at DESC, id DESC",
            REVIEWABLE_STATUSES,
        ).fetchall()
    return [story_from_row(row) for row in rows]


def update_story_status(
    db_path: str | Path,
    story_id: int,
    status: str,
    summary: str | None = None,
    ai_review_score: float | None = None,
) -> bool:
    """Update a story's status and optional summary / ai_review_score.

    Replaces the old (review_notes, score) signature: ``summary`` becomes the
    Phase 1 summary persisted on the story; ``ai_review_score`` is the AI
    review total.
    """

    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET status = ?,
                summary = COALESCE(?, summary),
                ai_review_score = COALESCE(?, ai_review_score),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, summary, ai_review_score, story_id),
        )
        return cursor.rowcount > 0


def update_story_phase(
    db_path: str | Path,
    story_id: int,
    current_phase: str,
    final_content_path: str | None = None,
) -> bool:
    """Advance the pipeline state machine for a story."""

    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET current_phase = ?,
                final_content_path = COALESCE(?, final_content_path),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (current_phase, final_content_path, story_id),
        )
        return cursor.rowcount > 0


def update_story_ai_review(
    db_path: str | Path,
    story_id: int,
    score: float,
    attempts: int,
    status: str | None = None,
) -> bool:
    """Persist AI review outcome for a story."""

    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET ai_review_score = ?,
                ai_review_attempts = ?,
                status = COALESCE(?, status),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (float(score), int(attempts), status, story_id),
        )
        return cursor.rowcount > 0


def update_story_metadata(
    db_path: str | Path,
    story_id: int,
    *,
    title: str | None = None,
    summary: str | None = None,
    emotion: str | None = None,
    genre: str | None = None,
    hint_title: str | None = None,
    target_length: int | None = None,
) -> bool:
    """Edit story metadata while preserving status / phase / cost."""

    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET title = COALESCE(?, title),
                summary = COALESCE(?, summary),
                emotion = COALESCE(?, emotion),
                genre = COALESCE(?, genre),
                hint_title = COALESCE(?, hint_title),
                target_length = COALESCE(?, target_length),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, summary, emotion, genre, hint_title, target_length, story_id),
        )
        return cursor.rowcount > 0


def add_pipeline_cost(db_path: str | Path, story_id: int, delta_cny: float) -> bool:
    """Add ``delta_cny`` to the story's accumulated pipeline cost."""

    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET pipeline_cost_cny = COALESCE(pipeline_cost_cny, 0) + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (float(delta_cny), story_id),
        )
        return cursor.rowcount > 0


def insert_pipeline_cost_log(db_path: str | Path, entry: PipelineCostLogEntry) -> int:
    """Append a row to ``pipeline_cost_log`` and return its id."""

    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            INSERT INTO pipeline_cost_log (
                story_id, phase, model,
                input_tokens, cached_tokens, output_tokens, cost_cny
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.story_id,
                entry.phase,
                entry.model,
                int(entry.input_tokens or 0),
                int(entry.cached_tokens or 0),
                int(entry.output_tokens or 0),
                float(entry.cost_cny or 0.0),
            ),
        )
        return int(cursor.lastrowid)


def upsert_daily_publish_plan(db_path: str | Path, plan: DailyPublishPlan) -> None:
    """Insert or replace a daily publish plan row for ``plan.date``."""

    if not isinstance(plan.slots_json, str):
        raise TypeError("DailyPublishPlan.slots_json must be a JSON string")
    json.loads(plan.slots_json)

    with sqlite3.connect(Path(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO daily_publish_plan (date, planned_count, slots_json)
            VALUES (?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                planned_count = excluded.planned_count,
                slots_json = excluded.slots_json
            """,
            (plan.date, int(plan.planned_count), plan.slots_json),
        )


def get_daily_publish_plan(db_path: str | Path, date: str) -> DailyPublishPlan | None:
    """Fetch one daily publish plan by date, or None."""

    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT date, planned_count, slots_json, created_at FROM daily_publish_plan WHERE date = ?",
            (date,),
        ).fetchone()
    if row is None:
        return None
    return DailyPublishPlan(
        date=str(row["date"]),
        planned_count=int(row["planned_count"]),
        slots_json=str(row["slots_json"]),
        created_at=row["created_at"],
    )


def story_from_row(row: sqlite3.Row) -> Story:
    """Convert a sqlite Row from ``stories`` into a Story dataclass."""

    return Story(
        id=int(row["id"]),
        title=str(row["title"]),
        status=str(row["status"]),
        pipeline_version=str(row["pipeline_version"]),
        work_dir=str(row["work_dir"] or ""),
        current_phase=str(row["current_phase"]),
        final_content_path=row["final_content_path"],
        pipeline_cost_cny=float(row["pipeline_cost_cny"] or 0.0),
        target_length=row["target_length"],
        emotion=row["emotion"],
        genre=row["genre"],
        hint_title=row["hint_title"],
        summary=row["summary"],
        ai_review_score=row["ai_review_score"],
        ai_review_attempts=int(row["ai_review_attempts"] or 0),
        content=row["content"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_SELECT_STORY_SQL = """
SELECT id, title, status, pipeline_version, work_dir, current_phase,
       final_content_path, pipeline_cost_cny, target_length,
       emotion, genre, hint_title, summary,
       ai_review_score, ai_review_attempts, content,
       created_at, updated_at
FROM stories
""".strip()


__all__ = [
    "REVIEWABLE_STATUSES",
    "TERMINAL_STATUSES",
    "SCHEMA",
    "add_pipeline_cost",
    "get_daily_publish_plan",
    "get_database_path",
    "get_story",
    "initialize_database",
    "insert_pipeline_cost_log",
    "insert_story",
    "list_reviewable_stories",
    "story_from_row",
    "update_story_ai_review",
    "update_story_metadata",
    "update_story_phase",
    "update_story_status",
    "upsert_daily_publish_plan",
]
