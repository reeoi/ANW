"""SQLite schema：建表、迁移与 schema_version。

c_pipeline 三张核心表（docs/c_pipeline_plan.md §3.2）+ phase_transitions +
pending_human_input + 遥测两张表（api_usage / pipeline_events），以及记录
结构版本的 ``schema_version`` 表。所有建表 / 迁移逻辑集中于此，CRUD 分别在
``storage.stories`` 与 ``storage.usage``。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from config_loader import LoadedConfig
from storage.connection import connect, get_database_path

logger = logging.getLogger(__name__)

# 结构版本：每次不兼容的 schema 变更 +1，并在 initialize_database 里补迁移。
# 1 = storage 包抽取时的基线（stories/daily_publish_plan/pipeline_cost_log/
#     phase_transitions/pending_human_input + api_usage/pipeline_events）。
SCHEMA_VERSION = 1

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
    cancel_requested INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS phase_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL,
    phase TEXT NOT NULL,
    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(story_id) REFERENCES stories(id)
);

CREATE INDEX IF NOT EXISTS idx_phase_transitions_story_id ON phase_transitions(story_id, id);
"""

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


def initialize_database(config: LoadedConfig) -> Path:
    """Create the SQLite database and the c_pipeline schema if missing."""

    db_path = get_database_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as connection:
        # WAL 持久化在库文件里：读不再阻塞写。本应用是「多 daemon 写线程 +
        # 线程池」并发写同一个库，默认 rollback journal 会让读写互相卡锁。
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)
        _migrate_add_cancel_requested(connection)
        _record_schema_version(connection)
    # ensure_metrics_schema 自吞 sqlite3.Error（遥测损坏不能拦截创作主链路）。
    ensure_metrics_schema(db_path)
    return db_path


def ensure_metrics_schema(db_path: str | Path) -> None:
    """Create metrics tables if they do not yet exist."""
    try:
        with connect(db_path) as connection:
            connection.executescript(METRICS_SCHEMA)
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(api_usage)").fetchall()}
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


def get_schema_version(db_path: str | Path) -> int:
    """Return the recorded schema version, or 0 for pre-storage legacy databases."""

    with connect(db_path) as connection:
        try:
            row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0] or 0) if row is not None else 0


def _record_schema_version(connection: sqlite3.Connection) -> None:
    """Stamp ``SCHEMA_VERSION`` into the schema_version table (idempotent)."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )


def _migrate_add_cancel_requested(connection: sqlite3.Connection) -> None:
    """Add ``cancel_requested`` column to legacy stories tables (idempotent)."""

    cols = {row[1] for row in connection.execute("PRAGMA table_info(stories)").fetchall()}
    if "cancel_requested" not in cols:
        connection.execute("ALTER TABLE stories ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
    if "preset_name" not in cols:
        connection.execute("ALTER TABLE stories ADD COLUMN preset_name TEXT NOT NULL DEFAULT ''")
    # Phase 6: pending_human_input table
    connection.execute("""
        CREATE TABLE IF NOT EXISTS pending_human_input (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            prompt TEXT NOT NULL DEFAULT '',
            input_schema TEXT NOT NULL DEFAULT '{}',
            payload_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            FOREIGN KEY(story_id) REFERENCES stories(id)
        )
    """)


def _migrate_add_cost_log_story_title(connection: sqlite3.Connection) -> None:
    """Legacy no-op kept for compatibility with older imports."""

    return None


__all__ = [
    "METRICS_SCHEMA",
    "SCHEMA",
    "SCHEMA_VERSION",
    "ensure_metrics_schema",
    "get_schema_version",
    "initialize_database",
]
