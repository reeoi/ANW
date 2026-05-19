"""Validate-and-heal orchestration — lint → dry-run → repair loop. Phase 5.4."""

from __future__ import annotations

import logging
from typing import Any

from generator.c_pipeline.contract import StepManifest
from generator.c_pipeline.step_dryrun import dry_run
from generator.c_pipeline.step_linter import lint

logger = logging.getLogger(__name__)


def validate_and_heal(
    manifest: StepManifest,
    peer_steps: list[dict] | None = None,
    *,
    max_rounds: int = 3,
    client=None,
) -> tuple[StepManifest, list[dict], dict[str, Any]]:
    """Lint → dry-run → repair loop. Returns (manifest, issues, stats)."""
    stats: dict[str, Any] = {"rounds": 0, "repaired": False, "dry_run_ok": False}

    for round_idx in range(1, max_rounds + 1):
        stats["rounds"] = round_idx
        issues = lint(manifest)
        issues_dicts = [{"level": i.level, "field": i.field, "message": i.message} for i in issues]
        errors = [i for i in issues_dicts if i["level"] == "error"]

        if errors:
            logger.info("heal round %s: %s lint errors, repairing", round_idx, len(errors))
            from generator.c_pipeline.step_repair import repair
            manifest, issues_dicts = repair(manifest, issues_dicts, peer_steps, client=client)
            stats["repaired"] = True
            continue

        # Lint clean → dry-run
        dr = dry_run(manifest)
        if dr.ok:
            stats["dry_run_ok"] = True
            return manifest, [], stats

        # Dry-run failed → feed to repair
        logger.info("heal round %s: dry-run failed with %s issues", round_idx, len(dr.issues))
        all_issues = issues_dicts + dr.issues
        from generator.c_pipeline.step_repair import repair
        manifest, issues_dicts = repair(manifest, all_issues, peer_steps, client=client)
        stats["repaired"] = True

    issues = lint(manifest)
    issues_dicts = [{"level": i.level, "field": i.field, "message": i.message} for i in issues]
    return manifest, issues_dicts, stats


__all__ = ["validate_and_heal"]
