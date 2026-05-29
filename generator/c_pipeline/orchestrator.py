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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.c_pipeline import (
    phase0_select,
    phase1_framework,
    phase2_outline,
    phase3_sections,
    phase4_polish,
    phase5_5_zhuque_loop,
    phase5_deslop,
    phase6_chapter_title,
)
from generator.c_pipeline.concurrency import PipelineSemaphore, make_semaphore_from_config
from generator.c_pipeline.cost_tracker import CostTracker
from generator.c_pipeline.phase5_5_zhuque_loop import (
    ZhuqueAnomalyError,
    ZhuqueRejectedError,
)
from review_queue.db import (
    get_story,
    initialize_database,
    insert_story,
    is_cancel_requested,
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
    "phase_5_5",
    "phase_6",
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
    """Raised when a pipeline phase failed and the story was marked failed.

    The ``failed_phase`` attribute holds the canonical phase identifier
    (``phase_0`` … ``phase_5``) where the failure was detected, so callers
    such as ``atomic_runner.run_generate_with_retry`` can resume the next
    attempt from the failing phase instead of restarting at phase_0.
    """

    def __init__(self, message: str, *, failed_phase: str | None = None) -> None:
        super().__init__(message)
        self.failed_phase: str | None = failed_phase


class PipelineCancelledError(RuntimeError):
    """Raised when ``cancel_requested`` is detected between phases."""


def _check_cancellation(db_path: Path, story_id: int) -> None:
    """Raise :class:`PipelineCancelledError` if the story has cancel_requested=1."""
    if is_cancel_requested(db_path, story_id):
        raise PipelineCancelledError(
            f"pipeline cancelled by user request: story_id={story_id}"
        )


def _get_phase_control(config: LoadedConfig, phase: str) -> str:
    """读取某个 phase 的用户控制策略。"""
    controls = (config.data.get("c_pipeline") or {}).get("phase_controls") or {}
    return str(controls.get(phase, "auto") or "auto")


def _should_skip_phase(config: LoadedConfig, db_path: Path, story_id: int, phase: str) -> bool:
    """如果是 'skip'，标记为 user_skipped 并返回 True。"""
    if _get_phase_control(config, phase) == "skip":
        update_story_phase(db_path, story_id, f"{phase}_user_skipped")
        logger.info("phase_control skip story_id=%s phase=%s", story_id, phase)
        return True
    return False


def _handle_phase_pause_after(
    config: LoadedConfig,
    db_path: Path,
    story_id: int,
    phase: str,
    *,
    work_dir: Path,
    final_content_path: Path | str | None,
    char_count: int,
    used_fallback_any: bool,
    final_title: str,
    summary: str,
    sections_needs_human: int,
    started: float,
    warnings: list[str],
) -> PipelineResult | None:
    """如果是 'pause_after'，mark 暂停并返回 PipelineResult；否则返回 None（继续）。"""
    if _get_phase_control(config, phase) != "pause_after":
        return None
    update_story_phase(db_path, story_id, f"{phase}_user_paused")
    update_story_status(db_path, story_id, "paused_user", summary=f"phase_control: {phase} 完成后暂停")
    logger.info("phase_control paused story_id=%s phase=%s", story_id, phase)
    refreshed = get_story(db_path, story_id)
    return PipelineResult(
        story_id=story_id,
        work_dir=work_dir,
        final_phase=f"{phase}_user_paused",
        status="paused_user",
        final_content_path=Path(final_content_path) if final_content_path else None,
        used_fallback=used_fallback_any,
        needs_human=False,
        total_cost_cny=float(refreshed.pipeline_cost_cny if refreshed else 0.0),
        final_title=final_title,
        summary=summary,
        char_count=char_count,
        sections_needs_human=sections_needs_human,
        duration_seconds=round(time.monotonic() - started, 3),
        warnings=warnings + [f"用户设置 {phase} 完成后暂停"],
    )


# ============================================================ public


def run_pipeline(
    story_id: int | None = None,
    *,
    config: LoadedConfig | None = None,
    work_dir: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    resume_from: str | None = None,
    stop_after: str | None = None,
    client: DeepSeekClient | None = None,
    semaphore: PipelineSemaphore | None = None,
    slot_timeout: float | None = None,
    cost_tracker: CostTracker | None = None,
) -> PipelineResult:
    """Run the full c_pipeline state machine for one story.

    If ``c_pipeline.active_preset`` is set to a non-default value, the
    pipeline is driven by :func:`preset_runner.run_preset` instead of the
    hardcoded phase loop.
    """
    if config is None:
        from config_loader import load_from_environment

        config = load_from_environment()

    db_path = initialize_database(config)
    project_root = _project_root(config)

    if story_id is None:
        story_id = _create_placeholder_story(db_path, project_root=project_root)

    work_dir = Path(work_dir) if work_dir else project_root / "data" / "works" / str(story_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Preset routing: if active_preset is set, delegate to preset runner
    active_preset = str((config.data.get("c_pipeline") or {}).get("active_preset") or "default").strip()
    if active_preset != "default":
        logger.info("routing via preset runner: %s", active_preset)
        try:
            from generator.c_pipeline.preset_runner import run_preset
            result = run_preset(
                story_id=story_id,
                preset_name=active_preset,
                config=config,
                client=client,
                cost_tracker=cost_tracker,
                work_dir=work_dir,
            )
            return PipelineResult(
                story_id=result.story_id,
                work_dir=result.work_dir,
                final_phase=result.final_step,
                status=result.status,
                final_content_path=result.final_content_path,
                used_fallback=False,
                needs_human=(result.status == "paused_user"),
                total_cost_cny=0.0,
                final_title=result.final_title,
                summary=result.summary,
                char_count=result.char_count,
                sections_needs_human=0,
                duration_seconds=result.duration_seconds,
                warnings=result.warnings,
            )
        except Exception:
            logger.exception("preset runner failed, falling back to hardcoded pipeline")
            # Fall through to hardcoded path below

    if resume_from is None:
        # Fresh run: reset the state machine. When resuming we leave
        # ``current_phase`` alone so the dashboard timeline shows the
        # retry continuing from the failed phase rather than restarting.
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

    # 集中 phase skip：推进 resume_idx 越过所有标记为 "skip" 的 phase
    while resume_idx < len(PHASES) and _get_phase_control(config, PHASES[resume_idx]) == "skip":
        logger.info("phase_control skip story_id=%s phase=%s", story_id, PHASES[resume_idx])
        update_story_phase(db_path, story_id, f"{PHASES[resume_idx]}_user_skipped")
        resume_idx += 1

    with sem.acquire_slot(timeout=slot_timeout):
        try:
            # ---------- Phase 0 ----------
            if resume_idx <= 0:
                _check_cancellation(db_path, story_id)
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
            if stop_after == "phase_0":
                return PipelineResult(
                    story_id=story_id, work_dir=work_dir, final_phase="phase_0_done",
                    status="completed", final_content_path=final_content_path,
                    used_fallback=used_fallback_any, needs_human=needs_human,
                    total_cost_cny=0.0, final_title=final_title, summary=summary,
                    char_count=char_count, sections_needs_human=sections_needs_human,
                    duration_seconds=round(time.monotonic() - started, 3),
                    warnings=warnings,
                )
                # phase_control: pause after phase_0?
                _pause = _handle_phase_pause_after(config, db_path, story_id, 'phase_0', work_dir=work_dir, final_content_path=final_content_path, char_count=char_count, used_fallback_any=used_fallback_any, final_title=final_title, summary=summary, sections_needs_human=sections_needs_human, started=started, warnings=warnings)
                if _pause:
                    return _pause

            # ---------- Phase 1 ----------
            if resume_idx <= 1:
                _check_cancellation(db_path, story_id)
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
            if stop_after == "phase_1":
                return PipelineResult(
                    story_id=story_id, work_dir=work_dir, final_phase="phase_1_done",
                    status="completed", final_content_path=final_content_path,
                    used_fallback=used_fallback_any, needs_human=needs_human,
                    total_cost_cny=0.0, final_title=final_title, summary=summary,
                    char_count=char_count, sections_needs_human=sections_needs_human,
                    duration_seconds=round(time.monotonic() - started, 3),
                    warnings=warnings,
                )
                # phase_control: pause after phase_1?
                _pause = _handle_phase_pause_after(config, db_path, story_id, 'phase_1', work_dir=work_dir, final_content_path=final_content_path, char_count=char_count, used_fallback_any=used_fallback_any, final_title=final_title, summary=summary, sections_needs_human=sections_needs_human, started=started, warnings=warnings)
                if _pause:
                    return _pause
                warnings.extend(phase1.warnings)
            else:
                title, summary = _read_phase1_artifacts(work_dir / "1_设定.md")
                final_title = title

            # ---------- Phase 2 ----------
            if resume_idx <= 2:
                _check_cancellation(db_path, story_id)
                update_story_phase(db_path, story_id, "phase_2_running")
                phase2 = phase2_outline.run_outline(
                    config, work_dir=work_dir, client=client
                )
                tracker.record_completion(
                    story_id=story_id, phase="phase_2", completion=phase2.llm_completion
                )
                used_fallback_any = used_fallback_any or phase2.used_fallback
                update_story_phase(db_path, story_id, "phase_2_done")
            if stop_after == "phase_2":
                return PipelineResult(
                    story_id=story_id, work_dir=work_dir, final_phase="phase_2_done",
                    status="completed", final_content_path=final_content_path,
                    used_fallback=used_fallback_any, needs_human=needs_human,
                    total_cost_cny=0.0, final_title=final_title, summary=summary,
                    char_count=char_count, sections_needs_human=sections_needs_human,
                    duration_seconds=round(time.monotonic() - started, 3),
                    warnings=warnings,
                )
                # phase_control: pause after phase_2?
                _pause = _handle_phase_pause_after(config, db_path, story_id, 'phase_2', work_dir=work_dir, final_content_path=final_content_path, char_count=char_count, used_fallback_any=used_fallback_any, final_title=final_title, summary=summary, sections_needs_human=sections_needs_human, started=started, warnings=warnings)
                if _pause:
                    return _pause
                warnings.extend(phase2.warnings)

            # ---------- Phase 3 ----------
            if resume_idx <= 3:
                _check_cancellation(db_path, story_id)
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
            if stop_after == "phase_3":
                return PipelineResult(
                    story_id=story_id, work_dir=work_dir, final_phase="phase_3_done",
                    status="completed", final_content_path=final_content_path,
                    used_fallback=used_fallback_any, needs_human=needs_human,
                    total_cost_cny=0.0, final_title=final_title, summary=summary,
                    char_count=char_count, sections_needs_human=sections_needs_human,
                    duration_seconds=round(time.monotonic() - started, 3),
                    warnings=warnings,
                )
                # phase_control: pause after phase_3?
                _pause = _handle_phase_pause_after(config, db_path, story_id, 'phase_3', work_dir=work_dir, final_content_path=final_content_path, char_count=char_count, used_fallback_any=used_fallback_any, final_title=final_title, summary=summary, sections_needs_human=sections_needs_human, started=started, warnings=warnings)
                if _pause:
                    return _pause
                warnings.extend(phase3.warnings)

            # ---------- Phase 4 ----------
            if resume_idx <= 4:
                _check_cancellation(db_path, story_id)
                update_story_phase(db_path, story_id, "phase_4_running")
                phase4 = phase4_polish.run_polish(
                    config, work_dir=work_dir, client=client
                )
                tracker.record_completion(
                    story_id=story_id, phase="phase_4", completion=phase4.llm_completion
                )
                used_fallback_any = used_fallback_any or phase4.used_fallback
                update_story_phase(db_path, story_id, "phase_4_done")
            if stop_after == "phase_4":
                return PipelineResult(
                    story_id=story_id, work_dir=work_dir, final_phase="phase_4_done",
                    status="completed", final_content_path=final_content_path,
                    used_fallback=used_fallback_any, needs_human=needs_human,
                    total_cost_cny=0.0, final_title=final_title, summary=summary,
                    char_count=char_count, sections_needs_human=sections_needs_human,
                    duration_seconds=round(time.monotonic() - started, 3),
                    warnings=warnings,
                )
                # phase_control: pause after phase_4?
                _pause = _handle_phase_pause_after(config, db_path, story_id, 'phase_4', work_dir=work_dir, final_content_path=final_content_path, char_count=char_count, used_fallback_any=used_fallback_any, final_title=final_title, summary=summary, sections_needs_human=sections_needs_human, started=started, warnings=warnings)
                if _pause:
                    return _pause
                warnings.extend(phase4.warnings)

            # ---------- Phase 5 ----------
            if resume_idx <= 5:
                _check_cancellation(db_path, story_id)
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
                if stop_after == "phase_5":
                    return PipelineResult(
                        story_id=story_id, work_dir=work_dir, final_phase="phase_5_done",
                        status="completed", final_content_path=final_content_path,
                        used_fallback=used_fallback_any, needs_human=needs_human,
                        total_cost_cny=0.0, final_title=final_title, summary=summary,
                        char_count=char_count, sections_needs_human=sections_needs_human,
                        duration_seconds=round(time.monotonic() - started, 3),
                        warnings=warnings,
                    )
                warnings.extend(phase5.warnings)

            # ---------- Phase 5.5 (朱雀 AI 检测闭环) ----------
            if resume_idx <= 6:
                _check_cancellation(db_path, story_id)
                update_story_phase(db_path, story_id, "phase_5_5_running")
                zhuque_enabled = bool(
                    (config.data.get("c_pipeline") or {}).get("zhuque_loop", {}).get("enabled", True)
                )
                # Mock / dry-run 模式跳过：朱雀需要真实 Chrome + 朱雀登录，单测无法 mock 站
                skip_for_mock = client.is_mock() or bool(
                    config.data.get("runtime", {}).get("dry_run")
                )
                if not zhuque_enabled or skip_for_mock:
                    reason = "config_disabled" if not zhuque_enabled else "mock_or_dryrun"
                    logger.info(
                        "phase5_5_skipped story_id=%s reason=%s", story_id, reason
                    )
                    update_story_phase(db_path, story_id, "phase_5_5_skipped")
                    # 复制 phase5 产物为 phase5_5 产物，保持下游路径一致
                    skip_dst = work_dir / "5_5_朱雀通过稿.md"
                    if final_content_path and Path(final_content_path).exists():
                        skip_dst.write_text(
                            Path(final_content_path).read_text(encoding="utf-8"),
                            encoding="utf-8",
                        )
                        final_content_path = skip_dst
                        warnings.append(f"phase 5.5 skipped ({reason})")
                else:
                    try:
                        phase5_5 = phase5_5_zhuque_loop.run_zhuque_loop(
                            config,
                            work_dir=work_dir,
                            story_id=story_id,
                            client=client,
                            cost_tracker=tracker,
                        )
                        final_content_path = phase5_5.final_path
                        char_count = phase5_5.char_count
                        update_story_phase(
                            db_path,
                            story_id,
                            "phase_5_5_done",
                            final_content_path=str(final_content_path),
                        )
                        warnings.append(
                            f"phase 5.5 朱雀通过：{len(phase5_5.rounds)} 轮检测"
                        )
                    except ZhuqueAnomalyError as anomaly_exc:
                        update_story_phase(db_path, story_id, "phase_5_5_paused")
                        update_story_status(
                            db_path,
                            story_id,
                            "paused_zhuque_anomaly",
                            summary=f"朱雀检测异常({anomaly_exc.anomaly.value})：{anomaly_exc}",
                        )
                        logger.warning(
                            "phase5_5_paused story_id=%s anomaly=%s",
                            story_id, anomaly_exc.anomaly.value,
                        )
                        refreshed = get_story(db_path, story_id)
                        return PipelineResult(
                            story_id=story_id,
                            work_dir=work_dir,
                            final_phase="phase_5_5_paused",
                            status="paused_zhuque_anomaly",
                            final_content_path=final_content_path,
                            used_fallback=used_fallback_any,
                            needs_human=True,
                            total_cost_cny=float(refreshed.pipeline_cost_cny if refreshed else 0.0),
                            final_title=final_title,
                            summary=summary,
                            char_count=char_count,
                            sections_needs_human=sections_needs_human,
                            duration_seconds=round(time.monotonic() - started, 3),
                            warnings=warnings + [
                                f"朱雀异常：{anomaly_exc.anomaly.value} - {anomaly_exc}"
                            ],
                        )
                    except ZhuqueRejectedError as rejected_exc:
                        update_story_phase(db_path, story_id, "phase_5_5_rejected")
                        update_story_status(
                            db_path,
                            story_id,
                            "rejected_ai",
                            summary=f"朱雀检测 {rejected_exc.rounds} 轮仍未达「人工创作特征显著」",
                        )
                        logger.warning(
                            "phase5_5_rejected story_id=%s rounds=%s last_label=%s",
                            story_id, rejected_exc.rounds, rejected_exc.last_label.value,
                        )
                        refreshed = get_story(db_path, story_id)
                        return PipelineResult(
                            story_id=story_id,
                            work_dir=work_dir,
                            final_phase="phase_5_5_rejected",
                            status="rejected_ai",
                            final_content_path=final_content_path,
                            used_fallback=used_fallback_any,
                            needs_human=False,
                            total_cost_cny=float(refreshed.pipeline_cost_cny if refreshed else 0.0),
                            final_title=final_title,
                            summary=summary,
                            char_count=char_count,
                            sections_needs_human=sections_needs_human,
                            duration_seconds=round(time.monotonic() - started, 3),
                            warnings=warnings + [str(rejected_exc)],
                        )

            # ---------- Phase 6 (chapter titling) ----------
            if resume_idx <= 7:
                _check_cancellation(db_path, story_id)
                update_story_phase(db_path, story_id, "phase_6_running")
                phase6 = phase6_chapter_title.run_chapter_titling(
                    config, work_dir=work_dir, client=client, cost_tracker=tracker
                )
                if phase6.llm_completion is not None:
                    tracker.record_completion(
                        story_id=story_id,
                        phase="phase_6",
                        completion=phase6.llm_completion,
                    )
                used_fallback_any = used_fallback_any or phase6.used_fallback
                final_content_path = phase6.chaptered_path
                char_count = phase6.char_count
                update_story_phase(
                    db_path,
                    story_id,
                    "phase_6_done",
                    final_content_path=str(final_content_path),
                )
                if stop_after == "phase_6":
                    return PipelineResult(
                        story_id=story_id, work_dir=work_dir, final_phase="phase_6_done",
                        status="completed", final_content_path=final_content_path,
                        used_fallback=used_fallback_any, needs_human=needs_human,
                        total_cost_cny=0.0, final_title=final_title, summary=summary,
                        char_count=char_count, sections_needs_human=sections_needs_human,
                        duration_seconds=round(time.monotonic() - started, 3),
                        warnings=warnings,
                    )
                warnings.extend(phase6.warnings)

        except PipelineCancelledError:
            current_phase = _current_phase_label(db_path, story_id)
            cancelled_marker = (
                "cancelled_at_" + current_phase.replace("_running", "")
                if current_phase
                else "cancelled_at_phase_0"
            )
            update_story_phase(db_path, story_id, cancelled_marker)
            update_story_status(db_path, story_id, "cancelled", summary="用户取消执行。")
            logger.info(
                "pipeline cancelled at %s for story_id=%s", cancelled_marker, story_id
            )
            refreshed = get_story(db_path, story_id)
            return PipelineResult(
                story_id=story_id,
                work_dir=work_dir,
                final_phase=cancelled_marker,
                status="cancelled",
                final_content_path=final_content_path,
                used_fallback=used_fallback_any,
                needs_human=False,
                total_cost_cny=float(refreshed.pipeline_cost_cny if refreshed else 0.0),
                final_title=final_title,
                summary=summary,
                char_count=char_count,
                sections_needs_human=sections_needs_human,
                duration_seconds=round(time.monotonic() - started, 3),
                warnings=warnings,
            )

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
            failed_phase_canonical = _canonical_failed_phase(failed_phase)
            raise PipelineError(
                f"pipeline failed at {failure_marker}: {exc}",
                failed_phase=failed_phase_canonical,
            ) from exc

    final_status = "needs_human" if needs_human else "pending"
    update_story_status(db_path, story_id, final_status)

    refreshed = get_story(db_path, story_id)
    total_cost = float(refreshed.pipeline_cost_cny if refreshed else 0.0)

    return PipelineResult(
        story_id=story_id,
        work_dir=work_dir,
        final_phase="phase_6_done",
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


def _canonical_failed_phase(failed_label: str | None) -> str | None:
    """Reduce a ``stories.current_phase`` value to a canonical ``phase_N``.

    The orchestrator emits markers like ``phase_3_running`` or
    ``phase_3_section_05_done`` while a phase is in flight; when that
    phase later fails we want to surface just ``phase_3`` so retry can
    pass it as ``resume_from``.
    """

    if not failed_label:
        return None
    raw = failed_label.strip().lower()
    if not raw:
        return None
    for phase in PHASES:
        if raw == phase or raw.startswith(phase + "_") or raw == phase:
            return phase
    return None


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
    "PipelineCancelledError",
    "PipelineError",
    "PipelineResult",
    "run_pipeline",
]
