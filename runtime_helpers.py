"""Stateless runtime helpers shared by main entrypoint, UI service, and CLI.

These helpers used to live in ``scheduler.py``. The scheduler module was
removed when ANP switched to fully-manual single-shot execution; the helpers
themselves remain useful for logging setup, log tailing, queue counting,
SQLite backup, and reading config-derived numbers.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from config_loader import LoadedConfig
from review_queue.db import initialize_database

logger = logging.getLogger(__name__)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def configure_logging(config: LoadedConfig) -> Path:
    """Configure file + console logging for the UI service and CLI entrypoints."""

    logging_config = config.data.get("logging", {}) if isinstance(config.data.get("logging"), dict) else {}
    log_file = Path(str(logging_config.get("file") or "logs/anp.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level_name = str(logging_config.get("level") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    added = False

    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_file.resolve() for handler in root_logger.handlers):
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)
        added = True

    if not any(getattr(handler, "_anp_console", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        console_handler._anp_console = True  # type: ignore[attr-defined]
        root_logger.addHandler(console_handler)
        added = True

    if added:
        logger.info("Logging initialized: file=%s level=%s", log_file, level_name)
    return log_file


def backup_sqlite_database(config: LoadedConfig) -> Path | None:
    """Copy the configured SQLite database into the configured backup directory."""

    logger.info("Pipeline stage started: sqlite_backup")
    db_path = initialize_database(config)
    if not Path(db_path).exists():
        logger.warning("SQLite backup skipped because database does not exist: %s", db_path)
        return None
    database = _mapping(config.data.get("database"))
    backup_dir = Path(str(database.get("backup_dir") or "data/backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{Path(db_path).stem}_{timestamp}.sqlite3"
    shutil.copy2(db_path, target)
    logger.info("Pipeline stage completed: sqlite_backup source=%s target=%s", db_path, target)
    return target


def get_monthly_api_limit(config: LoadedConfig) -> float | int | None:
    """Expose configured API monthly budget for risk-control checks/monitoring."""

    cost_limits = _mapping(config.data.get("cost_limits"))
    return cost_limits.get("monthly_budget_cny")


def recent_log_lines(config: LoadedConfig, max_lines: int = 80) -> tuple[Path, list[str]]:
    """Return the configured log file path and recent lines for the UI."""

    log_file = Path(str(_mapping(config.data.get("logging")).get("file") or "logs/anp.log"))
    if not log_file.exists():
        return log_file, []
    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()[-max_lines:]
    return log_file, [line.rstrip("\n") for line in lines]


def count_stories_by_status(config: LoadedConfig) -> dict[str, int]:
    """Lightweight monitoring helper for local queue status counts."""

    db_path = initialize_database(config)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT status, COUNT(*) FROM stories GROUP BY status").fetchall()
    return {str(status): int(count) for status, count in rows}


__all__ = [
    "backup_sqlite_database",
    "configure_logging",
    "count_stories_by_status",
    "get_monthly_api_limit",
    "recent_log_lines",
]
