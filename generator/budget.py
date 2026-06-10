"""月度预算闸门 —— 在 ``DeepSeekClient.chat_completion`` 入口统一执行。

背景：``cost_limits.monthly_budget_cny`` 此前只约束 c_pipeline（CostTracker
在编排层降级 / 中止）；长篇生成与主题建议走 ``api_usage`` 另一本账，完全
没有花费上限。本模块把「两本账（``pipeline_cost_log`` + ``api_usage``）的
当月合计」作为统一口径，在客户端入口检查：

- 预算未配置（<=0）或未超限 → 原样放行；
- 超限 + ``on_budget_exceeded='stop'`` → 抛 :class:`MonthlyBudgetExceededError`
  （所有调用方硬中止，与 CostTracker 的 'stop' 语义一致）；
- 超限 + ``'degrade'``（默认）→ **仅当调用方未显式指定 model 时**换用
  flash 模型。c_pipeline 各 phase 均显式传 model（由其 CostTracker 自主
  决定降级范围 ``degrade_phases``），因此行为不受影响。

查询失败（如表尚未建立）按花费 0 处理并告警——闸门失效宁可放行也不能
阻塞创作（fail-open）。

依赖方向说明：与 ``api_client._record_usage`` 一样依赖 ``review_queue.db``
的路径计算；待 storage 层抽取（改进清单 #11）后一并归位。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config_loader import LoadedConfig
from review_queue.db import get_database_path

logger = logging.getLogger(__name__)

# 两本账同构：cost_cny + occurred_at（TEXT，UTC datetime('now') 默认值）。
_SPEND_QUERIES = (
    "SELECT COALESCE(SUM(cost_cny), 0) FROM pipeline_cost_log WHERE strftime('%Y-%m', occurred_at) = ?",
    "SELECT COALESCE(SUM(cost_cny), 0) FROM api_usage WHERE strftime('%Y-%m', occurred_at) = ?",
)


class MonthlyBudgetExceededError(RuntimeError):
    """``on_budget_exceeded='stop'`` 且当月合计花费已达预算时抛出。"""


def _current_month_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _safe_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def combined_month_spend_cny(db_path: str | Path, *, month: str | None = None) -> float:
    """两本账（pipeline_cost_log + api_usage）指定月份（默认当月，UTC）合计。"""
    period = month or _current_month_utc()
    total = 0.0
    try:
        with sqlite3.connect(Path(db_path)) as connection:
            for query in _SPEND_QUERIES:
                try:
                    row = connection.execute(query, (period,)).fetchone()
                    total += float(row[0] or 0.0)
                except sqlite3.Error as exc:
                    logger.warning("月度花费查询失败（按 0 处理）: %s", exc)
    except sqlite3.Error as exc:
        logger.warning("月度花费查询无法连接数据库（闸门 fail-open）: %s", exc)
    return total


def enforce_monthly_budget(
    config: LoadedConfig,
    *,
    requested_model: str | None,
    chosen_model: str,
    flash_model: str | None,
) -> str:
    """检查当月合计花费，返回应使用的模型；'stop' 策略超限时抛错。

    Args:
        config: 已加载配置（取 ``cost_limits`` 与数据库路径）。
        requested_model: 调用方显式传入的 model（None 表示使用默认）。
        chosen_model: 入口已解析的模型（requested 或 settings 默认）。
        flash_model: 降级目标模型；为空则不降级。
    """
    limits = config.data.get("cost_limits") or {}
    budget_cny = _safe_float(limits.get("monthly_budget_cny"))
    if budget_cny <= 0:
        return chosen_model

    spent = combined_month_spend_cny(get_database_path(config))
    if spent < budget_cny:
        return chosen_model

    policy = str(limits.get("on_budget_exceeded") or "degrade").strip().lower()
    if policy == "stop":
        raise MonthlyBudgetExceededError(
            f"本月 LLM 花费 {spent:.2f} CNY 已达预算 {budget_cny:.2f} CNY"
            "（cost_limits.on_budget_exceeded=stop）。如需继续，请调高 monthly_budget_cny 或改用 degrade 策略。"
        )
    if requested_model is None and flash_model and chosen_model != flash_model:
        logger.warning("本月 LLM 花费 %.2f CNY 已达预算 %.2f CNY：默认模型降级为 %s", spent, budget_cny, flash_model)
        return flash_model
    return chosen_model


__all__ = ["MonthlyBudgetExceededError", "combined_month_spend_cny", "enforce_monthly_budget"]
