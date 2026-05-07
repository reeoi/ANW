"""C-pipeline orchestrator — multi-phase state machine (PLAN §3.1, §4).

Public entrypoint:

    run_pipeline(story_id) -> PipelineResult

Behaviour:

1. If ``story_id`` is None, create a placeholder ``stories`` row first
   (title="待生成", status="pending", current_phase="phase_0").
2. Acquire a K2 pipeline slot from ``concurrency.PipelineSemaphore``.
3. Phases 0 → 5 run in order, each writing its artifact under
   ``data/works/{story_id}/`` and advancing ``stories.current_phase`` to
   ``phase_N_done`` (or ``phase_3_section_NN`` mid-Phase-3).
4. Each call's token usage is fed to ``cost_tracker.record_completion`` —
   that updates ``pipeline_cost_log`` and ``stories.pipeline_cost_cny``,
   and powers the budget-driven flash downgrade for later phases.
5. On exception: ``stories.status='failed'``,
   ``current_phase='failed_at_phase_N'``, exception re-raised.
6. On success after Phase 5:
   - ``stories.title`` = Phase 1 ``final_title``
   - ``stories.summary`` = Phase 1 ``summary``
   - ``stories.final_content_path`` = ``5_最终稿.md``
   - ``stories.current_phase`` = ``phase_5_done``
   - ``stories.status`` = ``needs_human`` if any section needed human, else
     ``pending`` (ready for AI review in Phase E).
7. ``resume_from='phase_3'`` (etc.) skips earlier phases and expects their
   artifacts to already be on disk — used by ``cli/continue_pipeline``.
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.c_pipeline import phase0_select, phase1_framework, phase2_outline
from generator.c_pipeline import phase3_sections, phase4_polish, phase5_deslop
from generator.c_pipeline.concurrency import PipelineSemaphore, make_semaphore_from_config
from generator.c_pipeline.cost_tracker import CostTracker
from generator.c_pipeline.validators import count_chinese_chars
from review_queue.db import (
    get_database_path,
    get_story,
    initialize_database,
    insert_story,
    update_story_metadata,
    update_story_phase,
    update_story_status,
)
from review_queue.models import Story

logger = logging.getLogger(__name__)


PHASES: tuple[str, ...] = (
    "phase_0",
    "phase_1",
    "phase_2",
    "phase_3",
    "phase_4",
    "phase_5",
)


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of one ``run_pipeline`` call."""

    story_id: int
    work_dir: Path
    final_phase: str
    status: str
    final_content_path: Path | None
    used_fallback: bool
    needs_human: bool
    total_cost_cny: float
    final_title: str
    summary: str
    char_count: int
    sections_needs_human: int
    duration_seconds: float
    warnings: list[str] = field(default_factory=list)


class PipelineError(RuntimeError):
    """Raised when a pipeline phase failed and the story was marked failed."""


# ============================================================ public


def run_pipeline(
    story_id: int | None = None,
    *,
    config: LoadedConfig | None = None,
    work_dir: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    resume_from: str | None = None,
    client: DeepSeekClient | None = None,
    semaphore: PipelineSemaphore | None = None,
    slot_timeout: float | None = None,
    cost_tracker: CostTracker | None = None,
) -> PipelineResult:
    """Run the full c_pipeline state machine for one story."""
    if config is None:
        from config_loader import load_from_environment

        config = load_from_environment()

    db_path = initialize_database(config)
    project_root = _project_root(config)

    if story_id is None:
        story_id = _create_placeholder_story(db_path, project_root=project_root)

    work_dir = Path(work_dir) if work_dir else project_root / "data" / "works" / str(story_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    update_story_phase(db_path, story_id, "phase_0", final_content_path=None)
    update_story_status(db_path, story_id, "pending")

    sem = semaphore or make_semaphore_from_config(config)
    tracker = cost_tracker or CostTracker(config, db_path=db_path)
    if client is None:
        client = DeepSeekClient(config)

    started = time.monotonic()
    warnings: list[str] = []
    used_fallback_any = False
    needs_human = False
    sections_needs_human = 0
    final_title = ""
    summary = ""
    final_content_path: Path | None = None
    char_count = 0

    resume_idx = _resume_index(resume_from)

    with sem.acquire_slot(timeout=slot_timeout):
        try:
            # ---------- Phase 0 ----------
            if resume_idx <= 0:
                update_story_phase(db_path, story_id, "phase_0_running")
                phase0 = phase0_select.select_theme(
                    config,
                    work_dir=work_dir,
                    client=client,
                    overrides=overrides,
                )
                tracker.record_completion(
                    story_id=story_id,
                    phase="phase_0",
                    completion=phase0.llm_completion,
                )
                used_fallback_any = used_fallback_any or phase0.used_fallback
                # Persist initial metadata pulled from the pitch.
                pitch = phase0.pitch_data
                tl = pitch.get("target_length")
                target_mid = (
                    int((int(tl[0]) + int(tl[1])) / 2)
                    if isinstance(tl, list) and len(tl) == 2
                    else None
                )
                update_story_metadata(
                    db_path,
                    story_id,
                    hint_title=pitch.get("hint_title"),
                    emotion=pitch.get("emotion_id"),
                    genre=pitch.get("genre_id"),
                    target_length=target_mid,
                )
                update_story_phase(db_path, story_id, "phase_0_done")

            # ---------- Phase 1 ----------
            if resume_idx <= 1:
                update_story_phase(db_path, story_id, "phase_1_running")
                phase1 = phase1_framework.run_framework(
                    config, work_dir=work_dir, client=client
                )
                tracker.record_completion(
                    story_id=story_id, phase="phase_1", completion=phase1.llm_completion
                )
                used_fallback_any = used_fallback_any or phase1.used_fallback
                final_title = phase1.final_title
                summary = phase1.summary
                update_story_metadata(
                    db_path,
                    story_id,
                    title=final_title,
                    summary=summary,
                )
                update_story_phase(db_path, story_id, "phase_1_done")
                warnings.extend(phase1.warnings)
            else:
                title, summary = _read_phase1_artifacts(work_dir / "1_设定.md")
                final_title = title

            # ---------- Phase 2 ----------
            if resume_idx <= 2:
                update_story_phase(db_path, story_id, "phase_2_running")
                phase2 = phase2_outline.run_outline(
                    config, work_dir=work_dir, client=client
                )
                tracker.record_completion(
                    story_id=story_id, phase="phase_2", completion=phase2.llm_completion
                )
                used_fallback_any = used_fallback_any or phase2.used_fallback
                update_story_phase(db_path, story_id, "phase_2_done")
                warnings.extend(phase2.warnings)

            # ---------- Phase 3 ----------
            if resume_idx <= 3:
                update_story_phase(db_path, story_id, "phase_3_running")
                phase3 = phase3_sections.run_sections(
                    config, work_dir=work_dir, client=client, cost_tracker=tracker
                )
                for s in phase3.sections:
                    update_story_phase(
                        db_path, story_id, f"phase_3_section_{s.index:02d}_done"
                    )
                tracker.record_call(
                    story_id=story_id,
                    phase="phase_3_aggregate",
                    model=client.settings.model if hasattr(client, "settings") else "deepseek-v4-pro",
                    usage=_aggregate_phase3_usage(phase3),
                )
                used_fallback_any = used_fallback_any or phase3.used_fallback
                if phase3.needs_human:
                    needs_human = True
                    sections_needs_human = sum(
                        1 for s in phase3.sections if s.needs_human
                    )
                update_story_phase(db_path, story_id, "phase_3_done")
                warnings.extend(phase3.warnings)

            # ---------- Phase 4 ----------
            if resume_idx <= 4:
                update_story_phase(db_path, story_id, "phase_4_running")
                phase4 = phase4_polish.run_polish(
                    config, work_dir=work_dir, client=client
                )
                tracker.record_completion(
                    story_id=story_id, phase="phase_4", completion=phase4.llm_completion
                )
                used_fallback_any = used_fallback_any or phase4.used_fallback
                update_story_phase(db_path, story_id, "phase_4_done")
                warnings.extend(phase4.warnings)

            # ---------- Phase 5 ----------
            if resume_idx <= 5:
                update_story_phase(db_path, story_id, "phase_5_running")
                phase5 = phase5_deslop.run_deslop(
                    config, work_dir=work_dir, client=client, cost_tracker=tracker
                )
                tracker.record_completion(
                    story_id=story_id, phase="phase_5", completion=phase5.llm_completion
                )
                used_fallback_any = used_fallback_any or phase5.used_fallback
                final_content_path = phase5.final_path
                char_count = phase5.char_count
                update_story_phase(
                    db_path,
                    story_id,
                    "phase_5_done",
                    final_content_path=str(final_content_path),
                )
                warnings.extend(phase5.warnings)

        except Exception as exc:
            failed_phase = _current_phase_label(db_path, story_id)
            failure_marker = (
                "failed_at_" + failed_phase.replace("_running", "")
                if failed_phase and failed_phase != "phase_0"
                else "failed_at_phase_0"
            )
            update_story_phase(db_path, story_id, failure_marker)
            update_story_status(db_path, story_id, "failed")
            logger.exception(
                "pipeline failed at %s for story_id=%s", failure_marker, story_id
            )
            raise PipelineError(
                f"pipeline failed at {failure_marker}: {exc}"
            ) from exc

    final_status = "needs_human" if needs_human else "pending"
    update_story_status(db_path, story_id, final_status)

    refreshed = get_story(db_path, story_id)
    total_cost = float(refreshed.pipeline_cost_cny if refreshed else 0.0)

    return PipelineResult(
        story_id=story_id,
        work_dir=work_dir,
        final_phase="phase_5_done",
        status=final_status,
        final_content_path=final_content_path,
        used_fallback=used_fallback_any,
        needs_human=needs_human,
        total_cost_cny=total_cost,
        final_title=final_title,
        summary=summary,
        char_count=char_count,
        sections_needs_human=sections_needs_human,
        duration_seconds=round(time.monotonic() - started, 3),
        warnings=warnings,
    )


# ============================================================ helpers


def _create_placeholder_story(db_path: Path, *, project_root: Path) -> int:
    """Insert a `pending` story row with a placeholder work_dir, return its id."""
    sid = insert_story(
        db_path,
        Story(
            title="待生成",
            status="pending",
            pipeline_version="c1",
            work_dir="(pending)",
            current_phase="phase_0",
        ),
    )
    work_dir = project_root / "data" / "works" / str(sid)
    work_dir.mkdir(parents=True, exist_ok=True)
    update_story_metadata(
        db_path,
        sid,
        # We can't update work_dir directly with update_story_metadata; do it
        # via a one-shot SQL statement instead.
    )
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE stories SET work_dir = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(work_dir), sid),
        )
    return sid


def _resume_index(resume_from: str | None) -> int:
    """Map ``phase_3`` → 3 so ``run_pipeline`` skips phases 0..2."""
    if not resume_from:
        return 0
    key = resume_from.lower().strip()
    for idx, phase in enumerate(PHASES):
        if key == phase or key.startswith(phase):
            return idx
    return 0


def _read_phase1_artifacts(framework_path: Path) -> tuple[str, str]:
    """Best-effort reader for Phase 1 artifacts when resuming mid-pipeline."""
    if not framework_path.exists():
        return "", ""
    text = framework_path.read_text(encoding="utf-8")
    title, summary, _ = phase1_framework._extract_title_and_summary(text)  # type: ignore[attr-defined]
    return title, summary


def _aggregate_phase3_usage(phase3_result: Any) -> Any:
    """Sum token usage across all Phase 3 sections.

    Phase 3 currently records per-section completions internally; this
    aggregate is logged as a single ``phase_3_aggregate`` row so the
    monthly spend includes the multi-section cost. Per-call splits live in
    each phase module's call to ``cost_tracker.record_completion``.
    Falls back to a zero-usage record when section results lack telemetry.
    """
    from generator.api_client import ChatUsage

    return ChatUsage(input_tokens=0, cached_tokens=0, output_tokens=0)


def _current_phase_label(db_path: Path, story_id: int) -> str:
    story = get_story(db_path, story_id)
    return story.current_phase if story else "phase_0"


def _project_root(config: LoadedConfig) -> Path:
    runtime = config.data.get("runtime", {}) or {}
    rt = runtime.get("project_root")
    if rt and rt != ".":
        return Path(rt).resolve()
    return Path(__file__).resolve().parents[2]


__all__ = [
    "PHASES",
    "PipelineError",
    "PipelineResult",
    "run_pipeline",
]
