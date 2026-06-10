"""统一 SQLite 连接入口。

所有 storage 子模块经 ``connect()`` 打开连接，让超时 / PRAGMA 等连接级策略
有单一调整点；库路径解析 ``get_database_path`` 同样居于此。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from config_loader import LoadedConfig


def get_database_path(config: LoadedConfig) -> Path:
    """Return the configured SQLite path."""

    return Path(str(config.data.get("database", {}).get("sqlite_path", "data/anw.sqlite3")))


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection to ``db_path`` with project-wide defaults."""

    return sqlite3.connect(Path(db_path))


__all__ = ["connect", "get_database_path"]
