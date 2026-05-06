"""Tests for ``scheduler_planner.pick_story_for_slot`` (decision #20).

FIFO + cross-day emotion balance: when several approved stories are
available, we prefer the one whose emotion has appeared least often in
the most recent ``lookback_days`` window of *published* stories. Ties
fall back to FIFO (created_at ASC, id ASC). Stories already claimed by
other slots in today's plan are excluded so each approved item is
consumed at most once per day.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue.db import (
    initialize_database,
    insert_story,
    upsert_daily_publish_plan,
)
from review_queue.models import DailyPublishPlan, Story
from scheduler_planner import pick_story_for_slot


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={"database": {"sqlite_path": str(tmp_path / "picker.sqlite3")}},
        path=Path("picker.yaml"),
    )


def _put_story(
    db_path: Path,
    *,
    title: str,
    status: str,
    emotion: str,
    created_at: str = "2026-04-30 09:00:00",
    updated_at: str | None = None,
) -> int:
    sid = insert_story(
        db_path,
        Story(
            title=title,
            status=status,
            emotion=emotion,
            current_phase="phase_5_done",
            target_length=10000,
        ),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE stories SET created_at = ?, updated_at = ? WHERE id = ?",
            (created_at, updated_at or created_at, sid),
        )
    return sid


def test_picker_returns_none_when_no_approved(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    _put_story(db_path, title="x", status="pending", emotion="意难平")
    assert pick_story_for_slot(db_path, today=date(2026, 5, 1)) is None


def test_picker_fifo_when_emotions_equal(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    early = _put_story(
        db_path,
        title="A 早",
        status="approved",
        emotion="意难平",
        created_at="2026-04-30 08:00:00",
    )
    late = _put_story(
        db_path,
        title="B 晚",
        status="approved",
        emotion="意难平",
        created_at="2026-04-30 18:00:00",
    )
    chosen = pick_story_for_slot(db_path, today=date(2026, 5, 1))
    assert chosen is not None
    assert chosen.id == early
    assert chosen.id != late


def test_picker_prefers_underrepresented_emotion_after_recent_publishes(
    tmp_path: Path,
) -> None:
    """Decision #20: 近 3 天发了 3 个"shuang_gan_shi_fang",当日候选优先选其它情绪."""

    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    today = date(2026, 5, 7)
    yesterday = (today - timedelta(days=1)).isoformat() + " 12:00:00"
    two_days_ago = (today - timedelta(days=2)).isoformat() + " 12:00:00"

    # Three recent published stories with the same emotion (frequent).
    _put_story(
        db_path,
        title="已发 1",
        status="published",
        emotion="shuang_gan_shi_fang",
        created_at=two_days_ago,
        updated_at=two_days_ago,
    )
    _put_story(
        db_path,
        title="已发 2",
        status="published",
        emotion="shuang_gan_shi_fang",
        created_at=yesterday,
        updated_at=yesterday,
    )
    _put_story(
        db_path,
        title="已发 3",
        status="published",
        emotion="shuang_gan_shi_fang",
        created_at=yesterday,
        updated_at=yesterday,
    )

    # Two approved candidates: same FIFO ordering, but different emotions.
    same_emotion_first = _put_story(
        db_path,
        title="候选-shuang",
        status="approved",
        emotion="shuang_gan_shi_fang",
        created_at="2026-04-30 09:00:00",
    )
    other_emotion_later = _put_story(
        db_path,
        title="候选-yi-nan-ping",
        status="approved",
        emotion="意难平",
        created_at="2026-04-30 18:00:00",
    )

    chosen = pick_story_for_slot(db_path, today=today, lookback_days=3)
    assert chosen is not None
    # FIFO would pick the earlier-created shuang one, but emotion balance
    # forces us to the underrepresented emotion despite later created_at.
    assert chosen.id == other_emotion_later
    assert chosen.id != same_emotion_first


def test_picker_excludes_stories_claimed_by_other_slots(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    today = date(2026, 5, 1)
    today_str = today.isoformat()
    early = _put_story(
        db_path,
        title="A",
        status="approved",
        emotion="意难平",
        created_at="2026-04-30 08:00:00",
    )
    late = _put_story(
        db_path,
        title="B",
        status="approved",
        emotion="意难平",
        created_at="2026-04-30 18:00:00",
    )
    plan = DailyPublishPlan(
        date=today_str,
        planned_count=2,
        slots_json=json.dumps(
            [
                {
                    "slot_time": f"{today_str}T10:00:00",
                    "story_id": early,
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
    chosen = pick_story_for_slot(db_path, today=today, slot_index=1)
    assert chosen is not None
    assert chosen.id == late
    # Slot 0's own claim is its own — when picking for slot 0 we should
    # ignore that claim and re-evaluate.
    chosen_for_slot0 = pick_story_for_slot(db_path, today=today, slot_index=0)
    assert chosen_for_slot0 is not None
    assert chosen_for_slot0.id == early
