"""月度预算闸门（`generator/budget.py`）。

此前 `cost_limits.monthly_budget_cny` 只约束 c_pipeline（CostTracker）；长篇
生成走 `api_usage` 另一本账，完全没有花费上限。闸门统一在
`DeepSeekClient.chat_completion` 入口检查「两本账的当月合计」：

- 预算未配置 / 未超限 → 放行；
- 超限 + ``on_budget_exceeded='stop'`` → 抛 `MonthlyBudgetExceededError`；
- 超限 + ``'degrade'``（默认）→ 仅当调用方**未显式传 model** 时降级 flash；
  c_pipeline 各 phase 显式传 model（自带 CostTracker 决策），语义不变；
- mock / dry-run 不花钱，完全跳过闸门。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator import budget
from generator.api_client import ChatCompletion, ChatUsage, DeepSeekClient
from review_queue.db import initialize_database, insert_pipeline_cost_log
from review_queue.metrics import record_api_usage
from review_queue.models import PipelineCostLogEntry

PRO = "deepseek-v4-pro"
FLASH = "deepseek-v4-flash"


def _config(
    tmp_path: Path,
    *,
    budget_cny: float,
    policy: str = "degrade",
    api_key: str = "test-key",
) -> LoadedConfig:
    return LoadedConfig(
        data={
            "database": {"sqlite_path": str(tmp_path / "budget.sqlite3")},
            "cost_limits": {"monthly_budget_cny": budget_cny, "on_budget_exceeded": policy},
            "deepseek": {"api_key": api_key, "model": PRO, "flash_model": FLASH},
        },
        path=tmp_path / "config.yaml",
    )


def _seed_spend(config: LoadedConfig, *, pipeline_cny: float = 0.0, usage_cny: float = 0.0) -> Path:
    db = initialize_database(config)
    if pipeline_cny:
        insert_pipeline_cost_log(
            db,
            PipelineCostLogEntry(story_id=None, phase="phase_3", model=PRO, cost_cny=pipeline_cny),
        )
    if usage_cny:
        record_api_usage(
            db,
            provider="deepseek",
            model=PRO,
            purpose="chapter_draft",
            prompt_tokens=1000,
            completion_tokens=2000,
            cost_cny=usage_cny,
        )
    return db


# ------------------------------------------------------------- 聚合查询


def test_combined_month_spend_sums_both_ledgers(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100)
    db = _seed_spend(config, pipeline_cny=60.0, usage_cny=50.0)
    assert budget.combined_month_spend_cny(db) == pytest.approx(110.0)


def test_old_month_spend_not_counted(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100)
    db = _seed_spend(config, pipeline_cny=999.0)
    # insert_pipeline_cost_log 不接受自定义时间戳（occurred_at 走 datetime('now')
    # 默认值），这里直接把整表改成旧月份来验证月份过滤。
    import sqlite3

    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE pipeline_cost_log SET occurred_at = '2020-01-15 10:00:00'")
    assert budget.combined_month_spend_cny(db) == pytest.approx(0.0)


def test_missing_tables_fail_open_as_zero(tmp_path: Path) -> None:
    empty_db = tmp_path / "empty.sqlite3"
    assert budget.combined_month_spend_cny(empty_db) == pytest.approx(0.0)


# ------------------------------------------------------------- 闸门决策


def test_no_budget_configured_is_noop(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=0)
    _seed_spend(config, usage_cny=500.0)
    assert budget.enforce_monthly_budget(config, requested_model=None, chosen_model=PRO, flash_model=FLASH) == PRO


def test_under_budget_keeps_default_model(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100)
    _seed_spend(config, usage_cny=99.0)
    assert budget.enforce_monthly_budget(config, requested_model=None, chosen_model=PRO, flash_model=FLASH) == PRO


def test_degrade_policy_swaps_default_model_to_flash(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100, policy="degrade")
    _seed_spend(config, pipeline_cny=40.0, usage_cny=70.0)
    assert budget.enforce_monthly_budget(config, requested_model=None, chosen_model=PRO, flash_model=FLASH) == FLASH


def test_degrade_policy_respects_explicit_model(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100, policy="degrade")
    _seed_spend(config, usage_cny=120.0)
    assert budget.enforce_monthly_budget(config, requested_model=PRO, chosen_model=PRO, flash_model=FLASH) == PRO


def test_stop_policy_raises_over_budget(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100, policy="stop")
    _seed_spend(config, usage_cny=120.0)
    with pytest.raises(budget.MonthlyBudgetExceededError):
        budget.enforce_monthly_budget(config, requested_model=None, chosen_model=PRO, flash_model=FLASH)


def test_invalid_budget_value_treated_as_unconfigured(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100)
    config.data["cost_limits"]["monthly_budget_cny"] = "not-a-number"
    _seed_spend(config, usage_cny=500.0)
    assert budget.enforce_monthly_budget(config, requested_model=None, chosen_model=PRO, flash_model=FLASH) == PRO


# ------------------------------------------------------------- 客户端接线


def test_chat_completion_stop_policy_blocks_before_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, budget_cny=100, policy="stop", api_key="real-key")
    _seed_spend(config, usage_cny=150.0)

    def _no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("budget gate must block before any network request")

    monkeypatch.setattr("generator.api_client.urlopen", _no_network)
    client = DeepSeekClient(config)
    assert not client.is_mock()
    with pytest.raises(budget.MonthlyBudgetExceededError):
        client.chat_completion([{"role": "user", "content": "hi"}])


def test_chat_completion_degrade_policy_swaps_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path, budget_cny=100, policy="degrade", api_key="real-key")
    _seed_spend(config, usage_cny=150.0)
    client = DeepSeekClient(config)
    captured: dict[str, Any] = {}

    def _fake_live(messages: Any, *, model: str, **kwargs: Any) -> ChatCompletion:
        captured["model"] = model
        return ChatCompletion(text="ok", reasoning=None, model=model, usage=ChatUsage())

    monkeypatch.setattr(client, "_live_completion", _fake_live)
    client.chat_completion([{"role": "user", "content": "hi"}])
    assert captured["model"] == FLASH


def test_chat_completion_mock_skips_gate(tmp_path: Path) -> None:
    config = _config(tmp_path, budget_cny=100, policy="stop", api_key="")
    _seed_spend(config, usage_cny=150.0)
    client = DeepSeekClient(config)
    assert client.is_mock()
    completion = client.chat_completion([{"role": "user", "content": "hi"}])
    assert completion.text
