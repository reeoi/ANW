"""Cost tracking + B2 budget-driven degrade (PLAN §3.2, §5.1).

Three jobs:

1. **Estimate** per-call cost from token usage using model-aware pricing
   (decision: v4-pro vs v4-flash). Cache hits are billed at the discounted
   rate; cache misses at the regular input rate.
2. **Persist** every call to ``pipeline_cost_log`` (per-call cost telemetry)
   and bump ``stories.pipeline_cost_cny`` so the monitoring view can show
   per-story totals.
3. **Decide** when to downgrade. When the running monthly spend crosses
   ``cost_limits.monthly_budget_cny`` (default 100 CNY = decision B2), any
   phase listed in ``cost_limits.degrade_phases`` is rerouted to the
   ``flash_model``. Other phases stay on the configured ``model``.

Rough DeepSeek prices used for estimation come from PLAN §10 / DeepSeek
docs and can be overridden via ``config.cost_limits.unit_price_cny.{pro,flash}``.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, ChatUsage
from review_queue.db import (
    add_pipeline_cost,
    get_database_path,
    initialize_database,
    insert_pipeline_cost_log,
)
from review_queue.models import PipelineCostLogEntry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    """Per-model CNY pricing per million tokens.

    DeepSeek-V4-Pro has a large prompt-cache discount (decision M2 / B2).
    """

    input_cny_per_million: float
    cached_input_cny_per_million: float
    output_cny_per_million: float


# PLAN §10: pro cache hit 0.025/M, output 6/M; flash cache hit 0.02/M, output 2/M.
# Cache-miss input rates are public DeepSeek list prices (approximate).
DEFAULT_PRICING: dict[str, ModelPricing] = {
    "deepseek-v4-pro": ModelPricing(
        input_cny_per_million=4.0,
        cached_input_cny_per_million=0.025,
        output_cny_per_million=6.0,
    ),
    "deepseek-v4-flash": ModelPricing(
        input_cny_per_million=1.0,
        cached_input_cny_per_million=0.02,
        output_cny_per_million=2.0,
    ),
}


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot of monthly budget status used for degrade decisions."""

    monthly_budget_cny: float
    used_cny: float
    remaining_cny: float
    is_degrade_active: bool
    degrade_phases: tuple[str, ...]
    period: str  # YYYY-MM, the calendar month being aggregated

    @property
    def usage_ratio(self) -> float:
        if self.monthly_budget_cny <= 0:
            return 0.0
        return round(self.used_cny / self.monthly_budget_cny, 4)


# ============================================================ pricing


def estimate_call_cost_cny(
    *,
    model: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    pricing: Mapping[str, ModelPricing] | None = None,
) -> float:
    """Estimate one chat-completion call cost in CNY.

    ``cached_tokens`` is the prompt-cache-hit subset of ``input_tokens``.
    Fresh input (cache miss) is billed at the regular input rate; cache hits
    use the discounted rate.
    """
    pricing = pricing or DEFAULT_PRICING
    p = _resolve_pricing(pricing, model)
    fresh_input = max(0, int(input_tokens) - int(cached_tokens))
    cost = (
        fresh_input * p.input_cny_per_million
        + int(cached_tokens) * p.cached_input_cny_per_million
        + int(output_tokens) * p.output_cny_per_million
    ) / 1_000_000
    return round(cost, 6)


def _resolve_pricing(
    pricing: Mapping[str, ModelPricing], model: str
) -> ModelPricing:
    if model in pricing:
        return pricing[model]
    # Fallbacks by family.
    lower = model.lower()
    if "flash" in lower and "deepseek-v4-flash" in pricing:
        return pricing["deepseek-v4-flash"]
    return pricing.get("deepseek-v4-pro") or ModelPricing(
        input_cny_per_million=4.0,
        cached_input_cny_per_million=0.025,
        output_cny_per_million=6.0,
    )


# ============================================================ tracker


class CostTracker:
    """Per-config helper that persists token usage and decides on degrade."""

    def __init__(
        self,
        config: LoadedConfig,
        *,
        db_path: Path | None = None,
        pricing: Mapping[str, ModelPricing] | None = None,
    ) -> None:
        self.config = config
        self.db_path = Path(db_path) if db_path else get_database_path(config)
        self.pricing = pricing or _resolve_config_pricing(config)
        self._cost_limits = config.data.get("cost_limits", {}) or {}
        self.monthly_budget_cny = float(
            self._cost_limits.get("monthly_budget_cny") or 0
        )
        self.degrade_phases: tuple[str, ...] = tuple(
            self._cost_limits.get("degrade_phases") or ()
        )
        # Ensure schema exists (idempotent — db.initialize_database is safe).
        initialize_database(config)

    # ------------------------------------------------------------ recording

    def record_completion(
        self,
        *,
        story_id: int | None,
        phase: str,
        completion: ChatCompletion,
    ) -> float:
        """Convenience: pull tokens out of ``ChatCompletion`` and persist."""
        return self.record_call(
            story_id=story_id,
            phase=phase,
            model=completion.model,
            usage=completion.usage,
        )

    def record_call(
        self,
        *,
        story_id: int | None,
        phase: str,
        model: str,
        usage: ChatUsage,
    ) -> float:
        """Persist one LLM call's cost and bump per-story aggregate."""
        cost = estimate_call_cost_cny(
            model=model,
            input_tokens=usage.input_tokens,
            cached_tokens=usage.cached_tokens,
            output_tokens=usage.output_tokens,
            pricing=self.pricing,
        )
        try:
            insert_pipeline_cost_log(
                self.db_path,
                PipelineCostLogEntry(
                    story_id=story_id,
                    phase=phase,
                    model=model,
                    input_tokens=usage.input_tokens,
                    cached_tokens=usage.cached_tokens,
                    output_tokens=usage.output_tokens,
                    cost_cny=cost,
                ),
            )
            if story_id is not None:
                add_pipeline_cost(self.db_path, story_id, cost)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            logger.warning("record_call failed: %s", exc)
        return cost

    # ------------------------------------------------------------ querying

    def monthly_spend_cny(self, *, month: str | None = None) -> float:
        """Sum cost_cny across the given calendar month (YYYY-MM, UTC)."""
        period = month or _current_month_utc()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(cost_cny), 0)
                FROM pipeline_cost_log
                WHERE strftime('%Y-%m', occurred_at) = ?
                """,
                (period,),
            ).fetchone()
        return float(row[0] or 0.0)

    def get_status(self) -> BudgetStatus:
        period = _current_month_utc()
        used = self.monthly_spend_cny(month=period)
        remaining = max(0.0, self.monthly_budget_cny - used)
        is_degrade = (
            self.monthly_budget_cny > 0
            and used >= self.monthly_budget_cny
            and bool(self.degrade_phases)
        )
        return BudgetStatus(
            monthly_budget_cny=self.monthly_budget_cny,
            used_cny=used,
            remaining_cny=remaining,
            is_degrade_active=is_degrade,
            degrade_phases=self.degrade_phases,
            period=period,
        )

    # ------------------------------------------------------------ routing

    def select_model_for_phase(
        self,
        phase: str,
        *,
        default_model: str,
        flash_model: str,
        status: BudgetStatus | None = None,
    ) -> str:
        """Return ``flash_model`` only when degrade active AND phase listed.

        Phase keys we recognise (matching ``cost_limits.degrade_phases``):
        ``phase_0``, ``phase_1``, ``phase_2``, ``phase_3`` (matches
        ``phase_3_section_NN``), ``phase_4``, ``phase_5``, ``ai_review``,
        ``weekly_scan``.
        """
        status = status or self.get_status()
        if not status.is_degrade_active:
            return default_model
        if _phase_in_degrade_list(phase, status.degrade_phases):
            return flash_model
        return default_model


# ============================================================ helpers


def _phase_in_degrade_list(
    phase: str, degrade_phases: Iterable[str]
) -> bool:
    """phase_3_section_05 should match the 'phase_3' degrade rule."""
    p = (phase or "").strip().lower()
    for entry in degrade_phases:
        e = (entry or "").strip().lower()
        if not e:
            continue
        if p == e or p.startswith(e + "_") or p.startswith(e):
            return True
    return False


def _current_month_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _resolve_config_pricing(config: LoadedConfig) -> dict[str, ModelPricing]:
    """Allow config.cost_limits.unit_price_cny to override default rates."""
    overrides = (
        (config.data.get("cost_limits") or {}).get("unit_price_cny") or {}
    )
    if not isinstance(overrides, dict) or not overrides:
        return DEFAULT_PRICING
    pricing = dict(DEFAULT_PRICING)
    for key, model_default in DEFAULT_PRICING.items():
        section = overrides.get(_pricing_key_for_model(key))
        if not isinstance(section, dict):
            continue
        pricing[key] = ModelPricing(
            input_cny_per_million=float(section.get("input", model_default.input_cny_per_million)),
            cached_input_cny_per_million=float(
                section.get("cached_input", model_default.cached_input_cny_per_million)
            ),
            output_cny_per_million=float(section.get("output", model_default.output_cny_per_million)),
        )
    return pricing


def _pricing_key_for_model(model: str) -> str:
    if "flash" in model.lower():
        return "flash"
    return "pro"


__all__ = [
    "BudgetStatus",
    "CostTracker",
    "DEFAULT_PRICING",
    "ModelPricing",
    "estimate_call_cost_cny",
]
