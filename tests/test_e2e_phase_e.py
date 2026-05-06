"""Phase E end-to-end dry-run integration test.

Exercises the full chain in mock/dry-run mode (no real DeepSeek, no
real number Tomato browser):

    seed approved-able c_pipeline story
        -> AI review (mock approving) -> status='approved'
        -> upsert daily_publish_plan with one slot
        -> scheduled_slot_trigger (claims story_id, schedules publish)
        -> scheduled_publish in dry-run with commit_dry_run=True
        -> mark_slot_published, status='published'

This test verifies the wiring between Phase D (slot scheduler) and
Phase E (AI review + publisher) without touching networks.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from publisher.base_publisher import PublishStatus
from review_queue import ai_review as ai_review_module
from review_queue.ai_review import DIMENSIONS, ReviewResult, run_review_batch
from review_queue.db import (
    get_daily_publish_plan,
    get_story,
    initialize_database,
    insert_story,
    upsert_daily_publish_plan,
)
from review_queue.models import DailyPublishPlan, Story
from scheduler import scheduled_publish, scheduled_slot_trigger


def _config(tmp_path: Path) -> LoadedConfig:
    state_dir = tmp_path / "browser"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "fansq_state.json"
    state_path.write_text("{}", encoding="utf-8")
    return LoadedConfig(
        data={
            "deepseek": {"mock": True, "api_key": ""},
            "runtime": {"dry_run": True, "headless": True, "project_root": str(tmp_path)},
            "audit": {
                "approval_threshold": 90,
                "max_rewrite_attempts": 3,
                "rewrite_strategy": "phase_4_5_only",
            },
            "publisher": {
                "default_platform": "fansq",
                "daily_count_min": 0,
                "daily_count_max": 5,
                "operating_hours": ["09:00", "22:00"],
                "slot_min_gap_minutes": 30,
                "commit_dry_run": True,
                "fansq": {
                    "enabled": True,
                    "username": "test",
                    "login_state_path": str(state_path),
                    "draft_url": "https://fanqienovel.com/",
                    "min_publish_interval_minutes": 5,
                    "max_publish_interval_minutes": 15,
                    "pause_on_risk_control": True,
                },
            },
            "scheduler": {
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "weekly_scan_cron": "0 3 * * 1",
                "plan_today_cron": "0 3 * * *",
                "review_cron": "30 9 * * *",
                "publish_cron": "",
                "backup_cron": "0 4 * * *",
            },
            "database": {"sqlite_path": str(tmp_path / "anp.sqlite3")},
            "logging": {
                "level": "INFO",
                "file": str(tmp_path / "anp.log"),
                "screenshot_dir": str(tmp_path / "screens"),
            },
            "cost_limits": {"monthly_budget_cny": 100.0},
        },
        path=Path("e2e.yaml"),
    )


class _RecordingScheduler:
    """Minimal stand-in capturing add_job calls without starting APScheduler."""

    timezone = "Asia/Shanghai"

    def __init__(self) -> None:
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


def _seed_post_phase_5_story(tmp_path: Path, db_path: Path) -> int:
    """Simulate a generate run that just finished Phase 5."""

    work_dir = tmp_path / "data" / "works" / "1"
    work_dir.mkdir(parents=True, exist_ok=True)
    final_path = work_dir / "5_最终稿.md"
    final_path.write_text("正文段落，故事内容。" * 600, encoding="utf-8")
    return insert_story(
        db_path,
        Story(
            title="Phase 1 final_title 示例",
            status="pending",
            current_phase="phase_5_done",
            work_dir=str(work_dir),
            final_content_path=str(final_path),
            summary="情境设定 → 冲突 → 反转 → 余韵收尾，约 200 字简介。" * 4,
            emotion="意难平",
            target_length=10000,
        ),
    )


def test_end_to_end_dry_run_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)

    # ---------- 1. Seed a "post-phase-5" story (simulates cli/generate) ----------
    sid = _seed_post_phase_5_story(tmp_path, db_path)
    pre = get_story(db_path, sid)
    assert pre is not None
    assert pre.status == "pending"

    # ---------- 2. AI review approves on first pass ----------
    approving = ReviewResult(
        total_score=95,
        dimension_scores={k: 90 for k in DIMENSIONS},
        issues=[],
        suggestions=[],
        decision="approved",
    )
    monkeypatch.setattr(
        ai_review_module,
        "review_story",
        lambda story, config=None, settings=None: approving,
    )

    review_result = run_review_batch(db_path, threshold=90, limit=10, config=cfg)
    assert review_result.reviewed == 1
    assert review_result.approved == 1
    assert review_result.needs_human == 0

    after_review = get_story(db_path, sid)
    assert after_review is not None
    assert after_review.status == "approved"
    assert after_review.ai_review_score == 95

    # ---------- 3. Plan today's slot manually ----------
    today = date.today()
    today_str = today.isoformat()
    upsert_daily_publish_plan(
        db_path,
        DailyPublishPlan(
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
        ),
    )

    # ---------- 4. Slot trigger claims the story ----------
    sched = _RecordingScheduler()
    fired = scheduled_slot_trigger(sched, cfg, slot_index=0, today=today_str)
    assert fired is True

    plan = get_daily_publish_plan(db_path, today_str)
    assert plan is not None
    slots_after_claim = json.loads(plan.slots_json)
    assert slots_after_claim[0]["story_id"] == sid

    # The slot trigger schedules a randomized DateTrigger 5-15 min later.
    assert len(sched.jobs) == 1
    delayed_job = sched.jobs[0]
    assert delayed_job["kwargs"]["slot_index"] == 0
    assert delayed_job["kwargs"]["today"] == today_str
    assert delayed_job["kwargs"]["story"] is not None
    assert delayed_job["kwargs"]["story"].id == sid

    # ---------- 5. Run scheduled_publish (dry-run, commit_dry_run=True) ----------
    story_to_publish = delayed_job["kwargs"]["story"]
    published = scheduled_publish(
        cfg,
        story=story_to_publish,
        slot_index=0,
        today=today_str,
    )
    assert published is True

    # ---------- 6. Assert end state ----------
    plan_final = get_daily_publish_plan(db_path, today_str)
    assert plan_final is not None
    final_slots = json.loads(plan_final.slots_json)
    assert final_slots[0]["story_id"] == sid
    assert final_slots[0]["published_at"] is not None
    assert final_slots[0]["skipped_reason"] is None

    final_story = get_story(db_path, sid)
    assert final_story is not None
    assert final_story.status == str(PublishStatus.PUBLISHED)


def test_end_to_end_empty_queue_marks_slot_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When AI review never approves anything, slot fires with no story to claim."""

    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)

    today = date.today()
    today_str = today.isoformat()
    upsert_daily_publish_plan(
        db_path,
        DailyPublishPlan(
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
        ),
    )

    sched = _RecordingScheduler()
    fired = scheduled_slot_trigger(sched, cfg, slot_index=0, today=today_str)
    assert fired is False
    assert sched.jobs == []

    plan = get_daily_publish_plan(db_path, today_str)
    assert plan is not None
    slots = json.loads(plan.slots_json)
    assert slots[0]["story_id"] is None
    assert slots[0]["skipped_reason"] == "no_approved_story"


def test_end_to_end_with_r2_rewrite_then_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full chain when first review fails and one R2 rerun rescues approval."""

    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    sid = _seed_post_phase_5_story(tmp_path, db_path)

    rerun_calls: list[int] = []
    monkeypatch.setattr(
        ai_review_module,
        "_do_rewrite_phase_4_5",
        lambda story_id, *, config=None: rerun_calls.append(story_id),
    )
    decisions = iter(
        [
            ReviewResult(
                total_score=72,
                dimension_scores={k: 72 for k in DIMENSIONS},
                issues=["反转不足"],
                suggestions=["提升钩子"],
                decision="rewrite",
            ),
            ReviewResult(
                total_score=92,
                dimension_scores={k: 90 for k in DIMENSIONS},
                issues=[],
                suggestions=[],
                decision="approved",
            ),
        ]
    )
    monkeypatch.setattr(
        ai_review_module,
        "review_story",
        lambda story, config=None, settings=None: next(decisions),
    )

    result = run_review_batch(db_path, threshold=90, limit=10, config=cfg)
    assert result.approved == 1
    assert rerun_calls == [sid]  # one rewrite

    fetched = get_story(db_path, sid)
    assert fetched is not None
    assert fetched.status == "approved"
    assert fetched.ai_review_attempts == 1
