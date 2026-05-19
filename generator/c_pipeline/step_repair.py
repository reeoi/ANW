"""AI-assisted step repair — feed lint/dry-run errors to LLM for fixing. Phase 5.3."""

from __future__ import annotations

import logging
import string
from pathlib import Path

from generator.c_pipeline.contract import StepManifest
from generator.c_pipeline.contract_io import dump_manifest, load_manifest

logger = logging.getLogger(__name__)

_REPAIR_PROMPT_FILE = Path(__file__).parent / "prompts" / "step_repair_system.txt"

DEFAULT_MAX_RETRIES = 3


def repair(
    manifest: StepManifest,
    issues: list,
    peer_steps: list[dict] | None = None,
    *,
    client=None,
    max_rounds: int = DEFAULT_MAX_RETRIES,
) -> tuple[StepManifest, list]:
    """Try to repair a manifest using LLM feedback. Returns (manifest, remaining_issues)."""
    import yaml

    for round_idx in range(1, max_rounds + 1):
        errors = [i for i in issues if i.get("level") == "error"]
        if not errors:
            logger.info("repair round %s: no errors, stopping", round_idx)
            return manifest, issues

        if client is None:
            from config_loader import load_from_environment
            from generator.api_client import DeepSeekClient
            client = DeepSeekClient(load_from_environment())

        # Build repair prompt
        manifest_yaml = yaml.dump(
            manifest.model_dump(mode="json", exclude_none=True),
            allow_unicode=True, default_flow_style=False,
        )
        issues_text = "\n".join(
            f"- [{i.get('level','?')}] {i.get('field','')}: {i.get('message','')}"
            for i in errors
        )
        template = string.Template(_REPAIR_PROMPT_FILE.read_text(encoding="utf-8"))
        prompt = template.safe_substitute(
            user_goal="修复报错",
            manifest_yaml=manifest_yaml,
            issues_text=issues_text,
        )

        try:
            completion = client.chat_completion(
                [{"role": "user", "content": prompt}],
                thinking_mode=False,
                purpose="step_repair",
            )
            raw = (completion.text or "").strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:]) if len(lines) > 1 else raw
            if raw.endswith("```"):
                raw = raw[:-3].strip()
            repaired = yaml.safe_load(raw)
            if not isinstance(repaired, dict):
                logger.warning("repair returned non-dict, retrying")
                continue
            manifest = StepManifest.model_validate(repaired)
            from generator.c_pipeline.step_linter import lint
            issues = lint(manifest)
            # Convert LintIssue to dict for consistent return
            issues = [{"level": i.level, "field": i.field, "message": i.message} for i in issues]
        except Exception as exc:
            logger.warning("repair round %s failed: %s", round_idx, exc)
            continue

    return manifest, issues


__all__ = ["repair"]
