"""Tests for ``scheduler_planner.plan_today_publishes`` (Phase D, decisions #17 / #21).

Covers:

- ``planned_count`` distribution across many trials sits in expected range
  for ``uniform_int[0, 5]`` and uses every value at least once.
- ``slot_min_gap_minutes`` is honoured between any two adjacent slots.
- All slot times fall inside ``operating_hours``.
- When ``slot_min_gap_minutes`` makes the requested count infeasible,
  the result is silently downscaled (no exception).
- Re-running for the same date upserts (only the latest row remains).
- Each slot dict in ``slots_json`` exposes the four contract keys
  (slot_time / story_id / published_at / skipped_reason) with the
  documented defaults.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
from datetime import date, datetime, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue.db import get_daily_publish_plan, initialize_database
from scheduler_planner import plan_today_publishes


def _config(
    tmp_path: Path,
    *,
    daily_min: int = 0,
    daily_max: int = 5,
    operating_hours: tuple[str, str] = ("09:00", "22:00"),
    slot_min_gap_minutes: int = 30,
) -> LoadedConfig:
    return LoadedConfig(
        data={
            "database": {"sqlite_path": str(tmp_path / "plan.sqlite3")},
            "publisher": {
                "daily_count_min": daily_min,
                "daily_count_max": daily_max,
                "operating_hours": list(operating_hours),
                "slot_min_gap_minutes": slot_min_gap_minutes,
            },
        },
        path=Path("plan.yaml"),
    )


def _slot_times(plan_slots_json: str) -> list[datetime]:
    payload = json.loads(plan_slots_json)
    return [datetime.fromisoformat(s["slot_time"]) for s in payload]


# ============================================================ distribution


def test_daily_count_distribution_uniform_0_to_5(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    counts: list[int] = []
    for i in range(1000):
        plan = plan_today_publishes(
            cfg,
            today=date(2026, 5, 1),
            rng=random.Random(i),
        )
        counts.append(plan.planned_count)

    mean = sum(counts) / len(counts)
    # Each value 0..5 should appear at least once across 1000 trials
    # (probability of missing any specific value ≈ (5/6)^1000 ≈ 0).
    assert set(counts) == {0, 1, 2, 3, 4, 5}
    # uniform_int[0,5] expectation = 2.5; allow [2.0, 3.0] margin.
    assert 2.0 <= mean <= 3.0, f"mean={mean} outside [2.0, 3.0]"


# ============================================================ gap >= 30 min


def test_gap_at_least_slot_min_gap_minutes(tmp_path: Path) -> None:
    cfg = _config(tmp_path, daily_min=2, daily_max=5, slot_min_gap_minutes=30)
    saw_multi_slot_plan = False
    for seed in range(50):
        plan = plan_today_publishes(
            cfg,
            today=date(2026, 5, 1),
            rng=random.Random(seed),
        )
        slots = _slot_times(plan.slots_json)
        if len(slots) >= 2:
            saw_multi_slot_plan = True
            for a, b in zip(slots, slots[1:]):
                assert (b - a).total_seconds() >= 30 * 60, (
                    f"seed={seed} slot pair below 30 min gap: {a} -> {b}"
                )
    assert saw_multi_slot_plan, "no multi-slot plans observed; range invalid"


# ============================================================ window


def test_all_slots_inside_operating_hours(tmp_path: Path) -> None:
    cfg = _config(tmp_path, daily_min=1, daily_max=5)
    today = date(2026, 5, 1)
    start_dt = datetime.combine(today, time(9, 0))
    end_dt = datetime.combine(today, time(22, 0))
    for seed in range(60):
        plan = plan_today_publishes(cfg, today=today, rng=random.Random(seed))
        slots = _slot_times(plan.slots_json)
        for slot in slots:
            assert start_dt <= slot <= end_dt, (
                f"seed={seed} slot {slot} outside [{start_dt}, {end_dt}]"
            )


# ============================================================ downscale


def test_downscale_when_gap_does_not_fit(tmp_path: Path) -> None:
    """daily_count_max=30 + slot_min_gap=60 in 9-22 -> max feasible 14 < 30."""

    cfg = _config(
        tmp_path,
        daily_min=30,
        daily_max=30,
        slot_min_gap_minutes=60,
    )
    today = date(2026, 5, 1)
    seen_lengths: set[int] = set()
    for seed in range(20):
        plan = plan_today_publishes(cfg, today=today, rng=random.Random(seed))
        # window 09:00-22:00 = 780 min, gap 60 -> max k = 780//60 + 1 = 14.
        assert plan.planned_count <= 14
        assert plan.planned_count < 30
        seen_lengths.add(plan.planned_count)
        slots = _slot_times(plan.slots_json)
        for a, b in zip(slots, slots[1:]):
            assert (b - a).total_seconds() >= 60 * 60
    # Even with max=30, every run fits within feasible_max=14.
    assert max(seen_lengths) <= 14


# ============================================================ upsert


def test_upsert_same_date_replaces(tmp_path: Path) -> None:
    cfg = _config(tmp_path, daily_min=2, daily_max=2)
    today = date(2026, 5, 1)
    today_str = today.isoformat()
    db_path = initialize_database(cfg)

    plan_v1 = plan_today_publishes(cfg, today=today, rng=random.Random(1))
    plan_v2 = plan_today_publishes(cfg, today=today, rng=random.Random(2))

    fetched = get_daily_publish_plan(db_path, today_str)
    assert fetched is not None
    assert fetched.slots_json == plan_v2.slots_json
    assert fetched.slots_json != plan_v1.slots_json

    with sqlite3.connect(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM daily_publish_plan WHERE date = ?",
            (today_str,),
        ).fetchone()[0]
    assert n == 1


# ============================================================ slots_json schema


def test_slots_json_field_contract(tmp_path: Path) -> None:
    cfg = _config(tmp_path, daily_min=3, daily_max=3)
    plan = plan_today_publishes(
        cfg,
        today=date(2026, 5, 1),
        rng=random.Random(7),
    )
    slots = json.loads(plan.slots_json)
    assert plan.planned_count == len(slots)
    assert len(slots) == 3
    for slot in slots:
        assert set(slot.keys()) == {"slot_time", "story_id", "published_at", "skipped_reason"}
        # New-plan defaults: only slot_time populated.
        assert isinstance(slot["slot_time"], str)
        assert slot["story_id"] is None
        assert slot["published_at"] is None
        assert slot["skipped_reason"] is None
        # ISO format parsable.
        parsed = datetime.fromisoformat(slot["slot_time"])
        assert parsed.second == 0
        assert parsed.microsecond == 0


def test_zero_planned_count_emits_empty_slots_array(tmp_path: Path) -> None:
    cfg = _config(tmp_path, daily_min=0, daily_max=0)
    plan = plan_today_publishes(cfg, today=date(2026, 5, 1), rng=random.Random(0))
    assert plan.planned_count == 0
    assert json.loads(plan.slots_json) == []


def test_invalid_daily_count_range_raises(tmp_path: Path) -> None:
    cfg = _config(tmp_path, daily_min=5, daily_max=2)
    with pytest.raises(ValueError):
        plan_today_publishes(cfg, today=date(2026, 5, 1), rng=random.Random(0))


def test_invalid_operating_hours_raises(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        daily_min=1,
        daily_max=1,
        operating_hours=("22:00", "09:00"),
    )
    with pytest.raises(ValueError):
        plan_today_publishes(cfg, today=date(2026, 5, 1), rng=random.Random(0))
