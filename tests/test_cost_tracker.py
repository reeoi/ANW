"""Tests for generator/c_pipeline/cost_tracker.py (Phase C.8).

Coverage:
- estimate_call_cost_cny: pro vs flash gives different costs, cache hits cheap
- record_call writes a row to pipeline_cost_log + bumps stories.pipeline_cost_cny
- monthly_spend_cny aggregates the current calendar month only
- BudgetStatus.is_degrade_active flips when used ≥ monthly_budget_cny
- select_model_for_phase routes degraded phases to flash_model
- select_model_for_phase keeps non-degrade phases on default_model
- pricing override via config.cost_limits.unit_price_cny
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, ChatUsage
from generator.c_pipeline.cost_tracker import (
    BudgetStatus,
    CostTracker,
    DEFAULT_PRICING,
    ModelPricing,
    estimate_call_cost_cny,
)
from review_queue.db import insert_story
from review_queue.models import Story


def _config(tmp_path: Path, *, monthly_budget: float = 100.0, degrade=None) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(ROOT)},
            "deepseek": {"api_key": "", "mock": True},
            "database": {"sqlite_path": str(tmp_path / "anp.sqlite3")},
            "cost_limits": {
                "monthly_budget_cny": monthly_budget,
                "degrade_phases": degrade if degrade is not None else
                    ["phase_3", "phase_5", "ai_review", "weekly_scan"],
            },
        },
        path=Path("config.yaml"),
    )


# ============================================================ estimate_call_cost_cny


def test_estimate_pro_call_with_no_cache_hit() -> None:
    cost = estimate_call_cost_cny(
        model="deepseek-v4-pro",
        input_tokens=1_000_000,
        cached_tokens=0,
        output_tokens=200_000,
    )
    # 1M input @ 4 + 200k output @ 6/M = 4 + 1.2 = 5.2
    assert cost == pytest.approx(5.2, abs=0.001)


def test_estimate_pro_call_with_full_cache_hit() -> None:
    cost = estimate_call_cost_cny(
        model="deepseek-v4-pro",
        input_tokens=1_000_000,
        cached_tokens=1_000_000,
        output_tokens=200_000,
    )
    # 1M cached @ 0.025/M + 200k output @ 6/M = 0.025 + 1.2 = 1.225
    assert cost == pytest.approx(1.225, abs=0.001)


def test_estimate_flash_cheaper_than_pro_for_same_tokens() -> None:
    args = dict(input_tokens=500_000, cached_tokens=0, output_tokens=200_000)
    pro = estimate_call_cost_cny(model="deepseek-v4-pro", **args)
    flash = estimate_call_cost_cny(model="deepseek-v4-flash", **args)
    assert flash < pro
    # flash output is 1/3 of pro; flash input is 1/4 of pro → expect substantially lower
    assert flash < pro * 0.5


def test_estimate_unknown_model_falls_back_to_pro_pricing() -> None:
    cost_unknown = estimate_call_cost_cny(
        model="unknown-model", input_tokens=100_000, cached_tokens=0, output_tokens=10_000
    )
    cost_pro = estimate_call_cost_cny(
        model="deepseek-v4-pro", input_tokens=100_000, cached_tokens=0, output_tokens=10_000
    )
    assert cost_unknown == pytest.approx(cost_pro)


def test_estimate_zero_tokens_zero_cost() -> None:
    assert (
        estimate_call_cost_cny(
            model="deepseek-v4-pro", input_tokens=0, cached_tokens=0, output_tokens=0
        )
        == 0.0
    )


# ============================================================ persistence


def test_record_call_writes_pipeline_cost_log_row(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tracker = CostTracker(config)
    story_id = insert_story(tracker.db_path, Story(title="t", work_dir=str(tmp_path / "w")))

    cost = tracker.record_call(
        story_id=story_id,
        phase="phase_3_section_01",
        model="deepseek-v4-pro",
        usage=ChatUsage(input_tokens=100_000, cached_tokens=80_000, output_tokens=2000),
    )
    assert cost > 0
    with sqlite3.connect(tracker.db_path) as conn:
        rows = list(
            conn.execute(
                "SELECT story_id, phase, model, input_tokens, cached_tokens, output_tokens, cost_cny FROM pipeline_cost_log"
            )
        )
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == story_id
    assert row[1] == "phase_3_section_01"
    assert row[2] == "deepseek-v4-pro"
    assert row[3] == 100_000
    assert row[4] == 80_000
    assert row[5] == 2000
    assert pytest.approx(row[6], abs=0.001) == cost


def test_record_call_bumps_pipeline_cost_cny_on_story(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tracker = CostTracker(config)
    story_id = insert_story(tracker.db_path, Story(title="t", work_dir=str(tmp_path / "w")))

    tracker.record_call(
        story_id=story_id,
        phase="phase_1",
        model="deepseek-v4-pro",
        usage=ChatUsage(input_tokens=200_000, cached_tokens=0, output_tokens=5000),
    )
    tracker.record_call(
        story_id=story_id,
        phase="phase_2",
        model="deepseek-v4-pro",
        usage=ChatUsage(input_tokens=300_000, cached_tokens=200_000, output_tokens=8000),
    )

    with sqlite3.connect(tracker.db_path) as conn:
        row = conn.execute(
            "SELECT pipeline_cost_cny FROM stories WHERE id=?", (story_id,)
        ).fetchone()
    assert row[0] > 0


def test_record_completion_extracts_usage_from_completion(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tracker = CostTracker(config)
    completion = ChatCompletion(
        text="...",
        reasoning=None,
        model="deepseek-v4-pro",
        usage=ChatUsage(input_tokens=50_000, cached_tokens=10_000, output_tokens=3000),
        finish_reason="stop",
        cached=True,
    )
    cost = tracker.record_completion(story_id=None, phase="phase_4", completion=completion)
    assert cost > 0
    with sqlite3.connect(tracker.db_path) as conn:
        row = conn.execute(
            "SELECT phase, model, input_tokens FROM pipeline_cost_log"
        ).fetchone()
    assert row[0] == "phase_4"
    assert row[1] == "deepseek-v4-pro"
    assert row[2] == 50_000


def test_record_call_with_null_story_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tracker = CostTracker(config)
    cost = tracker.record_call(
        story_id=None,
        phase="weekly_scan",
        model="deepseek-v4-pro",
        usage=ChatUsage(input_tokens=10_000, cached_tokens=0, output_tokens=500),
    )
    assert cost > 0
    with sqlite3.connect(tracker.db_path) as conn:
        row = conn.execute("SELECT story_id FROM pipeline_cost_log").fetchone()
    assert row[0] is None


# ============================================================ monthly_spend_cny


def test_monthly_spend_aggregates_current_month_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tracker = CostTracker(config)
    # Insert two rows: one in current month, one from a past month
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", 12.34, f"{now_month}-15 10:00:00"),
        )
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", 99.99, "2020-01-15 10:00:00"),
        )
    used = tracker.monthly_spend_cny()
    assert used == pytest.approx(12.34, abs=0.01)


def test_monthly_spend_explicit_period(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tracker = CostTracker(config)
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", 50.0, "2020-01-15 10:00:00"),
        )
    assert tracker.monthly_spend_cny(month="2020-01") == pytest.approx(50.0)
    assert tracker.monthly_spend_cny(month="2020-02") == 0.0


# ============================================================ get_status


def test_get_status_under_budget(tmp_path: Path) -> None:
    config = _config(tmp_path, monthly_budget=100.0)
    tracker = CostTracker(config)
    tracker.record_call(
        story_id=None,
        phase="phase_3",
        model="deepseek-v4-pro",
        usage=ChatUsage(input_tokens=100, cached_tokens=0, output_tokens=10),
    )
    status = tracker.get_status()
    assert status.monthly_budget_cny == 100.0
    assert status.is_degrade_active is False
    assert status.remaining_cny > 99.0


def test_get_status_over_budget_triggers_degrade(tmp_path: Path) -> None:
    config = _config(tmp_path, monthly_budget=10.0)
    tracker = CostTracker(config)
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_3", "deepseek-v4-pro", 11.0, f"{now_month}-15 10:00:00"),
        )
    status = tracker.get_status()
    assert status.used_cny == pytest.approx(11.0, abs=0.001)
    assert status.is_degrade_active is True
    assert status.remaining_cny == 0.0
    assert "phase_3" in status.degrade_phases


def test_get_status_no_degrade_when_phases_empty(tmp_path: Path) -> None:
    config = _config(tmp_path, monthly_budget=1.0, degrade=[])
    tracker = CostTracker(config)
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_3", "deepseek-v4-pro", 5.0, f"{now_month}-15 10:00:00"),
        )
    status = tracker.get_status()
    # No phases configured for degrade → never degrade
    assert status.is_degrade_active is False


# ============================================================ select_model_for_phase


def test_select_model_phase_3_falls_back_to_flash_when_over_budget(tmp_path: Path) -> None:
    config = _config(tmp_path, monthly_budget=1.0)
    tracker = CostTracker(config)
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", 5.0, f"{now_month}-15 10:00:00"),
        )
    chosen = tracker.select_model_for_phase(
        "phase_3_section_01",
        default_model="deepseek-v4-pro",
        flash_model="deepseek-v4-flash",
    )
    assert chosen == "deepseek-v4-flash"


def test_select_model_phase_1_stays_on_pro_when_over_budget(tmp_path: Path) -> None:
    config = _config(tmp_path, monthly_budget=1.0)
    tracker = CostTracker(config)
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", 5.0, f"{now_month}-15 10:00:00"),
        )
    # phase_1 is NOT in degrade_phases → stays on pro
    chosen = tracker.select_model_for_phase(
        "phase_1",
        default_model="deepseek-v4-pro",
        flash_model="deepseek-v4-flash",
    )
    assert chosen == "deepseek-v4-pro"


def test_select_model_under_budget_always_default(tmp_path: Path) -> None:
    config = _config(tmp_path, monthly_budget=1000.0)
    tracker = CostTracker(config)
    chosen = tracker.select_model_for_phase(
        "phase_3",
        default_model="deepseek-v4-pro",
        flash_model="deepseek-v4-flash",
    )
    assert chosen == "deepseek-v4-pro"


def test_select_model_phase_5_routes_to_flash(tmp_path: Path) -> None:
    config = _config(tmp_path, monthly_budget=1.0)
    tracker = CostTracker(config)
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) VALUES (?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", 5.0, f"{now_month}-15 10:00:00"),
        )
    chosen = tracker.select_model_for_phase(
        "phase_5",
        default_model="deepseek-v4-pro",
        flash_model="deepseek-v4-flash",
    )
    assert chosen == "deepseek-v4-flash"


# ============================================================ pricing override


def test_config_unit_price_overrides_default(tmp_path: Path) -> None:
    config = LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(ROOT)},
            "deepseek": {"api_key": "", "mock": True},
            "database": {"sqlite_path": str(tmp_path / "anp.sqlite3")},
            "cost_limits": {
                "monthly_budget_cny": 100.0,
                "degrade_phases": [],
                "unit_price_cny": {
                    "pro": {"input": 8.0, "cached_input": 0.05, "output": 12.0},
                    "flash": {"input": 2.0, "cached_input": 0.04, "output": 4.0},
                },
            },
        },
        path=Path("config.yaml"),
    )
    tracker = CostTracker(config)
    cost = tracker.record_call(
        story_id=None,
        phase="phase_1",
        model="deepseek-v4-pro",
        usage=ChatUsage(input_tokens=1_000_000, cached_tokens=0, output_tokens=100_000),
    )
    # 1M @ 8 + 100k @ 12/M = 8 + 1.2 = 9.2
    assert cost == pytest.approx(9.2, abs=0.001)
