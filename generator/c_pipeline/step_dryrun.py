"""Step dry-run — run a step with mock fixtures. Phase 5.2."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from generator.c_pipeline.contract import StepManifest

logger = logging.getLogger(__name__)

DRY_RUN_TIMEOUT = 30  # seconds


@dataclass
class DryRunResult:
    ok: bool
    issues: list[dict] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    duration_seconds: float = 0


def dry_run(
    manifest: StepManifest,
    fixtures: dict[str, Any] | None = None,
    ctx_overrides: dict[str, Any] | None = None,
) -> DryRunResult:
    """Simulate running a step without side effects."""
    fixtures = fixtures or manifest.fixtures or {}
    issues: list[dict] = []
    logs: list[str] = []
    started = time.monotonic()

    try:
        # 1. Check executor is reachable
        from generator.c_pipeline.step_linter import lint
        lint_issues = lint(manifest)
        for li in lint_issues:
            if li.level == "error":
                issues.append({"level": li.level, "field": li.field, "message": li.message})

        # 2. Check preconditions
        for pc in manifest.preconditions:
            if not _check_precondition(pc):
                issues.append({
                    "level": "error", "field": "preconditions",
                    "message": f"Precondition not met: {pc}",
                })

        # 3. Mock action execution
        if manifest.executor.kind.value == "action_chain" and manifest.executor.actions:
            for a in manifest.executor.actions:
                aname = a.get("action", "") if isinstance(a, dict) else ""
                # Check action is known
                from generator.c_pipeline.actions.runner import _ACTION_REGISTRY
                if aname and aname not in _ACTION_REGISTRY:
                    issues.append({"level": "error", "field": "actions", "message": f"Unknown action: {aname}"})
                logs.append(f"mock: {aname}")

        # 4. Use fixtures for expected outputs
        outputs: dict[str, Any] = {}
        for port in manifest.outputs:
            outputs[port.name] = fixtures.get(port.name, f"<mock {port.name}>")

    except Exception as exc:
        issues.append({"level": "error", "field": "runtime", "message": str(exc)})

    return DryRunResult(
        ok=len([i for i in issues if i["level"] == "error"]) == 0,
        issues=issues,
        outputs={},
        logs=logs,
        duration_seconds=round(time.monotonic() - started, 2),
    )


def _check_precondition(condition: str) -> bool:
    """Check if a precondition is met. Currently only chrome_cdp_connected."""
    if condition == "chrome_cdp_connected":
        from publisher.chrome_launcher import is_cdp_ready
        return is_cdp_ready()
    return True  # unknown conditions pass


__all__ = ["DryRunResult", "dry_run"]
