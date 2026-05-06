"""CLI entry point: manually trigger daily publish-slot planning.

Usage:
    python -m cli.plan_today                  # plan slots for today
    python -m cli.plan_today --date 2026-05-08

Decision #17 / #21: ``planned_count = uniform_int[daily_count_min, daily_count_max]``
within ``operating_hours`` with ``slot_min_gap_minutes`` enforcement; the
result is upserted into ``daily_publish_plan`` so the scheduler (when
running) and downstream slot triggers consume it.

This CLI does NOT register APScheduler jobs — only the persistent
``daily_publish_plan`` row is written. When the long-running scheduler
later boots, ``register_publish_slots`` picks up unfired slots from this
row.
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from config_loader import load_from_environment
from scheduler_planner import plan_today_publishes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sample today's publish slots and upsert daily_publish_plan. "
            "PLAN §3.1 / §7 Phase D / decisions #17 / #21."
        )
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Override target date (YYYY-MM-DD). Defaults to today.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_from_environment()
    for warning in config.warnings:
        print(f"[config] {warning}")

    target_day: date | None = None
    if args.date:
        try:
            target_day = date.fromisoformat(args.date)
        except ValueError:
            print(f"--date must be YYYY-MM-DD, got: {args.date!r}")
            return 2

    plan = plan_today_publishes(config, today=target_day)
    print(f"date={plan.date}")
    print(f"planned_count={plan.planned_count}")
    slots = json.loads(plan.slots_json)
    for i, slot in enumerate(slots):
        print(
            f"  #{i:02d} slot_time={slot['slot_time']} "
            f"story_id={slot['story_id']} skipped_reason={slot['skipped_reason']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
