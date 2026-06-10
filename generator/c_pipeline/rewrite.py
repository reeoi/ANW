"""R2 rewrite (decision #31): rerun Phase 4-5 only after AI review fails.

Lighter than ``orchestrator.run_pipeline(resume_from='phase_4')`` — keeps
``stories.status`` / ``current_phase`` ownership inside the AI review
loop instead of resetting to ``phase_0``/``pending``. Reads the existing
``3_正文_合稿.md`` from the story's work_dir, runs ``phase4_polish.run_polish``
then ``phase5_deslop.run_deslop`` in order, updates ``stories.final_content_path``
to the freshly written ``5_最终稿.md``, and tags cost-log rows with
``phase_4_rewrite`` / ``phase_5_rewrite`` so monthly spend can attribute
R2 attempts separately.

Public entrypoint: ``rerun_phase_4_5(story_id, config)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.c_pipeline import phase4_polish, phase5_deslop
from generator.c_pipeline.cost_tracker import CostTracker
from storage.schema import initialize_database
from storage.stories import get_story, update_story_phase

logger = logging.getLogger(__name__)


class RewriteError(RuntimeError):
    """Raised when the R2 rewrite cannot proceed (work_dir missing, ...)."""


@dataclass(frozen=True)
class RewriteResult:
    """Outcome of one ``rerun_phase_4_5`` call."""

    story_id: int
    final_content_path: Path
    char_count: int
    used_fallback: bool
    warnings: list[str] = field(default_factory=list)


def rerun_phase_4_5(
    story_id: int,
    *,
    config: LoadedConfig,
    client: Any | None = None,
    cost_tracker: CostTracker | None = None,
) -> RewriteResult:
    """Decision #31 (R2): re-polish + re-de-slop the existing manuscript.

    Reads ``3_正文_合稿.md`` already on disk under the story's
    ``work_dir``, re-runs Phase 4 (polish) then Phase 5 (deslop), and
    repoints ``stories.final_content_path`` at the new ``5_最终稿.md``.
    Phase 0-3 are NOT re-run (decision #31).
    """

    db_path = initialize_database(config)
    story = get_story(db_path, story_id)
    if story is None:
        raise RewriteError(f"story not found: {story_id}")
    work_dir = Path(story.work_dir or "")
    if not str(story.work_dir or "").strip():
        raise RewriteError(f"story {story_id} has no work_dir set")
    if not work_dir.exists():
        raise RewriteError(f"work_dir does not exist: {work_dir}")
    combined = work_dir / "3_正文_合稿.md"
    if not combined.exists():
        raise RewriteError(
            f"3_正文_合稿.md missing — Phase 3 must have completed before R2 rewrite: {combined}"
        )

    if client is None:
        client = DeepSeekClient(config)
    if cost_tracker is None:
        cost_tracker = CostTracker(config, db_path=db_path)

    warnings: list[str] = []
    used_fallback = False

    update_story_phase(db_path, story_id, "phase_4_rewrite")
    phase4 = phase4_polish.run_polish(config, work_dir=work_dir, client=client)
    cost_tracker.record_completion(
        story_id=story_id,
        phase="phase_4_rewrite",
        completion=phase4.llm_completion,
    )
    used_fallback = used_fallback or phase4.used_fallback
    warnings.extend(phase4.warnings)

    update_story_phase(db_path, story_id, "phase_5_rewrite")
    phase5 = phase5_deslop.run_deslop(
        config, work_dir=work_dir, client=client, cost_tracker=cost_tracker
    )
    cost_tracker.record_completion(
        story_id=story_id,
        phase="phase_5_rewrite",
        completion=phase5.llm_completion,
    )
    used_fallback = used_fallback or phase5.used_fallback
    warnings.extend(phase5.warnings)

    update_story_phase(
        db_path,
        story_id,
        "phase_5_done",
        final_content_path=str(phase5.final_path),
    )
    logger.info(
        "R2 rewrite completed: story_id=%s chars=%s fallback=%s",
        story_id,
        phase5.char_count,
        used_fallback,
    )
    return RewriteResult(
        story_id=story_id,
        final_content_path=phase5.final_path,
        char_count=phase5.char_count,
        used_fallback=used_fallback,
        warnings=warnings,
    )


__all__ = ["RewriteError", "RewriteResult", "rerun_phase_4_5"]
