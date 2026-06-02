"""Long novel database — books / volumes / chapters tables.

Shares the same SQLite connection as ``review_queue.db``.
Schema is created lazily via ``initialize_database``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS ln_books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    genre TEXT NOT NULL DEFAULT '',
    premise TEXT NOT NULL DEFAULT '',
    target_chapters INTEGER NOT NULL DEFAULT 30,
    target_words_per_chapter INTEGER NOT NULL DEFAULT 3000,
    total_volumes INTEGER NOT NULL DEFAULT 1,
    current_volume INTEGER NOT NULL DEFAULT 1,
    current_chapter INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'setup',
    work_dir TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ln_volumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES ln_books(id) ON DELETE CASCADE,
    volume_number INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    chapter_count INTEGER NOT NULL DEFAULT 30,
    status TEXT NOT NULL DEFAULT 'planned',
    outline_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, volume_number)
);

CREATE TABLE IF NOT EXISTS ln_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES ln_books(id) ON DELETE CASCADE,
    volume_number INTEGER NOT NULL,
    chapter_number INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'outline_only',
    target_words INTEGER NOT NULL DEFAULT 3000,
    actual_words INTEGER NOT NULL DEFAULT 0,
    outline_path TEXT,
    draft_path TEXT,
    review_status TEXT DEFAULT NULL,
    ai_review_json TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(book_id, chapter_number)
);

CREATE INDEX IF NOT EXISTS idx_ln_volumes_book ON ln_volumes(book_id);
CREATE INDEX IF NOT EXISTS idx_ln_chapters_book ON ln_chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_ln_chapters_status ON ln_chapters(book_id, status);
"""


def initialize_long_novel_tables(db_path: str | Path) -> None:
    """Ensure the long-novel tables exist in the given database."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# ── Book CRUD ────────────────────────────────────────────────────────


def list_books(db_path: str | Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM ln_books ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_book(db_path: str | Path, book_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM ln_books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_book(
    db_path: str | Path,
    title: str,
    genre: str = "",
    premise: str = "",
    target_chapters: int = 30,
    target_words_per_chapter: int = 3000,
    work_dir: str = "",
) -> int:
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        """INSERT INTO ln_books (title, genre, premise, target_chapters,
           target_words_per_chapter, work_dir, status)
           VALUES (?, ?, ?, ?, ?, ?, 'setup')""",
        (title, genre, premise, target_chapters, target_words_per_chapter, work_dir),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


def update_book(db_path: str | Path, book_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [book_id]
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"UPDATE ln_books SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        values,
    )
    conn.commit()
    conn.close()


def delete_book(db_path: str | Path, book_id: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM ln_books WHERE id=?", (book_id,))
    conn.commit()
    conn.close()


# ── Volume CRUD ──────────────────────────────────────────────────────


def list_volumes(db_path: str | Path, book_id: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM ln_volumes WHERE book_id=? ORDER BY volume_number",
        (book_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_volume(
    db_path: str | Path, book_id: int, volume_number: int
) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM ln_volumes WHERE book_id=? AND volume_number=?",
        (book_id, volume_number),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_volume(
    db_path: str | Path,
    book_id: int,
    volume_number: int,
    title: str = "",
    chapter_count: int = 30,
    status: str = "planned",
    outline_path: str | None = None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ln_volumes (book_id, volume_number, title, chapter_count, status, outline_path)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(book_id, volume_number) DO UPDATE SET
           title=excluded.title, chapter_count=excluded.chapter_count,
           status=excluded.status, outline_path=excluded.outline_path""",
        (book_id, volume_number, title, chapter_count, status, outline_path),
    )
    conn.commit()
    conn.close()


# ── Chapter CRUD ─────────────────────────────────────────────────────


def list_chapters(
    db_path: str | Path, book_id: int, volume_number: int | None = None
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if volume_number is not None:
        rows = conn.execute(
            "SELECT * FROM ln_chapters WHERE book_id=? AND volume_number=? ORDER BY chapter_number",
            (book_id, volume_number),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ln_chapters WHERE book_id=? ORDER BY chapter_number",
            (book_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_chapter(
    db_path: str | Path, book_id: int, chapter_number: int
) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM ln_chapters WHERE book_id=? AND chapter_number=?",
        (book_id, chapter_number),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_chapter(
    db_path: str | Path,
    book_id: int,
    volume_number: int,
    chapter_number: int,
    title: str = "",
    status: str = "outline_only",
    target_words: int = 3000,
    actual_words: int = 0,
    outline_path: str | None = None,
    draft_path: str | None = None,
    review_status: str | None = None,
    ai_review_json: str | None = None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ln_chapters (book_id, volume_number, chapter_number, title,
           status, target_words, actual_words, outline_path, draft_path,
           review_status, ai_review_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(book_id, chapter_number) DO UPDATE SET
           title=excluded.title, status=excluded.status,
           target_words=excluded.target_words, actual_words=excluded.actual_words,
           outline_path=excluded.outline_path, draft_path=excluded.draft_path,
           review_status=excluded.review_status, ai_review_json=excluded.ai_review_json,
           updated_at=CURRENT_TIMESTAMP""",
        (
            book_id,
            volume_number,
            chapter_number,
            title,
            status,
            target_words,
            actual_words,
            outline_path,
            draft_path,
            review_status,
            ai_review_json,
        ),
    )
    conn.commit()
    conn.close()


def normalize_saved_chapter_statuses(db_path: str | Path, book_id: int) -> int:
    """Treat legacy review-gate results with saved text as ordinary drafts."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        """UPDATE ln_chapters
           SET status='draft', updated_at=CURRENT_TIMESTAMP
           WHERE book_id=? AND status='needs_human'
             AND COALESCE(draft_path, '') <> ''""",
        (book_id,),
    )
    conn.commit()
    updated = int(cursor.rowcount or 0)
    conn.close()
    return updated


def get_next_chapter(
    db_path: str | Path, book_id: int
) -> dict[str, Any] | None:
    """Return the first chapter with status != 'published' (or the first unwritten)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM ln_chapters WHERE book_id=? AND status NOT IN ('published','writing') ORDER BY chapter_number LIMIT 1",
        (book_id,),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM ln_chapters WHERE book_id=? AND status='outline_only' ORDER BY chapter_number LIMIT 1",
            (book_id,),
        ).fetchone()
    conn.close()
    return dict(row) if row else None
