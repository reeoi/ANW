"""SQLite bootstrap utilities for the local novel queue."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config_loader import LoadedConfig


SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def initialize_database(config: LoadedConfig) -> Path:
    """Create the SQLite database and base queue table if needed."""
    db_path = Path(str(config.data.get("database", {}).get("sqlite_path", "data/anp.sqlite3")))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
    return db_path
