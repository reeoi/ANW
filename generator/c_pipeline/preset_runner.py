"""Preset runner — iterate preset steps, dispatch builtin phases and custom actions.

Replaces the orchestrator's hardcoded phase loop with preset-driven execution.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.c_pipeline.actions.base import ActionContext
from generator.c_pipeline.actions.runner import ActionRunner
from generator.c_pipeline.cost_tracker import CostTracker
from generator.c_pipeline.preset_loader import load_preset
from review_queue.db import (
    get_database_path,
    get_story,
    initialize_database,
    update_story_phase,
    update_story_status,
)

logger = logging.getLogger(__name__)

# Map builtin phase ids → orchestrator phase functions (lazy import)
_BUILTIN_DISPATCH: dict[str, str] = {
    "phase_0": "phase0_select.select_theme",
    "phase_1": "phase1_framework.run_framework",
    "phase_2": "phase2_outline.run_outline",
    "phase_3": "phase3_sections.run_sections",
    "phase_4": "phase4_polish.run_polish",
    "phase_5": "phase5_deslop.run_deslop",
    "phase_5_5": "phase5_5_zhuque_loop.run_zhuque_loop",
    "phase_6": "phase6_chapter_title.run_chapter_titling",
}


@dataclass(frozen=True)
class PresetResult:
    """Outcome of one ``run_preset`` call."""

    story_id: int
    work_dir: Path
    final_step: str
    status: str
    final_content_path: Path | None
    char_count: int
    final_title: str
    summary: str
    duration_seconds: float
    warnings: list[str] = field(default_factory=list)


def _resolve_phase_control(config: LoadedConfig, step_id: str) -> str:
    controls = (config.data.get("c_pipeline") or {}).get("phase_controls") or {}
    return str(controls.get(step_id, "auto"))


def run_preset(
    story_id: int,
    *,
    preset_name: str = "default",
    config: LoadedConfig | None = None,
    client: DeepSeekClient | None = None,
    cost_tracker: CostTracker | None = None,
    work_dir: Path | None = None,
) -> PresetResult:
    """Run a full pipeline defined by a preset on one story."""
    if config is None:
        from config_loader import load_from_environment
        config = load_from_environment()

    db_path = initialize_database(config)
    project_root = Path(config.data.get("runtime", {}).get("project_root", ".")).resolve()
    if not project_root.is_absolute() or str(project_root) == ".":
        project_root = Path(__file__).resolve().parents[2]

    work_dir = Path(work_dir) if work_dir else project_root / "data" / "works" / str(story_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    if client is None:
        client = DeepSeekClient(config)

    # Mark this story as using this preset
    try:
        with __import__("sqlite3").connect(str(db_path)) as conn:
            conn.execute("UPDATE stories SET preset_name = ? WHERE id = ?", (preset_name, story_id))
    except Exception:
        pass

    # Load preset
    preset = load_preset(preset_name)
    steps = preset.get("steps") or []
    if not steps:
        raise RuntimeError(f"预设 '{preset_name}' 没有定义任何步骤")

    story = get_story(db_path, story_id)
    final_title = story.title if story else ""
    summary = story.summary if story else ""
    final_content_path: Path | None = None
    char_count = 0
    warnings: list[str] = []

    started = time.monotonic()
    action_runner = ActionRunner(config=config, client=client)

    for step in steps:
        sid = step.get("id", "")
        stype = step.get("type", "builtin")
        enabled = bool(step.get("enabled", True))
        pause_after = bool(step.get("pause_after", False))

        if not enabled:
            update_story_phase(db_path, story_id, f"{sid}_user_skipped")
            logger.info("preset step %s skipped (disabled)", sid)
            continue

        ctrl = _resolve_phase_control(config, sid)
        if ctrl == "skip":
            update_story_phase(db_path, story_id, f"{sid}_user_skipped")
            logger.info("preset step %s skipped (phase_control)", sid)
            continue

        update_story_phase(db_path, story_id, f"{sid}_running")
        logger.info("preset step %s (%s) started", sid, stype)

        try:
            if stype == "builtin":
                result = _run_builtin_phase(sid, config, work_dir, client, cost_tracker, story_id)
                # Update content tracking from builtin result
                if result:
                    chars = result.get("char_count")
                    if chars:
                        char_count = chars
                    fp = result.get("final_path")
                    if fp:
                        final_content_path = Path(fp) if isinstance(fp, str) else fp
                    if result.get("final_title"):
                        final_title = result["final_title"]
                    if result.get("summary"):
                        summary = result["summary"]
                    if result.get("warnings"):
                        warnings.extend(result["warnings"])
            else:
                # Custom step
                custom_result = _run_custom_step(sid, step, work_dir, action_runner, story, config)
                if custom_result:
                    if custom_result.get("char_count"):
                        char_count = custom_result["char_count"]
                    fp = custom_result.get("final_path")
                    if fp:
                        final_content_path = Path(fp) if isinstance(fp, str) else fp
        except Exception as exc:
            update_story_phase(db_path, story_id, f"failed_at_{sid}")
            update_story_status(db_path, story_id, "failed", summary=f"步骤 {sid} 失败: {exc}")
            logger.exception("preset step %s failed", sid)
            story = get_story(db_path, story_id)
            return PresetResult(
                story_id=story_id, work_dir=work_dir, final_step=f"failed_at_{sid}",
                status="failed", final_content_path=final_content_path,
                char_count=char_count, final_title=final_title, summary=summary,
                duration_seconds=round(time.monotonic() - started, 3),
                warnings=warnings + [f"{sid} 失败: {exc}"],
            )

        # Check phase_controls pause_after
        effective_pause = pause_after or ctrl == "pause_after"
        if effective_pause:
            update_story_status(db_path, story_id, "paused_user", summary=f"步骤 {sid} 完成后暂停（用户设置）")
            logger.info("preset step %s paused by user setting", sid)
            story = get_story(db_path, story_id)
            return PresetResult(
                story_id=story_id, work_dir=work_dir, final_step=f"{sid}_user_paused",
                status="paused_user", final_content_path=final_content_path,
                char_count=char_count, final_title=final_title, summary=summary,
                duration_seconds=round(time.monotonic() - started, 3),
                warnings=warnings + [f"步骤 {sid} 暂停"],
            )

        update_story_phase(db_path, story_id, f"{sid}_done")

    # All steps completed
    update_story_phase(db_path, story_id, "complete")
    update_story_status(db_path, story_id, "approved", summary="preset pipeline complete")
    story = get_story(db_path, story_id)
    return PresetResult(
        story_id=story_id, work_dir=work_dir, final_step="complete",
        status="approved", final_content_path=final_content_path,
        char_count=char_count, final_title=final_title, summary=summary,
        duration_seconds=round(time.monotonic() - started, 3),
        warnings=warnings,
    )


def _run_builtin_phase(
    phase: str,
    config: LoadedConfig,
    work_dir: Path,
    client: DeepSeekClient,
    cost_tracker: CostTracker | None,
    story_id: int,
) -> dict[str, Any] | None:
    """Invoke a built-in orchestrator phase function."""
    import importlib

    module_path, func_name = _BUILTIN_DISPATCH[phase].rsplit(".", 1)
    module = importlib.import_module(f"generator.c_pipeline.{module_path}")
    fn = getattr(module, func_name)

    # Each builtin function has slightly different signatures. Adapt:
    kwargs: dict[str, Any] = {"config": config, "work_dir": work_dir, "client": client}
    if cost_tracker is not None:
        kwargs["cost_tracker"] = cost_tracker

    result = fn(**kwargs)

    # Normalize result to dict (all builtin results are dataclasses)
    out: dict[str, Any] = {}
    if hasattr(result, "char_count"):
        out["char_count"] = result.char_count
    if hasattr(result, "final_path"):
        out["final_path"] = result.final_path
    if hasattr(result, "final_md"):
        out["final_md"] = result.final_md
    if hasattr(result, "final_title"):
        out["final_title"] = result.final_title
    if hasattr(result, "summary"):
        out["summary"] = result.summary
    if hasattr(result, "warnings"):
        out["warnings"] = list(result.warnings)
    return out


def _run_custom_step(
    step_id: str,
    step: dict[str, Any],
    work_dir: Path,
    runner: ActionRunner,
    story: Any,
    config: LoadedConfig,
) -> dict[str, Any] | None:
    """Execute a custom step's action chain."""
    from generator.c_pipeline.validators import count_chinese_chars

    # Build variable context
    ctx = ActionContext()
    ctx.set("work_dir", str(work_dir))
    ctx.set("story_title", getattr(story, "title", "") or "")
    ctx.set("story_summary", getattr(story, "summary", "") or "")
    ctx.set("step_output_file", str(work_dir / (step.get("output") or f"{step_id}_output.md")))

    # Read previous output if available
    prev_output_path = step.get("input") or ""
    if prev_output_path and not prev_output_path.startswith("{"):
        p = Path(prev_output_path) if Path(prev_output_path).is_absolute() else work_dir / prev_output_path
    else:
        # Try to find latest artifact from work_dir
        md_files = sorted(work_dir.glob("*.md"))
        p = md_files[-1] if md_files else None
    if p and p.exists():
        ctx.set("prev_output", p.read_text(encoding="utf-8"))

    # Run actions
    result = runner.run(step.get("actions") or [], ctx)

    # Write output file
    output_text = ctx.get("response_text") or ctx.get("prev_output") or ""
    output_path = Path(ctx.resolve_v2(ctx.get("step_output_file", str(work_dir / f"{step_id}_output.md"))))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(str(output_text), encoding="utf-8")

    return {
        "char_count": count_chinese_chars(str(output_text)),
        "final_path": output_path,
        "warnings": [] if result.ok else [result.message],
    }


__all__ = ["PresetResult", "run_preset", "_BUILTIN_DISPATCH"]
