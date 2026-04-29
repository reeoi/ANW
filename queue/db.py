"""SQLite utilities for the local novel queue."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config_loader import LoadedConfig
from queue.models import Story

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    score REAL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    review_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_stories_status_created_at
ON stories(status, created_at);
"""

REQUIRED_COLUMNS: dict[str, str] = {
    "score": "REAL",
    "retry_count": "INTEGER NOT NULL DEFAULT 0",
    "review_notes": "TEXT",
    "published_at": "TEXT",
}

REVIEWABLE_STATUSES = ("pending", "needs_human")


def initialize_database(config: LoadedConfig) -> Path:
    """Create or migrate the SQLite database and queue table."""
    db_path = get_database_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
        _ensure_required_columns(connection)
    return db_path


def get_database_path(config: LoadedConfig) -> Path:
    """Return the configured SQLite path."""
    return Path(str(config.data.get("database", {}).get("sqlite_path", "data/anp.sqlite3")))


def insert_story(db_path: str | Path, story: Story) -> int:
    """Insert a story into the queue and return the new record id."""
    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            INSERT INTO stories (title, content, status, score, retry_count, review_notes, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                story.title,
                story.content,
                story.status,
                story.score,
                story.retry_count,
                story.review_notes,
                story.published_at,
            ),
        )
        return int(cursor.lastrowid)


def get_story(db_path: str | Path, story_id: int) -> Story | None:
    """Fetch one story by id."""
    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT id, title, content, status, score, retry_count, review_notes,
                   created_at, updated_at, published_at
            FROM stories
            WHERE id = ?
            """,
            (story_id,),
        ).fetchone()
    if row is None:
        return None
    return story_from_row(row)


def list_reviewable_stories(db_path: str | Path) -> list[Story]:
    """Return stories that need human or AI review, newest first."""
    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, title, content, status, score, retry_count, review_notes,
                   created_at, updated_at, published_at
            FROM stories
            WHERE status IN (?, ?)
            ORDER BY created_at DESC, id DESC
            """,
            REVIEWABLE_STATUSES,
        ).fetchall()
    return [story_from_row(row) for row in rows]


def update_story_status(
    db_path: str | Path,
    story_id: int,
    status: str,
    review_notes: str | None = None,
    score: float | None = None,
) -> bool:
    """Update a story's review status and optional score/notes."""
    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET status = ?,
                review_notes = COALESCE(?, review_notes),
                score = COALESCE(?, score),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, review_notes, score, story_id),
        )
        return cursor.rowcount > 0


def update_story_content(
    db_path: str | Path,
    story_id: int,
    title: str,
    content: str,
    review_notes: str | None = None,
) -> bool:
    """Update editable story fields while preserving review status."""
    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute(
            """
            UPDATE stories
            SET title = ?,
                content = ?,
                review_notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, content, review_notes, story_id),
        )
        return cursor.rowcount > 0


def story_from_row(row: sqlite3.Row) -> Story:
    """Convert a sqlite row from the stories table into a Story dataclass."""
    return Story(
        id=int(row["id"]),
        title=str(row["title"]),
        content=str(row["content"]),
        status=str(row["status"]),
        score=row["score"],
        retry_count=int(row["retry_count"]),
        review_notes=row["review_notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        published_at=row["published_at"],
    )


def _ensure_required_columns(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute("PRAGMA table_info(stories)")}
    for column, definition in REQUIRED_COLUMNS.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE stories ADD COLUMN {column} {definition}")


__all__ = [
    "REVIEWABLE_STATUSES",
    "SCHEMA",
    "get_database_path",
    "get_story",
    "initialize_database",
    "insert_story",
    "list_reviewable_stories",
    "story_from_row",
    "update_story_content",
    "update_story_status",
]
