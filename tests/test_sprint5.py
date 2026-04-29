"""Tests for Sprint 5 publishing framework and Fanqie safe pause behavior."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "queue" in sys.modules and not hasattr(sys.modules["queue"], "__path__"):
    del sys.modules["queue"]

from config_loader import LoadedConfig
from publisher.base_publisher import BasePublisher, PublishStatus
from publisher.fansq import FansqPublisher
from queue.db import get_story, initialize_database, insert_story
from queue.models import Story


def _config(tmp_path: Path, dry_run: bool = True) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": dry_run},
            "database": {"sqlite_path": str(tmp_path / "publish.sqlite3")},
            "logging": {
                "file": str(tmp_path / "logs" / "anp.log"),
                "screenshot_dir": str(tmp_path / "logs" / "screenshots"),
            },
            "publisher": {
                "fansq": {
                    "enabled": True,
                    "username": "configured-user",
                    "login_state_path": str(tmp_path / "browser" / "fansq_state.json"),
                    "draft_url": "https://example.invalid/draft",
                    "pause_on_risk_control": True,
                }
            },
        },
        path=tmp_path / "config.yaml",
    )


def test_base_publisher_pause_records_log_and_screenshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    publisher = BasePublisher(config, platform_name="test")

    result = publisher.pause_for_human("验证码/滑块需要人工处理", story_id=7, wait=False)

    assert result.status == PublishStatus.PAUSED
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).exists()
    log_text = (tmp_path / "logs" / "anp.log").read_text(encoding="utf-8")
    assert "验证码/滑块需要人工处理" in log_text
    assert "story_id=7" in log_text


def test_fansq_dry_run_can_simulate_success_and_pause(tmp_path: Path) -> None:
    config = _config(tmp_path)
    publisher = FansqPublisher(config)

    success = publisher.publish_story(Story(id=1, title="可发布", content="正文", status="approved"), dry_run=True)
    paused = publisher.publish_story(
        Story(id=2, title="需人工", content="正文", status="approved"),
        dry_run=True,
        dry_run_outcome="paused",
    )

    assert success.status == PublishStatus.PUBLISHED
    assert "dry-run" in success.message.lower()
    assert paused.status == PublishStatus.PAUSED
    assert paused.screenshot_path is not None
    assert Path(paused.screenshot_path).exists()


def test_fansq_real_mode_missing_login_state_pauses_without_bypass(tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    publisher = FansqPublisher(config)

    result = publisher.publish_story(Story(id=3, title="标题", content="正文", status="approved"), dry_run=False)

    assert result.status == PublishStatus.PAUSED
    assert "登录态" in result.message
    assert result.screenshot_path is not None
    assert Path(result.screenshot_path).exists()
    log_text = (tmp_path / "logs" / "anp.log").read_text(encoding="utf-8")
    assert "不尝试绕过" in log_text


def test_publish_cli_reads_one_approved_record_and_preserves_status_by_default(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    db_path = initialize_database(config)
    story_id = insert_story(db_path, Story(title="待发布", content="正文", status="approved"))

    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ANP_DRY_RUN", "true")
    monkeypatch.setenv("FANSQ_LOGIN_STATE_PATH", str(tmp_path / "browser" / "fansq_state.json"))

    completed = subprocess.run(
        [sys.executable, "-m", "cli.publish", "--dry-run", "--dry-run-outcome", "success"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "story_id=" in completed.stdout
    assert "published" in completed.stdout
    assert get_story(db_path, story_id).status == "approved"


def test_publish_cli_can_commit_dry_run_pause_status(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    db_path = initialize_database(config)
    story_id = insert_story(db_path, Story(title="待发布", content="正文", status="approved"))

    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ANP_DRY_RUN", "true")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.publish",
            "--dry-run",
            "--dry-run-outcome",
            "paused",
            "--commit-dry-run",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "publish_paused" in completed.stdout
    assert get_story(db_path, story_id).status == "publish_paused"
