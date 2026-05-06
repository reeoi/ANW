"""Weekly seed-evolution scan module (PLAN §4 / §3.1).

Reads the static seed library (data/scan_seeds.yaml), injects weekly trend
modifiers, and asks DeepSeek-V4-Pro to evolve a 100-item theme pool that
later feeds the c_pipeline orchestrator (Phase 0 selection).
"""

from __future__ import annotations

from .seed_evolver import (
    WeeklyScanBlockedError,
    WeeklyScanResult,
    build_evolution_prompt,
    load_seeds,
    pick_weekly_topics,
    run_weekly_scan,
)

__all__ = [
    "WeeklyScanBlockedError",
    "WeeklyScanResult",
    "build_evolution_prompt",
    "load_seeds",
    "pick_weekly_topics",
    "run_weekly_scan",
]
