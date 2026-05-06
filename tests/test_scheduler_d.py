"""Tests for scheduler.py Phase D wiring (PLAN §7 Phase D, decisions #17-#21).

Covers:

- ``create_scheduler`` registers ``weekly_scan`` / ``plan_today`` /
  ``sqlite_backup`` cron jobs (and the existing ``ai_review`` job).
- ``publish_cron`` is empty and no longer produces a job.
- 7-day plan_today simulation always produces 0..5 slots inside operating
  hours with gap >= 30 minutes.
- ``scheduled_slot_trigger`` returns False without raising when no
  approved story exists, and persists ``skipped_reason``.
- ``scheduled_slot_trigger`` claims FIFO + cross-day-emotion-balanced
  candidate, marks ``slots_json[i].story_id``, and skips already-claimed
  stories on neighbouring slots.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue.db import (
    get_daily_publish_plan,
    initialize_database,
    insert_story,
    upsert_daily_publish_plan,
)
from review_queue.models import DailyPublishPlan, Story
import scheduler as scheduler_module
from scheduler import (
    create_scheduler,
    register_publish_slots,
    scheduled_plan_today,
    scheduled_slot_trigger,
)
from scheduler_planner import plan_today_publishes


# ============================================================ helpers


def _config(tmp_path: Path, **overrides: Any) -> LoadedConfig:
    publisher = {
        "default_platform": "fansq",
        "daily_count_min": 0,
        "daily_count_max": 5,
        "operating_hours": ["09:00", "22:00"],
        "slot_min_gap_minutes": 30,
        "fansq": {
            "enabled": True,
            "min_publish_interval_minutes": 5,
            "max_publish_interval_minutes": 15,
            "pause_on_risk_control": True,
        },
    }
    publisher.update(overrides.get("publisher", {}))
    scheduler_cfg = {
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "weekly_scan_cron": "0 3 * * 1",
        "plan_today_cron": "0 3 * * *",
        "generate_cron": "",
        "review_cron": "30 9 * * *",
        "publish_cron": "",
        "backup_cron": "0 4 * * *",
    }
    scheduler_cfg.update(overrides.get("scheduler", {}))
    data: dict[str, Any] = {
        "database": {"sqlite_path": str(tmp_path / "sched.sqlite3")},
        "publisher": publisher,
        "scheduler": scheduler_cfg,
        "audit": {"approval_threshold": 90, "batch_limit": 20},
        "deepseek": {"mock": True, "api_key": ""},
        "runtime": {"dry_run": True, "mode": "auto"},
        "cost_limits": {"monthly_budget_cny": 100.0},
    }
    return LoadedConfig(data=data, path=Path("sched.yaml"))


class _RecordingScheduler:
    """Minimal stand-in capturing add_job calls without starting APScheduler."""

    def __init__(self, timezone: str = "Asia/Shanghai") -> None:
        self.timezone = timezone
        self.jobs: list[dict[str, Any]] = []

    def add_job(self, func, trigger=None, **kwargs: Any) -> None:
        self.jobs.append(
            {
                "func": func,
                "trigger": trigger,
                "id": kwargs.get("id"),
                "name": kwargs.get("name"),
                "args": list(kwargs.get("args") or []),
                "kwargs": dict(kwargs.get("kwargs") or {}),
            }
        )


def _make_approved_story(
    db_path: Path,
    *,
    title: str,
    emotion: str,
    created_at: str | None = None,
) -> int:
    sid = insert_story(
        db_path,
        Story(
            title=title,
            status="approved",
            emotion=emotion,
            current_phase="phase_5_done",
            final_content_path=str(db_path.parent / f"works/{title}/5_最终稿.md"),
            target_length=10000,
        ),
    )
    if created_at is not None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE stories SET created_at = ? WHERE id = ?",
                (created_at, sid),
            )
    return sid


# ============================================================ create_scheduler


def test_create_scheduler_registers_three_phase_d_cron_jobs(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    scheduler = create_scheduler(cfg)
    try:
        ids = {job.id for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False) if scheduler.running else None
    assert "weekly_scan" in ids
    assert "plan_today" in ids
    assert "sqlite_backup" in ids


def test_create_scheduler_no_publish_cron_when_empty(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    scheduler = create_scheduler(cfg)
    try:
        ids = {job.id for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False) if scheduler.running else None
    assert "publish_window" not in ids
    assert "generate_story" not in ids
    # ai_review is still registered because review_cron is non-empty.
    assert "ai_review" in ids


def test_create_scheduler_omits_legacy_generate_cron_even_if_set(tmp_path: Path) -> None:
    """Decision L1/L2: setting generate_cron must NOT register a generate job."""

    cfg = _config(tmp_path, scheduler={"generate_cron": "0 6 * * *"})
    scheduler = create_scheduler(cfg)
    try:
        ids = {job.id for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False) if scheduler.running else None
    assert "generate_story" not in ids


def test_create_scheduler_omits_publish_cron_even_if_set(tmp_path: Path) -> None:
    """Decision L1/L2: setting publish_cron must NOT register the legacy job."""

    cfg = _config(tmp_path, scheduler={"publish_cron": "0 12 * * *"})
    scheduler = create_scheduler(cfg)
    try:
        ids = {job.id for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False) if scheduler.running else None
    assert "publish_window" not in ids


# ============================================================ 7-day simulation


def test_seven_day_plan_simulation_always_legal(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    today = date(2026, 5, 1)
    seen_counts: set[int] = set()
    for offset in range(7):
        plan_date = today + timedelta(days=offset)
        plan = plan_today_publishes(cfg, today=plan_date, rng=random.Random(offset))
        assert 0 <= plan.planned_count <= 5
        seen_counts.add(plan.planned_count)
        slots = json.loads(plan.slots_json)
        start = datetime.combine(plan_date, time(9, 0))
        end = datetime.combine(plan_date, time(22, 0))
        previous = None
        for slot in slots:
            slot_dt = datetime.fromisoformat(slot["slot_time"])
            assert start <= slot_dt <= end
            if previous is not None:
                assert (slot_dt - previous).total_seconds() >= 30 * 60
            previous = slot_dt
    # 7 days of seeds should hit at least three different planned_counts.
    assert len(seen_counts) >= 2


# ============================================================ scheduled_slot_trigger empty


def test_scheduled_slot_trigger_returns_false_when_no_approved_story(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    today = date(2026, 5, 1)
    today_str = today.isoformat()
    plan = DailyPublishPlan(
        date=today_str,
        planned_count=1,
        slots_json=json.dumps(
            [
                {
                    "slot_time": f"{today_str}T14:23:00",
                    "story_id": None,
                    "published_at": None,
                    "skipped_reason": None,
                }
            ],
            ensure_ascii=False,
        ),
    )
    upsert_daily_publish_plan(db_path, plan)

    sched = _RecordingScheduler()
    fired = scheduled_slot_trigger(sched, cfg, slot_index=0, today=today_str)
    assert fired is False

    fetched = get_daily_publish_plan(db_path, today_str)
    assert fetched is not None
    payload = json.loads(fetched.slots_json)
    assert payload[0]["skipped_reason"] == "no_approved_story"
    assert payload[0]["story_id"] is None
    # No follow-up publish attempt scheduled.
    assert sched.jobs == []


# ============================================================ scheduled_slot_trigger picks FIFO


def test_scheduled_slot_trigger_picks_fifo_and_marks_story_id(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    today = date(2026, 5, 1)
    today_str = today.isoformat()
    plan = DailyPublishPlan(
        date=today_str,
        planned_count=2,
        slots_json=json.dumps(
            [
                {
                    "slot_time": f"{today_str}T10:00:00",
                    "story_id": None,
                    "published_at": None,
                    "skipped_reason": None,
                },
                {
                    "slot_time": f"{today_str}T15:00:00",
                    "story_id": None,
                    "published_at": None,
                    "skipped_reason": None,
                },
            ],
            ensure_ascii=False,
        ),
    )
    upsert_daily_publish_plan(db_path, plan)
    early_id = _make_approved_story(
        db_path,
        title="早 FIFO 故事",
        emotion="shuang_gan_shi_fang",
        created_at="2026-04-30 10:00:00",
    )
    late_id = _make_approved_story(
        db_path,
        title="晚 FIFO 故事",
        emotion="shuang_gan_shi_fang",
        created_at="2026-04-30 18:00:00",
    )
    assert early_id < late_id  # sanity

    sched = _RecordingScheduler()
    fired = scheduled_slot_trigger(sched, cfg, slot_index=0, today=today_str)
    assert fired is True

    fetched = get_daily_publish_plan(db_path, today_str)
    assert fetched is not None
    payload = json.loads(fetched.slots_json)
    assert payload[0]["story_id"] == early_id
    assert payload[1]["story_id"] is None  # other slot untouched

    # The follow-up publish DateTrigger was added to the scheduler.
    assert len(sched.jobs) == 1
    assert sched.jobs[0]["kwargs"].get("slot_index") == 0
    assert sched.jobs[0]["kwargs"].get("today") == today_str


def test_scheduled_slot_trigger_excludes_already_claimed_story(tmp_path: Path) -> None:
    """Decision #19: each approved story is consumed by at most one slot."""

    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    today = date(2026, 5, 1)
    today_str = today.isoformat()
    early_id = _make_approved_story(
        db_path,
        title="A",
        emotion="意难平",
        created_at="2026-04-30 09:00:00",
    )
    late_id = _make_approved_story(
        db_path,
        title="B",
        emotion="意难平",
        created_at="2026-04-30 12:00:00",
    )
    plan = DailyPublishPlan(
        date=today_str,
        planned_count=2,
        slots_json=json.dumps(
            [
                {
                    "slot_time": f"{today_str}T10:00:00",
                    "story_id": early_id,
                    "published_at": None,
                    "skipped_reason": None,
                },
                {
                    "slot_time": f"{today_str}T15:00:00",
                    "story_id": None,
                    "published_at": None,
                    "skipped_reason": None,
                },
            ],
            ensure_ascii=False,
        ),
    )
    upsert_daily_publish_plan(db_path, plan)
    sched = _RecordingScheduler()
    fired = scheduled_slot_trigger(sched, cfg, slot_index=1, today=today_str)
    assert fired is True
    fetched = get_daily_publish_plan(db_path, today_str)
    assert fetched is not None
    payload = json.loads(fetched.slots_json)
    assert payload[0]["story_id"] == early_id
    # Slot 1 must not pick the same story; only late_id remains.
    assert payload[1]["story_id"] == late_id


def test_scheduled_plan_today_writes_and_registers_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path, publisher={"daily_count_min": 3, "daily_count_max": 3})
    today = date(2026, 5, 1)
    today_str = today.isoformat()
    sched = _RecordingScheduler()
    db_path = initialize_database(cfg)
    # Force the slot times into the future (use tomorrow's date) so register_publish_slots
    # registers all of them.
    future = today + timedelta(days=400)
    monkeypatch.setattr(scheduler_module, "date", _FrozenDate(future))

    registered = scheduled_plan_today(sched, cfg, today=future)
    assert registered == 3
    plan = get_daily_publish_plan(db_path, future.isoformat())
    assert plan is not None
    assert plan.planned_count == 3
    assert len(sched.jobs) == 3
    for job in sched.jobs:
        assert job["id"].startswith(f"publish_slot_{future.isoformat()}_")
        assert job["kwargs"]["today"] == future.isoformat()
        assert isinstance(job["kwargs"]["slot_index"], int)


def test_register_publish_slots_skips_past_and_already_fired(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    today = date.today()
    today_str = today.isoformat()
    past_dt = (datetime.now() - timedelta(hours=2)).replace(microsecond=0).isoformat()
    future_dt = (datetime.now() + timedelta(hours=2)).replace(microsecond=0).isoformat()
    plan = DailyPublishPlan(
        date=today_str,
        planned_count=4,
        slots_json=json.dumps(
            [
                {"slot_time": past_dt, "story_id": None, "published_at": None, "skipped_reason": None},
                {"slot_time": future_dt, "story_id": 99, "published_at": None, "skipped_reason": None},
                {"slot_time": future_dt, "story_id": None, "published_at": "x", "skipped_reason": None},
                {"slot_time": future_dt, "story_id": None, "published_at": None, "skipped_reason": None},
            ],
            ensure_ascii=False,
        ),
    )
    upsert_daily_publish_plan(db_path, plan)
    sched = _RecordingScheduler()
    registered = register_publish_slots(sched, cfg, today=today)
    # Only the last (unfired, future) slot is eligible.
    assert registered == 1
    assert len(sched.jobs) == 1
    assert sched.jobs[0]["kwargs"]["slot_index"] == 3


class _FrozenDate:
    """Stand-in for ``date`` exposing ``today()`` -> a fixed value."""

    def __init__(self, today_value: date) -> None:
        self._today = today_value

    def today(self) -> date:  # pragma: no cover - trivial
        return self._today

    def fromisoformat(self, raw: str) -> date:  # pragma: no cover
        return date.fromisoformat(raw)
