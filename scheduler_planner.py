"""Phase D planning + slot logic (PLAN §3.1, decisions #17-#21).

Pure functions used by ``scheduler.py``:

- ``plan_today_publishes(config, today=...)`` — sample today's publish slots
  via ``uniform_int[daily_count_min, daily_count_max]`` within
  ``operating_hours`` with ``slot_min_gap_minutes`` enforcement, downscaling
  when the window can't fit the sampled count. Persists via
  ``review_queue.db.upsert_daily_publish_plan``.
- ``pick_story_for_slot(db_path, today=..., slot_index=...)`` — FIFO over
  ``status='approved'`` stories, with cross-day emotion balance:
  approved story whose emotion was published least often in the last
  ``lookback_days`` wins ties.
- ``mark_slot_story / mark_slot_skipped / mark_slot_published`` — update
  ``slots_json`` in place via upsert.

The scheduler module wires these into APScheduler triggers.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from config_loader import LoadedConfig
from review_queue.db import (
    get_daily_publish_plan,
    get_database_path,
    initialize_database,
    story_from_row,
    upsert_daily_publish_plan,
)
from review_queue.models import DailyPublishPlan, Story

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlotPick:
    """One slot row inside ``slots_json``."""

    slot_time: str
    story_id: int | None = None
    published_at: str | None = None
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_time": self.slot_time,
            "story_id": self.story_id,
            "published_at": self.published_at,
            "skipped_reason": self.skipped_reason,
        }


# ============================================================ plan_today_publishes


def plan_today_publishes(
    config: LoadedConfig,
    *,
    today: date | None = None,
    rng: random.Random | None = None,
) -> DailyPublishPlan:
    """Sample today's publish slots, persist (upsert), return the plan row.

    Decision #21: ``planned_count = uniform_int[daily_count_min, daily_count_max]``.
    Decision #17: enforce ``slot_min_gap_minutes`` within ``operating_hours``;
    when the window can't fit the sampled count, downscale to the largest
    feasible count.
    """

    today = today or date.today()
    rng = rng or random.Random()
    publisher_cfg = _mapping(config.data.get("publisher"))

    daily_min = int(publisher_cfg.get("daily_count_min", 0))
    daily_max = int(publisher_cfg.get("daily_count_max", 5))
    if daily_min < 0 or daily_max < daily_min:
        raise ValueError(
            f"publisher.daily_count_min/max invalid: min={daily_min} max={daily_max}"
        )

    operating = publisher_cfg.get("operating_hours") or ["09:00", "22:00"]
    if not (isinstance(operating, list) and len(operating) == 2):
        raise ValueError("publisher.operating_hours must be [start, end]")
    start_hm = _parse_hm(str(operating[0]))
    end_hm = _parse_hm(str(operating[1]))

    gap_minutes = int(publisher_cfg.get("slot_min_gap_minutes", 30))
    if gap_minutes < 0:
        raise ValueError("publisher.slot_min_gap_minutes must be >= 0")

    raw_count = rng.randint(daily_min, daily_max)
    slots = _sample_slot_times(
        today,
        start_hm=start_hm,
        end_hm=end_hm,
        gap_minutes=gap_minutes,
        target_count=raw_count,
        rng=rng,
    )

    slots_json = json.dumps(
        [s.to_dict() for s in slots], ensure_ascii=False
    )
    plan = DailyPublishPlan(
        date=today.isoformat(),
        planned_count=len(slots),
        slots_json=slots_json,
    )
    db_path = initialize_database(config)
    upsert_daily_publish_plan(db_path, plan)
    logger.info(
        "plan_today_publishes: date=%s planned=%s sampled=%s",
        plan.date,
        len(slots),
        raw_count,
    )
    return plan


def _sample_slot_times(
    on_date: date,
    *,
    start_hm: tuple[int, int],
    end_hm: tuple[int, int],
    gap_minutes: int,
    target_count: int,
    rng: random.Random,
) -> list[SlotPick]:
    """Return up to ``target_count`` slot picks within [start, end], gap >= gap_minutes."""

    if target_count <= 0:
        return []
    start_dt = datetime.combine(on_date, time(*start_hm))
    end_dt = datetime.combine(on_date, time(*end_hm))
    if end_dt <= start_dt:
        raise ValueError(
            f"operating_hours window invalid: {start_hm} -> {end_hm}"
        )

    window_minutes = int((end_dt - start_dt).total_seconds() // 60)
    if gap_minutes <= 0:
        feasible_max = target_count
    else:
        feasible_max = window_minutes // gap_minutes + 1

    target = max(0, min(target_count, feasible_max))
    if target <= 0:
        return []

    # Closed-form sampler: pick `target` offsets (with replacement) from
    # [0, available], sort, then add i*gap to the i-th. The expansion is
    # gap-monotone so any pair of resulting slots is at least gap_minutes
    # apart. Sampling with replacement is fine here because the +i*gap
    # expansion guarantees uniqueness.
    available = window_minutes - (target - 1) * max(0, gap_minutes)
    if available < 0:
        available = 0
    offsets = sorted(rng.randint(0, available) for _ in range(target))

    picks: list[SlotPick] = []
    for i, off in enumerate(offsets):
        slot_dt = start_dt + timedelta(minutes=off + i * gap_minutes)
        slot_dt = slot_dt.replace(second=0, microsecond=0)
        picks.append(SlotPick(slot_time=slot_dt.isoformat()))
    return picks


# ============================================================ slot picker


def pick_story_for_slot(
    db_path: str | Path,
    *,
    today: date | None = None,
    slot_index: int | None = None,
    lookback_days: int = 3,
) -> Story | None:
    """Choose the next approved story for a slot.

    Selection is FIFO (created_at ASC, id ASC) but reweighted by cross-day
    emotion balance: candidates whose ``emotion`` appears less often in the
    most recent ``lookback_days`` of *published* stories are preferred. If
    today's plan already has slots with ``story_id`` set on other indices,
    those stories are excluded so the same story is never published twice.
    """

    today = today or date.today()
    today_str = today.isoformat()
    db_path = Path(db_path)

    plan = get_daily_publish_plan(db_path, today_str)
    claimed_ids: set[int] = set()
    if plan is not None:
        try:
            slots = json.loads(plan.slots_json)
        except json.JSONDecodeError:
            slots = []
        for i, slot in enumerate(slots):
            if slot_index is not None and i == slot_index:
                continue
            sid = slot.get("story_id") if isinstance(slot, dict) else None
            if sid is not None:
                try:
                    claimed_ids.add(int(sid))
                except (TypeError, ValueError):
                    continue

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, title, status, pipeline_version, work_dir, current_phase,
                   final_content_path, pipeline_cost_cny, target_length,
                   emotion, genre, hint_title, summary,
                   ai_review_score, ai_review_attempts, content,
                   created_at, updated_at
            FROM stories
            WHERE status = 'approved'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        candidates = [story_from_row(r) for r in rows]
        cutoff_iso = (today - timedelta(days=max(0, lookback_days))).isoformat()
        emotion_rows = connection.execute(
            """
            SELECT emotion FROM stories
            WHERE status = 'published'
              AND emotion IS NOT NULL
              AND COALESCE(updated_at, created_at) >= ?
            """,
            (cutoff_iso,),
        ).fetchall()

    candidates = [s for s in candidates if s.id is not None and s.id not in claimed_ids]
    if not candidates:
        return None

    histogram: Counter[str] = Counter(
        str(r[0]) for r in emotion_rows if r[0] is not None
    )

    def sort_key(s: Story) -> tuple[int, str, int]:
        return (
            histogram.get(s.emotion or "", 0),
            str(s.created_at or ""),
            int(s.id or 0),
        )

    candidates.sort(key=sort_key)
    return candidates[0]


# ============================================================ slot mutations


def _update_slot(
    db_path: str | Path,
    today_str: str,
    slot_index: int,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
) -> bool:
    """Read-modify-write one slot dict in today's slots_json (upsert)."""

    plan = get_daily_publish_plan(db_path, today_str)
    if plan is None:
        return False
    try:
        slots = json.loads(plan.slots_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(slots, list):
        return False
    if slot_index < 0 or slot_index >= len(slots):
        return False
    slot = slots[slot_index] if isinstance(slots[slot_index], dict) else {}
    slots[slot_index] = mutator(dict(slot))
    new_plan = DailyPublishPlan(
        date=plan.date,
        planned_count=plan.planned_count,
        slots_json=json.dumps(slots, ensure_ascii=False),
    )
    upsert_daily_publish_plan(db_path, new_plan)
    return True


def mark_slot_story(
    db_path: str | Path,
    *,
    today: str,
    slot_index: int,
    story_id: int,
) -> bool:
    """Set ``slots_json[slot_index].story_id`` (claim the slot for a story)."""

    return _update_slot(db_path, today, slot_index, lambda s: {**s, "story_id": int(story_id)})


def mark_slot_skipped(
    db_path: str | Path,
    *,
    today: str,
    slot_index: int,
    reason: str,
) -> bool:
    """Set ``slots_json[slot_index].skipped_reason`` (no-approved-story / paused / ...)."""

    return _update_slot(db_path, today, slot_index, lambda s: {**s, "skipped_reason": str(reason)})


def mark_slot_published(
    db_path: str | Path,
    *,
    today: str,
    slot_index: int,
    published_at: str,
) -> bool:
    """Set ``slots_json[slot_index].published_at`` after a successful publish."""

    return _update_slot(db_path, today, slot_index, lambda s: {**s, "published_at": str(published_at)})


# ============================================================ helpers


def _parse_hm(raw: str) -> tuple[int, int]:
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid HH:MM literal: {raw!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"HH:MM out of range: {raw!r}")
    return hour, minute


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "SlotPick",
    "mark_slot_published",
    "mark_slot_skipped",
    "mark_slot_story",
    "pick_story_for_slot",
    "plan_today_publishes",
]
