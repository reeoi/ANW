"""Unified theme database — standardized schema for themes from all sources.

Sources:
- seeds: scan_seeds.yaml → seed_evolver → theme_pool.json
- fanqie: FanqieRankTracker daily rankings
- manual: hand-curated themes
- history: derived from successful stories

Every theme is normalized to a unified format regardless of source.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme TEXT NOT NULL,
    genre TEXT NOT NULL DEFAULT '',
    emotion TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    target_type TEXT NOT NULL DEFAULT 'short',
    hint_title TEXT NOT NULL DEFAULT '',
    target_words_min INTEGER DEFAULT 8000,
    target_words_max INTEGER DEFAULT 15000,
    target_chapters INTEGER DEFAULT 0,
    audience TEXT DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    source_detail TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    fetched_at TEXT DEFAULT '',
    is_consumed INTEGER NOT NULL DEFAULT 0,
    ai_score REAL DEFAULT 0,
    raw_json TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_themes_genre ON themes(genre);
CREATE INDEX IF NOT EXISTS idx_themes_source ON themes(source);
CREATE INDEX IF NOT EXISTS idx_themes_type ON themes(target_type);
CREATE INDEX IF NOT EXISTS idx_themes_consumed ON themes(is_consumed);
CREATE INDEX IF NOT EXISTS idx_themes_fetched ON themes(fetched_at);
"""


def initialize_theme_tables(db_path: str | Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# ── CRUD ──────────────────────────────────────────────────────────────


def list_themes(
    db_path: str | Path,
    target_type: str | None = None,
    genre: str | None = None,
    source: str | None = None,
    consumed: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    where = []
    params: list[Any] = []
    if target_type:
        where.append("target_type=?")
        params.append(target_type)
    if genre:
        where.append("genre=?")
        params.append(genre)
    if source:
        where.append("source=?")
        params.append(source)
    if consumed is not None:
        where.append("is_consumed=?")
        params.append(1 if consumed else 0)
    sql = "SELECT * FROM themes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY fetched_at DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_theme(db_path: str | Path, theme_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def count_themes(db_path: str | Path, **filters: Any) -> int:
    conn = sqlite3.connect(str(db_path))
    where = []
    params: list[Any] = []
    for k, v in filters.items():
        if v is not None:
            where.append(f"{k}=?")
            params.append(v)
    sql = "SELECT COUNT(*) FROM themes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    count = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return count


def upsert_theme(db_path: str | Path, **fields: Any) -> int:
    """Insert or update a theme. Returns the theme id."""
    conn = sqlite3.connect(str(db_path))
    # Check if exists by theme text + source
    theme_text = fields.get("theme", "")
    source = fields.get("source", "manual")
    existing = conn.execute(
        "SELECT id FROM themes WHERE theme=? AND source=?",
        (theme_text, source),
    ).fetchone()

    if existing:
        # Update
        set_clause = ", ".join(f"{k}=?" for k in fields if k not in ("theme", "source"))
        values = [fields[k] for k in fields if k not in ("theme", "source")] + [existing[0]]
        conn.execute(
            f"UPDATE themes SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            values,
        )
        theme_id = existing[0]
    else:
        # Insert
        keys = list(fields.keys())
        placeholders = ", ".join("?" for _ in keys)
        values = [fields[k] for k in keys]
        cur = conn.execute(
            f"INSERT INTO themes ({', '.join(keys)}) VALUES ({placeholders})",
            values,
        )
        theme_id = cur.lastrowid

    conn.commit()
    conn.close()
    return theme_id


def mark_consumed(db_path: str | Path, theme_id: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE themes SET is_consumed=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (theme_id,),
    )
    conn.commit()
    conn.close()


def get_theme_stats(db_path: str | Path) -> dict[str, Any]:
    """Aggregate statistics for dashboard."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) as c FROM themes").fetchone()["c"]
    unconsumed = conn.execute("SELECT COUNT(*) as c FROM themes WHERE is_consumed=0").fetchone()["c"]

    genres = [dict(r) for r in conn.execute(
        "SELECT genre, COUNT(*) as count FROM themes GROUP BY genre ORDER BY count DESC LIMIT 20"
    ).fetchall()]

    sources = [dict(r) for r in conn.execute(
        "SELECT source, COUNT(*) as count, MAX(fetched_at) as last_fetch FROM themes GROUP BY source ORDER BY count DESC"
    ).fetchall()]

    types = [dict(r) for r in conn.execute(
        "SELECT target_type, COUNT(*) as count FROM themes GROUP BY target_type"
    ).fetchall()]

    recent = conn.execute(
        "SELECT MAX(fetched_at) as m FROM themes WHERE source='fanqie'"
    ).fetchone()["m"] or ""

    conn.close()
    return {
        "total": total,
        "unconsumed": unconsumed,
        "genres": genres,
        "sources": sources,
        "types": types,
        "last_fanqie_fetch": recent,
    }


# ── Import & Standardize ──────────────────────────────────────────────


def import_from_theme_pool(db_path: str | Path) -> int:
    """Import themes from the existing theme_pool.json."""
    pool_path = Path(db_path).parent.parent / "data" / "theme_pool.json"
    if not pool_path.exists():
        return 0

    try:
        data = json.loads(pool_path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("items", [])
    except (json.JSONDecodeError, OSError):
        return 0

    count = 0
    for item in items:
        tl = item.get("target_length", [8000, 15000])
        upsert_theme(
            db_path,
            theme=item.get("theme", ""),
            genre=item.get("genre", ""),
            emotion=item.get("emotion", ""),
            platform=item.get("target_platform", "番茄短篇"),
            target_type="short",
            hint_title=item.get("hint_title", ""),
            target_words_min=int(tl[0]) if isinstance(tl, list) and len(tl) >= 2 else 8000,
            target_words_max=int(tl[1]) if isinstance(tl, list) and len(tl) >= 2 else 15000,
            audience=item.get("expected_audience", ""),
            source="seeds",
            source_detail=f"pool_id={item.get('id', '')}",
            fetched_at=item.get("created_at", ""),
            is_consumed=item.get("consumed_count", 0) > 0,
            raw_json=json.dumps(item, ensure_ascii=False),
        )
        count += 1
    return count


def import_from_fanqie_cache(db_path: str | Path) -> int:
    """Import themes from cached FanqieRankTracker data.

    Uses the website's own category names as genre — no keyword guessing.
    Only imports Fanqie-sourced themes; skip old seed-based pool.
    """
    cache_path = Path(db_path).parent.parent / "data" / "fanqie_trends.json"
    if not cache_path.exists():
        return 0

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        rankings_data = data.get("data", data)
    except (json.JSONDecodeError, OSError):
        return 0

    fetched_at = rankings_data.get("date", "") if isinstance(rankings_data, dict) else data.get("fetched_at", "")
    categories = rankings_data.get("categories", []) if isinstance(rankings_data, dict) else []

    count = 0
    seen = set()
    for cat in categories:
        cat_name = cat.get("name", "")
        books = cat.get("books", [])
        for book in books:
            title = book.get("title", "")
            intro = book.get("intro", "")
            if len(title) < 2:
                continue

            # Deduplicate across categories (first category wins)
            dedup_key = title[:60]
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            theme_text = intro[:300] if intro else f"【{cat_name}】{title}"

            upsert_theme(
                db_path,
                theme=theme_text,
                genre=cat_name,          # Use website's own category
                emotion="",
                platform="番茄小说",
                target_type="long",
                hint_title=title[:80],
                target_words_min=2500,
                target_words_max=3500,
                target_chapters=30,
                audience="女频",
                source="fanqie",
                source_detail=f"FanqieRankTracker/{cat_name}",
                source_url=book.get("url", ""),
                fetched_at=fetched_at,
                raw_json=json.dumps(book, ensure_ascii=False),
            )
            count += 1

    return count


def run_import_all(db_path: str | Path) -> dict[str, Any]:
    """Import themes from FanqieRankTracker only (seed pool deprecated)."""
    fanqie_count = import_from_fanqie_cache(db_path)
    stats = get_theme_stats(db_path)
    return {
        "from_fanqie": fanqie_count,
        "total": stats["total"],
        "unconsumed": stats["unconsumed"],
        "sources": stats["sources"],
    }
