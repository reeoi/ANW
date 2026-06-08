"""Tests for Phase G.1 — cost_tracker should_degrade + on_budget_exceeded.

Coverage matrix:

- ``should_degrade(phase)`` returns False under-budget.
- ``should_degrade`` returns True for ``degrade_phases`` once monthly spend
  ≥ ``monthly_budget_cny`` AND on_budget_exceeded='degrade'.
- Non-degrade phases (phase_0/1/2/4) remain False.
- ``on_budget_exceeded='stop'`` raises ``BudgetExceededError`` from
  ``select_model_for_phase``; should_abort returns True.
- ``daily_token_limit`` triggers degrade independently of monthly budget.
- Per-phase wiring: phase3_sections / phase5_deslop / ai_review /
  weekly_scan really pass ``model=flash_model`` when degrade fires.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, ChatUsage
from generator.c_pipeline.cost_tracker import (
    BudgetExceededError,
    CostTracker,
)

# ============================================================ helpers


def _config(
    tmp_path: Path,
    *,
    monthly_budget: float = 100.0,
    daily_tokens: int = 800_000,
    on_exceeded: str = "degrade",
    degrade=None,
) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(ROOT)},
            "deepseek": {
                "api_key": "",
                "mock": True,
                "model": "deepseek-v4-pro",
                "flash_model": "deepseek-v4-flash",
            },
            "database": {"sqlite_path": str(tmp_path / "anw.sqlite3")},
            "cost_limits": {
                "monthly_budget_cny": monthly_budget,
                "daily_token_limit": daily_tokens,
                "on_budget_exceeded": on_exceeded,
                "degrade_phases": degrade if degrade is not None else
                    ["phase_3", "phase_5", "ai_review", "weekly_scan"],
            },
        },
        path=Path("config.yaml"),
    )


def _push_monthly_spend(tracker: CostTracker, cny: float) -> None:
    """Push the running monthly spend to the given CNY total."""
    now_month = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, cost_cny, occurred_at) "
            "VALUES (?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", float(cny), f"{now_month}-15 10:00:00"),
        )


def _push_daily_tokens(tracker: CostTracker, tokens: int) -> None:
    """Push today's token count to ``tokens`` (split across input + output)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with sqlite3.connect(tracker.db_path) as conn:
        conn.execute(
            "INSERT INTO pipeline_cost_log (phase, model, input_tokens, "
            "output_tokens, cost_cny, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("phase_1", "deepseek-v4-pro", tokens, 0, 0.0, f"{today} 10:00:00"),
        )


# ============================================================ should_degrade


def test_under_budget_should_degrade_false_for_all_phases(tmp_path: Path) -> None:
    tracker = CostTracker(_config(tmp_path))
    for phase in ("phase_0", "phase_1", "phase_2", "phase_3", "phase_4",
                   "phase_5", "ai_review", "weekly_scan"):
        assert tracker.should_degrade(phase) is False, phase


def test_over_budget_degrades_listed_phases_only(tmp_path: Path) -> None:
    tracker = CostTracker(_config(tmp_path, monthly_budget=10.0))
    _push_monthly_spend(tracker, 11.0)

    # listed → True
    assert tracker.should_degrade("phase_3") is True
    assert tracker.should_degrade("phase_3_section_05") is True  # prefix match
    assert tracker.should_degrade("phase_5") is True
    assert tracker.should_degrade("ai_review") is True
    assert tracker.should_degrade("weekly_scan") is True

    # not listed → False (must stay on pro)
    assert tracker.should_degrade("phase_0") is False
    assert tracker.should_degrade("phase_1") is False
    assert tracker.should_degrade("phase_2") is False
    assert tracker.should_degrade("phase_4") is False


def test_on_budget_exceeded_stop_aborts_instead_of_degrading(tmp_path: Path) -> None:
    tracker = CostTracker(_config(tmp_path, monthly_budget=10.0, on_exceeded="stop"))
    _push_monthly_spend(tracker, 11.0)

    status = tracker.get_status()
    assert status.is_budget_exceeded is True
    # 'stop' policy never sets is_degrade_active (degrade is the alternative)
    assert status.is_degrade_active is False
    assert status.should_abort is True

    # should_abort() helper exposed for callers
    assert tracker.should_abort() is True

    # And select_model_for_phase raises BudgetExceededError instead of falling
    # back to flash (the orchestrator must abort the run).
    with pytest.raises(BudgetExceededError):
        tracker.select_model_for_phase(
            "phase_3", default_model="pro", flash_model="flash"
        )


def test_daily_token_limit_triggers_degrade_independently(tmp_path: Path) -> None:
    tracker = CostTracker(_config(tmp_path, monthly_budget=10000.0, daily_tokens=800_000))
    _push_daily_tokens(tracker, 800_001)

    status = tracker.get_status()
    assert status.is_token_limit_exceeded is True
    assert status.is_budget_exceeded is False  # only token cap crossed
    assert status.is_degrade_active is True

    # phases listed in degrade_phases get downgraded
    assert tracker.should_degrade("phase_3") is True
    assert tracker.should_degrade("phase_5") is True
    assert tracker.should_degrade("ai_review") is True
    assert tracker.should_degrade("weekly_scan") is True
    # phases not listed stay on pro
    assert tracker.should_degrade("phase_0") is False
    assert tracker.should_degrade("phase_4") is False


def test_daily_token_limit_zero_means_disabled(tmp_path: Path) -> None:
    tracker = CostTracker(_config(tmp_path, monthly_budget=10000.0, daily_tokens=0))
    _push_daily_tokens(tracker, 5_000_000)
    status = tracker.get_status()
    assert status.is_token_limit_exceeded is False


def test_degrade_phases_empty_disables_degrade(tmp_path: Path) -> None:
    tracker = CostTracker(_config(tmp_path, monthly_budget=1.0, degrade=[]))
    _push_monthly_spend(tracker, 5.0)
    status = tracker.get_status()
    assert status.is_budget_exceeded is True
    # No phases configured → degrade not active
    assert status.is_degrade_active is False
    assert tracker.should_degrade("phase_3") is False


def test_select_model_returns_flash_when_token_cap_exceeded(tmp_path: Path) -> None:
    tracker = CostTracker(_config(tmp_path, daily_tokens=1000))
    _push_daily_tokens(tracker, 5000)
    chosen = tracker.select_model_for_phase(
        "phase_3", default_model="deepseek-v4-pro", flash_model="deepseek-v4-flash"
    )
    assert chosen == "deepseek-v4-flash"


# ============================================================ phase3 wiring


def _make_outline_md() -> str:
    """Two-section outline (200 char target each) for fast Phase 3 mock runs."""
    return (
        "| 节号 | 主事件 | 子事件 | 情绪 | 读者新获知 | 钩子 | 伏笔/物件 | 动静 | 对话密度 | target_words |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
        "| 01 | 开场 | 子1 | 焦虑 | 主角入场 | 留白 | 钥匙 | 动 | 0.4 | 200 |\n"
        "| 02 | 转折 | 子2 | 紧张 | 真相浮现 | 反问 | 信封 | 静 | 0.5 | 200 |\n"
    )


def _make_phase1_framework_md() -> str:
    return (
        "# 标题\n小标题\n\n## summary\n这是一篇短篇,用来测试。\n\n## 设定\n人物甲、人物乙。\n"
    )


class _RecordingClient:
    """Minimal DeepSeekClient stub that records every chat_completion call."""

    @dataclass
    class _Settings:
        model: str = "deepseek-v4-pro"
        flash_model: str = "deepseek-v4-flash"
        thinking_mode: bool = True

    def __init__(self) -> None:
        self.settings = self._Settings()
        self.calls: list[dict[str, Any]] = []

    def is_mock(self) -> bool:
        return True

    def chat_completion(
        self,
        messages,
        *,
        thinking_mode=None,
        model=None,
        temperature=0.8,
        response_format=None,
        purpose="chat",
    ) -> ChatCompletion:
        self.calls.append(
            {
                "model": model,
                "purpose": purpose,
                "thinking_mode": thinking_mode,
                "messages": list(messages),
            }
        )
        # Provide a long enough response to pass mock validators where needed.
        body = (
            "她推开门走进屋子。\n"
            "桌上摆着茶杯,茶水还没凉。\n"
            "他抬起头,眼神平静。\n"
        ) * 80
        return ChatCompletion(
            text=body,
            reasoning=None,
            model=model or self.settings.model,
            usage=ChatUsage(input_tokens=10, cached_tokens=0, output_tokens=10),
            finish_reason="stop",
            cached=False,
        )


def test_phase3_sections_uses_flash_when_degraded(tmp_path: Path) -> None:
    from generator.c_pipeline import phase3_sections

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "1_设定.md").write_text(_make_phase1_framework_md(), encoding="utf-8")
    (work_dir / "2_小节大纲.md").write_text(_make_outline_md(), encoding="utf-8")

    config = _config(tmp_path, monthly_budget=1.0)
    tracker = CostTracker(config)
    _push_monthly_spend(tracker, 5.0)  # force degrade

    client = _RecordingClient()
    phase3_sections.run_sections(
        config,
        work_dir=work_dir,
        client=client,  # type: ignore[arg-type]
        cost_tracker=tracker,
        max_section_retries=0,  # 0 retries → exactly one call per section
    )
    # All Phase 3 calls should have been routed to flash
    phase3_calls = [c for c in client.calls if str(c["purpose"]).startswith("phase_3_")]
    assert phase3_calls, "expected phase_3 calls to be recorded"
    for c in phase3_calls:
        assert c["model"] == "deepseek-v4-flash", c


def test_phase3_sections_uses_pro_under_budget(tmp_path: Path) -> None:
    from generator.c_pipeline import phase3_sections

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "1_设定.md").write_text(_make_phase1_framework_md(), encoding="utf-8")
    (work_dir / "2_小节大纲.md").write_text(_make_outline_md(), encoding="utf-8")

    config = _config(tmp_path)  # 100 CNY budget, untouched
    tracker = CostTracker(config)
    client = _RecordingClient()

    phase3_sections.run_sections(
        config,
        work_dir=work_dir,
        client=client,  # type: ignore[arg-type]
        cost_tracker=tracker,
        max_section_retries=0,
    )
    phase3_calls = [c for c in client.calls if str(c["purpose"]).startswith("phase_3_")]
    assert phase3_calls
    # No degrade → model passed should be None (so client uses its default).
    for c in phase3_calls:
        assert c["model"] is None, c


# ============================================================ phase5 wiring


def test_phase5_deslop_uses_flash_when_degraded(tmp_path: Path) -> None:
    from generator.c_pipeline import phase5_deslop

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "4_精修稿.md").write_text("精修稿正文足够长" * 200, encoding="utf-8")

    config = _config(tmp_path, monthly_budget=1.0)
    tracker = CostTracker(config)
    _push_monthly_spend(tracker, 5.0)

    client = _RecordingClient()
    phase5_deslop.run_deslop(
        config,
        work_dir=work_dir,
        client=client,  # type: ignore[arg-type]
        cost_tracker=tracker,
    )
    phase5_calls = [c for c in client.calls if c["purpose"] == "phase_5"]
    assert phase5_calls
    assert phase5_calls[0]["model"] == "deepseek-v4-flash"


def test_phase5_deslop_uses_pro_under_budget(tmp_path: Path) -> None:
    from generator.c_pipeline import phase5_deslop

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "4_精修稿.md").write_text("精修稿正文足够长" * 200, encoding="utf-8")

    config = _config(tmp_path)
    tracker = CostTracker(config)

    client = _RecordingClient()
    phase5_deslop.run_deslop(
        config,
        work_dir=work_dir,
        client=client,  # type: ignore[arg-type]
        cost_tracker=tracker,
    )
    phase5_calls = [c for c in client.calls if c["purpose"] == "phase_5"]
    assert phase5_calls
    assert phase5_calls[0]["model"] is None  # default (pro)


# ============================================================ ai_review wiring


def test_ai_review_swaps_model_for_degrade(tmp_path: Path) -> None:
    """``_maybe_swap_model_for_degrade`` should rewrite settings.model to flash
    when the cost tracker says ai_review must downgrade."""
    from review_queue.ai_review import AIReviewSettings, _maybe_swap_model_for_degrade

    config = _config(tmp_path, monthly_budget=1.0)
    tracker = CostTracker(config)
    _push_monthly_spend(tracker, 5.0)

    settings = AIReviewSettings(model="deepseek-v4-pro", api_key="x", mock=False)
    routed = _maybe_swap_model_for_degrade(settings, config=config)
    assert routed.model == "deepseek-v4-flash"
    # original frozen dataclass untouched
    assert settings.model == "deepseek-v4-pro"


def test_ai_review_keeps_pro_under_budget(tmp_path: Path) -> None:
    from review_queue.ai_review import AIReviewSettings, _maybe_swap_model_for_degrade

    config = _config(tmp_path)
    settings = AIReviewSettings(model="deepseek-v4-pro", api_key="x", mock=False)
    routed = _maybe_swap_model_for_degrade(settings, config=config)
    assert routed.model == "deepseek-v4-pro"


# ============================================================ weekly_scan wiring


def test_weekly_scan_passes_flash_when_degraded(tmp_path: Path, monkeypatch) -> None:
    """run_weekly_scan should pick flash for the LLM call when degrade fires.

    We mock out the heavyweight schema/diversity validators so the test stays
    focused on model selection — the existing test_scan_evolver suite already
    covers the validation logic.
    """
    from scan import seed_evolver

    config = _config(tmp_path, monthly_budget=1.0)
    tracker = CostTracker(config)
    _push_monthly_spend(tracker, 5.0)

    # Build a minimal seeds + theme_pool layout the scan expects.
    seeds = {
        "llm_evolution_prompt_template": "pool_size=$pool_size",
        "target_platform": {"primary": "番茄", "primary_traits": {}, "comparator_platforms": {}},
        "emotion_types": [{"id": "e1"}],
        "genres": [{"id": "g1"}],
        "title_patterns": {},
        "opening_modes": [{"id": "o1"}],
        "ending_modes": [{"id": "x1"}],
        "reversal_types": [{"id": "r1"}],
        "diversity_constraints": {},
        "time_seed_modifiers": {},
    }
    monkeypatch.setattr(seed_evolver, "load_seeds", lambda *a, **k: seeds)
    monkeypatch.setattr(seed_evolver, "_validate_schema", lambda *a, **k: None)
    monkeypatch.setattr(seed_evolver, "_validate_diversity", lambda *a, **k: None)

    client = _RecordingClient()
    # Populate scan response with one normalized item to avoid pool_size mismatch.
    fake_item = {"id": "tp_x_001", "theme": "测试主题足够长A", "emotion": "e1", "genre": "g1",
                 "formula_used": "f", "target_platform": "番茄", "target_length": [8000, 12000],
                 "hint_title": "x", "title_pattern_used": "p", "opening_mode": "o1",
                 "ending_mode": "x1", "reversal_type": "r1", "expected_audience": "a",
                 "seasonal_or_topic_seed": "s", "consumed_count": 0, "created_at": "2026-01-01T00:00:00Z"}

    original_chat = client.chat_completion

    def fake_chat(messages, **kwargs):
        completion = original_chat(messages, **kwargs)
        return ChatCompletion(
            text=json.dumps([fake_item]),
            reasoning=None,
            model=completion.model,
            usage=completion.usage,
            finish_reason="stop",
            cached=False,
        )

    client.chat_completion = fake_chat  # type: ignore[assignment]

    monkeypatch.setattr(seed_evolver, "_atomic_write_json", lambda *a, **k: None)
    monkeypatch.setattr(seed_evolver, "_read_existing_pool", lambda *a, **k: None)

    seed_evolver.run_weekly_scan(config, client=client, cost_tracker=tracker, force=True)

    weekly_calls = [c for c in client.calls if c["purpose"] == "weekly_scan"]
    assert weekly_calls
    assert weekly_calls[0]["model"] == "deepseek-v4-flash"


def test_weekly_scan_keeps_pro_under_budget(tmp_path: Path, monkeypatch) -> None:
    from scan import seed_evolver

    config = _config(tmp_path)
    tracker = CostTracker(config)

    seeds = {
        "llm_evolution_prompt_template": "pool_size=$pool_size",
        "target_platform": {"primary": "番茄", "primary_traits": {}, "comparator_platforms": {}},
        "emotion_types": [{"id": "e1"}],
        "genres": [{"id": "g1"}],
        "title_patterns": {},
        "opening_modes": [{"id": "o1"}],
        "ending_modes": [{"id": "x1"}],
        "reversal_types": [{"id": "r1"}],
        "diversity_constraints": {},
        "time_seed_modifiers": {},
    }
    monkeypatch.setattr(seed_evolver, "load_seeds", lambda *a, **k: seeds)
    monkeypatch.setattr(seed_evolver, "_validate_schema", lambda *a, **k: None)
    monkeypatch.setattr(seed_evolver, "_validate_diversity", lambda *a, **k: None)
    monkeypatch.setattr(seed_evolver, "_atomic_write_json", lambda *a, **k: None)
    monkeypatch.setattr(seed_evolver, "_read_existing_pool", lambda *a, **k: None)

    client = _RecordingClient()
    fake_item = {"id": "tp_x_001", "theme": "测试主题足够长A", "emotion": "e1", "genre": "g1",
                 "formula_used": "f", "target_platform": "番茄", "target_length": [8000, 12000],
                 "hint_title": "x", "title_pattern_used": "p", "opening_mode": "o1",
                 "ending_mode": "x1", "reversal_type": "r1", "expected_audience": "a",
                 "seasonal_or_topic_seed": "s", "consumed_count": 0, "created_at": "2026-01-01T00:00:00Z"}

    original_chat = client.chat_completion

    def fake_chat(messages, **kwargs):
        completion = original_chat(messages, **kwargs)
        return ChatCompletion(
            text=json.dumps([fake_item]),
            reasoning=None,
            model=completion.model,
            usage=completion.usage,
            finish_reason="stop",
            cached=False,
        )

    client.chat_completion = fake_chat  # type: ignore[assignment]
    seed_evolver.run_weekly_scan(config, client=client, cost_tracker=tracker, force=True)

    weekly_calls = [c for c in client.calls if c["purpose"] == "weekly_scan"]
    assert weekly_calls
    assert weekly_calls[0]["model"] == "deepseek-v4-pro"
