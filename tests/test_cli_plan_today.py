"""Tests for ``cli.plan_today`` and ``cli.publish --slot-id`` (Phase D)."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli import plan_today as plan_today_cli
from cli import publish as publish_cli
from review_queue.db import (
    get_daily_publish_plan,
    initialize_database,
    insert_story,
    upsert_daily_publish_plan,
)
from review_queue.models import DailyPublishPlan, Story


def _setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ANP_CONFIG / ANP_SQLITE_PATH at a tmp config + DB."""

    db_path = tmp_path / "anp.sqlite3"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
deepseek:
  api_key: ""
runtime:
  mode: "semi-auto"
  dry_run: true
audit:
  approval_threshold: 90
  rewrite_strategy: "phase_4_5_only"
  max_rewrite_attempts: 3
publisher:
  default_platform: "fansq"
  daily_count_min: 2
  daily_count_max: 2
  operating_hours: ["09:00", "22:00"]
  slot_min_gap_minutes: 30
  fansq:
    enabled: true
    min_publish_interval_minutes: 5
    max_publish_interval_minutes: 15
    pause_on_risk_control: true
scheduler:
  enabled: false
  timezone: "Asia/Shanghai"
database:
  sqlite_path: "%s"
logging:
  level: "INFO"
  file: "%s"
""".strip()
        % (str(db_path).replace("\\", "/"), str(tmp_path / "anp.log").replace("\\", "/")),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_CONFIG", str(cfg_path))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    return db_path


def test_plan_today_cli_writes_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_env(tmp_path, monkeypatch)
    rc = plan_today_cli.main(["--date", "2026-05-08"])
    assert rc == 0
    plan = get_daily_publish_plan(db_path, "2026-05-08")
    assert plan is not None
    assert plan.planned_count == 2
    out = capsys.readouterr().out
    assert "date=2026-05-08" in out
    assert "planned_count=2" in out


def test_plan_today_cli_rejects_bad_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_env(tmp_path, monkeypatch)
    rc = plan_today_cli.main(["--date", "not-a-date"])
    assert rc == 2
    err = capsys.readouterr().out
    assert "YYYY-MM-DD" in err


def test_publish_cli_slot_id_with_no_plan_returns_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _setup_env(tmp_path, monkeypatch)
    rc = publish_cli.main(["--dry-run", "--slot-id", "0", "--slot-date", "2026-05-08"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No story claimed for slot_id=0" in out


def test_publish_cli_slot_id_resolves_claimed_story(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_env(tmp_path, monkeypatch)
    initialize_database_path = initialize_database
    # Need a Config to hit DB; reuse plan_today CLI to set up the schema.
    plan_today_cli.main(["--date", "2026-05-08"])

    final_path = tmp_path / "works/9/5_最终稿.md"
    final_path.parent.mkdir(parents=True)
    final_path.write_text("正文", encoding="utf-8")
    sid = insert_story(
        db_path,
        Story(
            title="claim-test",
            status="approved",
            emotion="意难平",
            current_phase="phase_5_done",
            final_content_path=str(final_path),
            target_length=10000,
        ),
    )
    upsert_daily_publish_plan(
        db_path,
        DailyPublishPlan(
            date="2026-05-08",
            planned_count=1,
            slots_json=json.dumps(
                [
                    {
                        "slot_time": "2026-05-08T14:23:00",
                        "story_id": sid,
                        "published_at": None,
                        "skipped_reason": None,
                    }
                ],
                ensure_ascii=False,
            ),
        ),
    )
    rc = publish_cli.main(
        ["--dry-run", "--slot-id", "0", "--slot-date", "2026-05-08"]
    )
    out = capsys.readouterr().out
    assert f"story_id={sid}" in out
    # Dry-run path returns 0 on success / paused.
    assert rc == 0
