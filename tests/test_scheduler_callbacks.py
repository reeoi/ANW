"""Phase H.1 — coverage tests for scheduler.py callbacks.

Targets the cron callback bodies that were uncovered by Phase D tests:
- scheduled_weekly_scan (success / blocked / generic-failure paths)
- scheduled_ai_review (failed > 0 / needs_human == reviewed branches)
- scheduled_publish (no approved story / risk-control / failed branches)
- backup_sqlite_database (db missing + happy path)
- run_dry_run_pipeline
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scheduler as sched
from config_loader import LoadedConfig
from publisher.base_publisher import PublishResult, PublishStatus
from review_queue.db import initialize_database, insert_story
from review_queue.models import Story
from scan import WeeklyScanBlockedError


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(tmp_path)},
            "deepseek": {"api_key": "", "mock": True},
            "database": {
                "sqlite_path": str(tmp_path / "anp.sqlite3"),
                "backup_dir": str(tmp_path / "backups"),
            },
            "audit": {"approval_threshold": 90, "batch_limit": 5},
            "publisher": {
                "default_platform": "fansq",
                "daily_count_min": 0,
                "daily_count_max": 5,
                "operating_hours": ["09:00", "22:00"],
                "slot_min_gap_minutes": 30,
                "fansq": {"enabled": True},
            },
            "scheduler": {"enabled": False, "timezone": "Asia/Shanghai"},
            "logging": {"level": "INFO", "file": str(tmp_path / "anp.log")},
        },
        path=Path("config.yaml"),
    )


# ============================================================ weekly_scan


def test_scheduled_weekly_scan_success(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    fake_result = SimpleNamespace(
        iso_week="2026W19", item_count=100, used_fallback=False,
    )
    monkeypatch.setattr(sched, "run_weekly_scan", lambda cfg: fake_result)
    monkeypatch.setattr(sched.bus, "publish", lambda *a, **k: None)
    out = sched.scheduled_weekly_scan(config)
    assert out is fake_result


def test_scheduled_weekly_scan_blocked_returns_none(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)

    def boom(cfg):
        raise WeeklyScanBlockedError("no fallback")

    monkeypatch.setattr(sched, "run_weekly_scan", boom)
    monkeypatch.setattr(sched.bus, "publish", lambda *a, **k: None)
    assert sched.scheduled_weekly_scan(config) is None


def test_scheduled_weekly_scan_used_fallback_branch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    fake_result = SimpleNamespace(
        iso_week="2026W19", item_count=100, used_fallback=True,
    )
    monkeypatch.setattr(sched, "run_weekly_scan", lambda cfg: fake_result)
    captured: list[str] = []
    monkeypatch.setattr(
        sched.bus,
        "publish",
        lambda sev, title, msg, **k: captured.append(title),
    )
    sched.scheduled_weekly_scan(config)
    assert any("回退" in t for t in captured)


# ============================================================ ai_review


def test_scheduled_ai_review_with_failed(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    initialize_database(config)
    fake_result = SimpleNamespace(reviewed=3, approved=1, needs_human=1, failed=1)
    monkeypatch.setattr(sched, "run_review_batch", lambda *a, **k: fake_result)
    captured: list[str] = []
    monkeypatch.setattr(
        sched.bus,
        "publish",
        lambda sev, title, msg, **k: captured.append(title),
    )
    out = sched.scheduled_ai_review(config)
    assert out is fake_result
    assert any("失败" in t for t in captured)


def test_scheduled_ai_review_all_needs_human(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    initialize_database(config)
    fake_result = SimpleNamespace(reviewed=2, approved=0, needs_human=2, failed=0)
    monkeypatch.setattr(sched, "run_review_batch", lambda *a, **k: fake_result)
    captured: list[str] = []
    monkeypatch.setattr(
        sched.bus,
        "publish",
        lambda sev, title, msg, **k: captured.append(title),
    )
    sched.scheduled_ai_review(config)
    assert any("需人工" in t for t in captured)


def test_scheduled_ai_review_normal(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    initialize_database(config)
    fake_result = SimpleNamespace(reviewed=2, approved=1, needs_human=1, failed=0)
    monkeypatch.setattr(sched, "run_review_batch", lambda *a, **k: fake_result)
    monkeypatch.setattr(sched.bus, "publish", lambda *a, **k: None)
    out = sched.scheduled_ai_review(config)
    assert out is fake_result


# ============================================================ publish


def test_scheduled_publish_no_approved(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    initialize_database(config)
    monkeypatch.setattr(sched.bus, "publish", lambda *a, **k: None)
    assert sched.scheduled_publish(config) is False


def test_scheduled_publish_published(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    db_path = initialize_database(config)
    sid = insert_story(db_path, Story(title="待发布", status="approved", work_dir=""))

    fake_result = PublishResult(
        story_id=sid,
        platform="fansq",
        status=PublishStatus.PUBLISHED,
        message="published",
    )

    fake_publisher = MagicMock()
    fake_publisher.publish_story.return_value = fake_result
    monkeypatch.setattr(sched, "FansqPublisher", lambda cfg: fake_publisher, raising=False)

    # Patch the import inside scheduled_publish:
    with patch("publisher.fansq.FansqPublisher", return_value=fake_publisher):
        with patch("cli.publish.apply_publish_result", return_value=True):
            monkeypatch.setattr(sched.bus, "publish", lambda *a, **k: None)
            ok = sched.scheduled_publish(config)
    assert ok is True


def test_scheduled_publish_paused_branch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    db_path = initialize_database(config)
    sid = insert_story(db_path, Story(title="待发布", status="approved", work_dir=""))

    fake_result = PublishResult(
        story_id=sid, platform="fansq", status=PublishStatus.PAUSED, message="captcha"
    )
    fake_publisher = MagicMock()
    fake_publisher.publish_story.return_value = fake_result
    captured: list[str] = []
    monkeypatch.setattr(
        sched.bus, "publish",
        lambda sev, title, msg, **k: captured.append(title),
    )
    with patch("publisher.fansq.FansqPublisher", return_value=fake_publisher):
        with patch("cli.publish.apply_publish_result", return_value=True):
            ok = sched.scheduled_publish(config)
    assert ok is False
    assert any("暂停" in t for t in captured)


def test_scheduled_publish_failed_branch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    db_path = initialize_database(config)
    sid = insert_story(db_path, Story(title="待发布", status="approved", work_dir=""))

    fake_result = PublishResult(
        story_id=sid, platform="fansq", status=PublishStatus.FAILED, message="500"
    )
    fake_publisher = MagicMock()
    fake_publisher.publish_story.return_value = fake_result
    captured: list[str] = []
    monkeypatch.setattr(
        sched.bus, "publish",
        lambda sev, title, msg, **k: captured.append(title),
    )
    with patch("publisher.fansq.FansqPublisher", return_value=fake_publisher):
        with patch("cli.publish.apply_publish_result", return_value=True):
            ok = sched.scheduled_publish(config)
    assert ok is False
    assert any("失败" in t for t in captured)


# ============================================================ backup


def test_backup_sqlite_database_skips_when_missing(tmp_path: Path) -> None:
    """The backup function calls initialize_database, which (re)creates the
    file. To exercise the missing-file branch we patch initialize_database to
    point at a non-existent path."""
    config = _config(tmp_path)
    missing = tmp_path / "missing.sqlite3"
    with patch("scheduler.initialize_database", return_value=missing):
        out = sched.backup_sqlite_database(config)
    assert out is None


def test_backup_sqlite_database_copies_file(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    initialize_database(config)
    monkeypatch.setattr(sched.bus, "publish", lambda *a, **k: None)
    out = sched.backup_sqlite_database(config)
    assert out is not None
    assert out.exists()
    assert out.parent.name == "backups"


# ============================================================ run_dry_run_pipeline


def test_run_dry_run_pipeline(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    initialize_database(config)
    monkeypatch.setattr(
        sched, "scheduled_ai_review",
        lambda cfg: SimpleNamespace(reviewed=2, approved=1, needs_human=1, failed=0),
    )
    monkeypatch.setattr(sched, "scheduled_publish", lambda cfg: True)
    out = sched.run_dry_run_pipeline(config)
    assert out.reviewed == 2
    assert out.published is True
