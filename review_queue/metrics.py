"""Lightweight metrics collection for the ANW local pipeline.

Stores DeepSeek token usage and pipeline events (generate / review / publish)
in two SQLite tables that live next to the existing ``stories`` table. The
monitoring dashboard queries aggregates from these tables.

All writes are best-effort: failures are logged at WARNING level and never
propagate to the caller, so a metrics outage cannot break the pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    provider TEXT NOT NULL,
    model TEXT,
    purpose TEXT,
    work_type TEXT,
    work_id INTEGER,
    work_title TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cny REAL NOT NULL DEFAULT 0.0,
    duration_seconds REAL,
    first_byte_seconds REAL,
    first_sentence_seconds REAL,
    success INTEGER NOT NULL DEFAULT 1,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_usage_occurred_at ON api_usage(occurred_at);

CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    story_id INTEGER,
    message TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_occurred_at
    ON pipeline_events(occurred_at);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_kind ON pipeline_events(kind);
"""


# Approximate DeepSeek pricing (CNY per 1K tokens). Values are configurable via
# config.yaml -> cost_limits.unit_price_cny.{prompt,completion}. They are only
# used to estimate cost — the actual invoice is authoritative.
DEFAULT_PROMPT_PRICE_CNY_PER_1K = 0.001
DEFAULT_COMPLETION_PRICE_CNY_PER_1K = 0.002


def ensure_metrics_schema(db_path: str | Path) -> None:
    """Create metrics tables if they do not yet exist."""
    try:
        with sqlite3.connect(Path(db_path)) as connection:
            connection.executescript(METRICS_SCHEMA)
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(api_usage)").fetchall()
            }
            additions = {
                "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
                "work_type": "TEXT",
                "work_id": "INTEGER",
                "work_title": "TEXT",
                "duration_seconds": "REAL",
                "first_byte_seconds": "REAL",
                "first_sentence_seconds": "REAL",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE api_usage ADD COLUMN {name} {declaration}")
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("ensure_metrics_schema failed: %s", exc)


def record_api_usage(
    db_path: str | Path,
    *,
    provider: str,
    model: str | None,
    purpose: str,
    work_type: str | None = None,
    work_id: int | None = None,
    work_title: str | None = None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    total_tokens: int | None = None,
    cost_cny: float = 0.0,
    duration_seconds: float | None = None,
    first_byte_seconds: float | None = None,
    first_sentence_seconds: float | None = None,
    success: bool = True,
    error: str | None = None,
) -> None:
    """Record a single LLM call. Errors are swallowed and logged."""
    try:
        ensure_metrics_schema(db_path)
        total = int(total_tokens if total_tokens is not None else (prompt_tokens + completion_tokens))
        with sqlite3.connect(Path(db_path)) as connection:
            connection.execute(
                """
                INSERT INTO api_usage(
                    provider, model, purpose, work_type, work_id, work_title,
                    prompt_tokens, cached_tokens, completion_tokens, total_tokens,
                    cost_cny, duration_seconds, first_byte_seconds,
                    first_sentence_seconds, success, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    model,
                    purpose,
                    work_type,
                    work_id,
                    work_title,
                    int(prompt_tokens),
                    int(cached_tokens),
                    int(completion_tokens),
                    total,
                    float(cost_cny),
                    float(duration_seconds) if duration_seconds is not None else None,
                    float(first_byte_seconds) if first_byte_seconds is not None else None,
                    float(first_sentence_seconds) if first_sentence_seconds is not None else None,
                    1 if success else 0,
                    error,
                ),
            )
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("record_api_usage failed: %s", exc)


def list_api_usage_logs(db_path: str | Path, limit: int = 80) -> list[dict[str, Any]]:
    """Return recent DeepSeek usage rows ordered newest first."""
    ensure_metrics_schema(db_path)
    capped = max(1, min(int(limit or 80), 500))
    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                id,
                occurred_at,
                provider,
                model,
                purpose,
                work_type,
                work_id,
                work_title,
                prompt_tokens,
                cached_tokens,
                completion_tokens,
                total_tokens,
                cost_cny,
                duration_seconds,
                first_byte_seconds,
                first_sentence_seconds,
                success,
                error
            FROM api_usage
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            (capped,),
        ).fetchall()
        sole_long_novel = _sole_long_novel_book(connection)

    items: list[dict[str, Any]] = []
    for row in rows:
        work_type = str(row["work_type"] or "")
        work_id = int(row["work_id"]) if row["work_id"] is not None else None
        work_title = str(row["work_title"] or "")
        association_inferred = False
        if (
            not work_title
            and str(row["purpose"] or "").startswith("long_novel_")
            and sole_long_novel is not None
        ):
            work_type = "long_novel"
            work_id = sole_long_novel["id"]
            work_title = sole_long_novel["title"]
            association_inferred = True

        items.append({
            "id": int(row["id"]),
            "occurred_at": str(row["occurred_at"] or ""),
            "provider": str(row["provider"] or ""),
            "model": str(row["model"] or ""),
            "purpose": str(row["purpose"] or ""),
            "phase": str(row["purpose"] or ""),
            "work_type": work_type,
            "work_id": work_id,
            "work_title": work_title,
            "book_id": work_id if work_type == "long_novel" else None,
            "book_title": work_title if work_type == "long_novel" else "",
            "story_id": work_id if work_type == "short_story" else None,
            "story_title": work_title if work_type == "short_story" else "",
            "association_inferred": association_inferred,
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "input_tokens": int(row["prompt_tokens"] or 0),
            "cached_tokens": int(row["cached_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "output_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "cost_cny": float(row["cost_cny"] or 0.0),
            "duration_seconds": float(row["duration_seconds"]) if row["duration_seconds"] is not None else None,
            "first_byte_seconds": float(row["first_byte_seconds"]) if row["first_byte_seconds"] is not None else None,
            "first_sentence_seconds": float(row["first_sentence_seconds"]) if row["first_sentence_seconds"] is not None else None,
            "success": bool(row["success"]),
            "error": str(row["error"] or ""),
        })
    return items


def _sole_long_novel_book(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the only current long-novel book for legacy usage attribution."""
    try:
        rows = connection.execute("SELECT id, title FROM ln_books ORDER BY id LIMIT 2").fetchall()
    except sqlite3.OperationalError:
        return None
    if len(rows) != 1:
        return None
    return {"id": int(rows[0]["id"]), "title": str(rows[0]["title"] or "")}


def record_pipeline_event(
    db_path: str | Path,
    *,
    kind: str,
    status: str,
    story_id: int | None = None,
    message: str | None = None,
    detail: str | None = None,
) -> None:
    """Record a pipeline event (generate / review / publish / error)."""
    try:
        ensure_metrics_schema(db_path)
        with sqlite3.connect(Path(db_path)) as connection:
            connection.execute(
                """
                INSERT INTO pipeline_events(kind, status, story_id, message, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (kind, status, story_id, message, detail),
            )
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("record_pipeline_event failed: %s", exc)


def estimate_cost_cny(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    prompt_price_per_1k: float = DEFAULT_PROMPT_PRICE_CNY_PER_1K,
    completion_price_per_1k: float = DEFAULT_COMPLETION_PRICE_CNY_PER_1K,
) -> float:
    """Estimate CNY cost from token counts."""
    cost = (prompt_tokens * prompt_price_per_1k + completion_tokens * completion_price_per_1k) / 1000.0
    return round(cost, 4)


def query_overview(db_path: str | Path) -> dict[str, Any]:
    """Return aggregated metrics for the monitoring dashboard."""
    ensure_metrics_schema(db_path)
    now = datetime.now(timezone.utc)

    today_iso = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    week_iso = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    month_iso = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    daily_cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row

        usage_24h = _aggregate_usage(connection, today_iso)
        usage_7d = _aggregate_usage(connection, week_iso)
        usage_30d = _aggregate_usage(connection, month_iso)

        events_24h = _aggregate_events(connection, today_iso)
        events_7d = _aggregate_events(connection, week_iso)

        recent_events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT occurred_at, kind, status, story_id, message
                FROM pipeline_events
                ORDER BY id DESC
                LIMIT 30
                """
            ).fetchall()
        ]

        recent_errors = [
            dict(row)
            for row in connection.execute(
                """
                SELECT occurred_at, kind, status, story_id, message
                FROM pipeline_events
                WHERE status IN ('failed', 'error', 'paused')
                ORDER BY id DESC
                LIMIT 15
                """
            ).fetchall()
        ]

        daily_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    substr(occurred_at, 1, 10) AS day,
                    SUM(total_tokens) AS tokens,
                    SUM(cost_cny) AS cost,
                    COUNT(*) AS calls
                FROM api_usage
                WHERE occurred_at >= ?
                GROUP BY day
                ORDER BY day ASC
                """,
                (daily_cutoff,),
            ).fetchall()
        ]

        story_status = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT status, COUNT(*) FROM stories GROUP BY status"
            ).fetchall()
        }

    return {
        "usage": {"d1": usage_24h, "d7": usage_7d, "d30": usage_30d},
        "events": {"d1": events_24h, "d7": events_7d},
        "recent_events": recent_events,
        "recent_errors": recent_errors,
        "daily_usage": daily_rows,
        "story_status": story_status,
    }


def _aggregate_usage(connection: sqlite3.Connection, since_iso: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS calls,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(cost_cny), 0) AS cost_cny,
            COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) AS failures
        FROM api_usage
        WHERE occurred_at >= ?
        """,
        (since_iso,),
    ).fetchone()
    return {
        "calls": int(row["calls"] or 0),
        "prompt_tokens": int(row["prompt_tokens"] or 0),
        "completion_tokens": int(row["completion_tokens"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
        "cost_cny": round(float(row["cost_cny"] or 0.0), 4),
        "failures": int(row["failures"] or 0),
    }


def _aggregate_events(connection: sqlite3.Connection, since_iso: str) -> dict[str, dict[str, int]]:
    rows = connection.execute(
        """
        SELECT kind, status, COUNT(*) AS count
        FROM pipeline_events
        WHERE occurred_at >= ?
        GROUP BY kind, status
        """,
        (since_iso,),
    ).fetchall()
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        kind = str(row["kind"])
        status = str(row["status"])
        summary.setdefault(kind, {})[status] = int(row["count"])
    return summary


__all__ = [
    "DEFAULT_COMPLETION_PRICE_CNY_PER_1K",
    "DEFAULT_PROMPT_PRICE_CNY_PER_1K",
    "ensure_metrics_schema",
    "estimate_cost_cny",
    "list_api_usage_logs",
    "query_overview",
    "record_api_usage",
    "record_pipeline_event",
]
