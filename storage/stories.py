"""stories / daily_publish_plan / phase_transitions 的 CRUD。

行为与旧 ``review_queue.db`` 完全一致；建表与迁移见 ``storage.schema``。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from storage.connection import connect
from storage.models import DailyPublishPlan, Story

REVIEWABLE_STATUSES: tuple[str, ...] = ("pending", "needs_human")
TERMINAL_STATUSES: tuple[str, ...] = ("approved", "published", "rejected", "failed")


def insert_story(db_path: str | Path, story: Story) -> int:
    """Insert a c_pipeline story record and return its id."""

    with connect(db_path) as connection:
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

    with connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(_SELECT_STORY_SQL + " WHERE id = ?", (story_id,)).fetchone()
    return story_from_row(row) if row is not None else None


def list_reviewable_stories(db_path: str | Path) -> list[Story]:
    """Return stories awaiting human or AI review (pending / needs_human)."""

    with connect(db_path) as connection:
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

    with connect(db_path) as connection:
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
    """Advance the pipeline state machine for a story.

    Also appends a row to ``phase_transitions`` so the dashboard can render a
    timeline of when each phase entered / completed. The transition is logged
    even when the phase string is identical to the current value, since the
    orchestrator may re-emit the same marker on retry.
    """

    with connect(db_path) as connection:
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
        if cursor.rowcount > 0:
            connection.execute(
                "INSERT INTO phase_transitions (story_id, phase) VALUES (?, ?)",
                (story_id, current_phase),
            )
        return cursor.rowcount > 0


def list_phase_transitions(db_path: str | Path, story_id: int) -> list[dict[str, str]]:
    """Return chronological phase transitions for one story.

    Each entry is ``{"phase": "phase_2_done", "occurred_at": "2026-05-08T..."}``
    in the order they were recorded. Returns an empty list when the story has
    no transitions (e.g. legacy rows created before this table existed).
    """

    with connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT phase, occurred_at
            FROM phase_transitions
            WHERE story_id = ?
            ORDER BY id ASC
            """,
            (story_id,),
        ).fetchall()
    return [{"phase": r["phase"], "occurred_at": r["occurred_at"]} for r in rows]


def update_story_ai_review(
    db_path: str | Path,
    story_id: int,
    score: float,
    attempts: int,
    status: str | None = None,
) -> bool:
    """Persist AI review outcome for a story."""

    with connect(db_path) as connection:
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

    with connect(db_path) as connection:
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

    with connect(db_path) as connection:
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


def upsert_daily_publish_plan(db_path: str | Path, plan: DailyPublishPlan) -> None:
    """Insert or replace a daily publish plan row for ``plan.date``."""

    if not isinstance(plan.slots_json, str):
        raise TypeError("DailyPublishPlan.slots_json must be a JSON string")
    json.loads(plan.slots_json)

    with connect(db_path) as connection:
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

    with connect(db_path) as connection:
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

    keys = row.keys() if hasattr(row, "keys") else []
    cancel_value = 0
    if "cancel_requested" in keys:
        try:
            cancel_value = int(row["cancel_requested"] or 0)
        except (TypeError, ValueError):
            cancel_value = 0
    return Story(
        id=int(row["id"]),
        title=str(row["title"]),
        status=str(row["status"]),
        pipeline_version=str(row["pipeline_version"]),
        preset_name=str(row["preset_name"] or "") if "preset_name" in keys else "",
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
        cancel_requested=cancel_value,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_SELECT_STORY_SQL = """
SELECT id, title, status, pipeline_version, preset_name, work_dir, current_phase,
       final_content_path, pipeline_cost_cny, target_length,
       emotion, genre, hint_title, summary,
       ai_review_score, ai_review_attempts, content,
       cancel_requested,
       created_at, updated_at
FROM stories
""".strip()


def request_story_cancel(db_path: str | Path, story_id: int) -> bool:
    """Set ``cancel_requested = 1`` for a story (cooperative cancel signal)."""

    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET cancel_requested = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (story_id,),
        )
        return cursor.rowcount > 0


def is_cancel_requested(db_path: str | Path, story_id: int) -> bool:
    """Return True if ``cancel_requested = 1`` for the given story."""

    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT cancel_requested FROM stories WHERE id = ?",
            (story_id,),
        ).fetchone()
    if row is None:
        return False
    try:
        return bool(int(row[0] or 0))
    except (TypeError, ValueError):
        return False


__all__ = [
    "REVIEWABLE_STATUSES",
    "TERMINAL_STATUSES",
    "add_pipeline_cost",
    "get_daily_publish_plan",
    "get_story",
    "insert_story",
    "is_cancel_requested",
    "list_phase_transitions",
    "list_reviewable_stories",
    "request_story_cancel",
    "story_from_row",
    "update_story_ai_review",
    "update_story_metadata",
    "update_story_phase",
    "update_story_status",
    "upsert_daily_publish_plan",
]
