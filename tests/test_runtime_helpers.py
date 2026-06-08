"""Tests for runtime_helpers (extracted from scheduler.py during scheduler removal).

Covers configure_logging, backup_sqlite_database, recent_log_lines,
count_stories_by_status, and get_monthly_api_limit.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_helpers
from config_loader import LoadedConfig
from review_queue.db import initialize_database, insert_story
from review_queue.models import Story


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(tmp_path)},
            "deepseek": {"api_key": "", "mock": True},
            "database": {
                "sqlite_path": str(tmp_path / "anw.sqlite3"),
                "backup_dir": str(tmp_path / "backups"),
            },
            "cost_limits": {"monthly_budget_cny": 500},
            "logging": {"level": "INFO", "file": str(tmp_path / "anw.log")},
        },
        path=Path("config.yaml"),
    )


# ============================================================ configure_logging


def test_configure_logging_creates_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log_file = runtime_helpers.configure_logging(config)
    assert log_file == Path(str(tmp_path / "anw.log"))
    assert log_file.parent.exists()


# ============================================================ backup_sqlite_database


def test_backup_sqlite_database_skips_when_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    missing = tmp_path / "missing.sqlite3"
    with patch("runtime_helpers.initialize_database", return_value=missing):
        out = runtime_helpers.backup_sqlite_database(config)
    assert out is None


def test_backup_sqlite_database_copies_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    initialize_database(config)
    out = runtime_helpers.backup_sqlite_database(config)
    assert out is not None
    assert out.exists()
    assert out.parent.name == "backups"


# ============================================================ get_monthly_api_limit


def test_get_monthly_api_limit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert runtime_helpers.get_monthly_api_limit(config) == 500


def test_get_monthly_api_limit_returns_none_when_unset(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.data["cost_limits"] = {}
    assert runtime_helpers.get_monthly_api_limit(config) is None


# ============================================================ recent_log_lines


def test_recent_log_lines_empty_when_file_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log_file, lines = runtime_helpers.recent_log_lines(config)
    assert lines == []
    assert log_file == Path(str(tmp_path / "anw.log"))


def test_recent_log_lines_tail(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log_file = Path(str(tmp_path / "anw.log"))
    log_file.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    _, lines = runtime_helpers.recent_log_lines(config, max_lines=3)
    assert lines == ["c", "d", "e"]


def test_recent_log_lines_returns_timestamped_blocks_newest_first(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log_file = Path(str(tmp_path / "anw.log"))
    log_file.write_text(
        "2026-06-01 10:00:00,000 INFO first\n"
        "2026-06-01 10:00:01,000 ERROR second\n"
        "traceback detail\n"
        "2026-06-01 10:00:02,000 INFO third\n",
        encoding="utf-8",
    )
    _, lines = runtime_helpers.recent_log_lines(config, max_lines=10)
    assert lines == [
        "2026-06-01 10:00:02,000 INFO third",
        "2026-06-01 10:00:01,000 ERROR second",
        "traceback detail",
        "2026-06-01 10:00:00,000 INFO first",
    ]


# ============================================================ count_stories_by_status


def test_count_stories_by_status(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db_path = initialize_database(config)
    for status in ("pending", "pending", "approved"):
        insert_story(db_path, Story(title=f"t-{status}", status=status))
    counts = runtime_helpers.count_stories_by_status(config)
    assert counts.get("pending") == 2
    assert counts.get("approved") == 1
