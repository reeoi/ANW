"""Atomic pipeline task runner — generate → AI review.

This module is the single execution surface used by both the "立即执行一次"
button on the dashboard execution console and the per-slot scheduler trigger
when no approved inventory exists.

Decisions (UI-rebuild plan §three):
- 严格串行: process-global lock, 1 concurrent atomic task.
- generate (Phase 0-6) failure → retry up to 3 times; otherwise status=failed.
- AI review failure: handled inside ``review_story_in_database`` (R2 rerun).
- cancel_requested honoured inside the orchestrator between phases.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config_loader import LoadedConfig
from review_queue.db import (
    initialize_database,
    update_story_status,
)
from review_queue.notification_bus import Severity, bus

logger = logging.getLogger(__name__)


_GENERATE_MAX_ATTEMPTS = 3
_PUBLISH_FAIL_STREAK_THRESHOLD = 3


@dataclass(frozen=True)
class AtomicResult:
    """Outcome of one atomic generate→review run."""

    story_id: int | None
    status: str  # 'approved' | 'failed' | 'cancelled' | 'paused' | 'needs_human' | 'no_story'
    phase: str  # last phase reached: 'generate' | 'review'
    message: str
    duration_seconds: float = 0.0


class AtomicRunnerState:
    """Process-global state for atomic task: busy flag, current phase, streak."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._busy_lock = threading.Lock()
        self._current: dict[str, Any] | None = None

    # -- busy lock ----------------------------------------------------------
    def try_acquire(self) -> bool:
        return self._busy_lock.acquire(blocking=False)

    def release(self) -> None:
        try:
            self._busy_lock.release()
        except RuntimeError:
            pass

    def is_busy(self) -> bool:
        if self._busy_lock.acquire(blocking=False):
            self._busy_lock.release()
            return False
        return True

    # -- current task -------------------------------------------------------
    def set_current(self, story_id: int | None, phase: str) -> None:
        with self._lock:
            self._current = {
                "story_id": story_id,
                "phase": phase,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

    def update_phase(self, phase: str) -> None:
        with self._lock:
            if self._current is not None:
                self._current = {**self._current, "phase": phase}

    def clear_current(self) -> None:
        with self._lock:
            self._current = None

    def get_current(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._current is None else dict(self._current)

    def reset(self) -> None:
        """Reset process-local runner state for tests and fresh manual runs."""
        with self._lock:
            self._current = None
        while self.is_busy():
            self.release()


state = AtomicRunnerState()


# ============================================================================
# generate phase
# ============================================================================


def run_generate_with_retry(
    config: LoadedConfig,
    *,
    story_id: int | None = None,
    overrides: dict[str, Any] | None = None,
    max_attempts: int = _GENERATE_MAX_ATTEMPTS,
) -> tuple[int, str]:
    """Run c_pipeline generate up to ``max_attempts`` times.

    On failure we resume from the failed phase rather than restarting at
    phase_0 — re-running phases 0..N-1 wastes minutes of LLM time and
    overwrites otherwise-good artifacts. ``PipelineError.failed_phase``
    carries the canonical phase identifier used as ``resume_from``.

    Returns ``(story_id, final_status)`` where ``final_status`` is one of
    ``'generated'``, ``'failed'``, ``'cancelled'``.
    """

    from generator.c_pipeline.orchestrator import (
        PipelineCancelledError,
        PipelineError,
        run_pipeline,
    )

    resume_from: str | None = None
    for attempt in range(1, max_attempts + 1):
        state.update_phase(f"generate#{attempt}")
        try:
            run_kwargs: dict[str, Any] = {
                "story_id": story_id,
                "config": config,
                "resume_from": resume_from,
            }
            if overrides is not None:
                run_kwargs["overrides"] = overrides
            result = run_pipeline(**run_kwargs)
            if result.status == "cancelled":
                return int(result.story_id), "cancelled"
            if result.status == "paused_user":
                return int(result.story_id), "paused"
            return int(result.story_id), "generated"
        except PipelineCancelledError as exc:
            logger.info("generate cancelled on attempt %s: %s", attempt, exc)
            sid = story_id if story_id is not None else None
            return int(sid) if sid is not None else 0, "cancelled"
        except PipelineError as exc:
            resume_from = getattr(exc, "failed_phase", None)
            logger.warning(
                "generate attempt %s/%s failed at %s: %s",
                attempt,
                max_attempts,
                resume_from or "phase_0",
                exc,
            )
        except Exception:  # pragma: no cover - defensive
            resume_from = None  # unknown failure → restart from scratch
            logger.exception("generate attempt %s crashed", attempt)

    return (int(story_id) if story_id is not None else 0), "failed"


# ============================================================================
# atomic full task
# ============================================================================


def run_full_atomic_task(
    config: LoadedConfig,
    *,
    story_id: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> AtomicResult:
    """Run the full atomic pipeline: generate → AI review.

    Acquires the global ``AtomicRunnerState`` busy lock for its entire
    duration. When the lock is already held (concurrent invocation), returns
    immediately with status='busy'.
    """

    if not state.try_acquire():
        return AtomicResult(
            story_id=story_id,
            status="busy",
            phase="busy",
            message="另一个原子任务正在运行",
        )

    started = time.monotonic()
    db_path = initialize_database(config)
    try:
        state.set_current(story_id, "generate")

        # ---------------- generate ----------------
        generate_kwargs: dict[str, Any] = {"story_id": story_id}
        if overrides is not None:
            generate_kwargs["overrides"] = overrides
        sid, gen_status = run_generate_with_retry(config, **generate_kwargs)
        if gen_status == "cancelled":
            return AtomicResult(
                story_id=sid or None,
                status="cancelled",
                phase="generate",
                message="生成阶段被取消",
                duration_seconds=round(time.monotonic() - started, 3),
            )
        if gen_status == "paused":
            return AtomicResult(
                story_id=sid or None,
                status="paused",
                phase="generate",
                message="短篇流程已按阶段控制暂停，确认产物后可继续运行。",
                duration_seconds=round(time.monotonic() - started, 3),
            )
        if gen_status != "generated":
            bus.publish(
                Severity.WARNING,
                "生成失败",
                f"作品 #{sid} 连续 {_GENERATE_MAX_ATTEMPTS} 次生成失败",
                source="atomic.generate",
                story_id=sid or None,
            )
            return AtomicResult(
                story_id=sid or None,
                status="failed",
                phase="generate",
                message=f"生成失败（{_GENERATE_MAX_ATTEMPTS} 次重试均失败）",
                duration_seconds=round(time.monotonic() - started, 3),
            )

        # ---------------- AI review ----------------
        state.update_phase("review")
        from review_queue.ai_review import review_story_in_database

        # Honour cancel between phases.
        from review_queue.db import is_cancel_requested

        if is_cancel_requested(db_path, sid):
            update_story_status(db_path, sid, "cancelled", summary="用户取消执行。")
            return AtomicResult(
                story_id=sid,
                status="cancelled",
                phase="review",
                message="审核前被取消",
                duration_seconds=round(time.monotonic() - started, 3),
            )

        from review_queue.db import update_story_phase

        update_story_phase(db_path, sid, "phase_7_running")
        review_summary = review_story_in_database(db_path, sid, config=config)
        if review_summary.decision != "approved":
            update_story_phase(db_path, sid, "phase_7_needs_human")
            return AtomicResult(
                story_id=sid,
                status="needs_human",
                phase="review",
                message=f"AI 审核未通过 → needs_human (score={review_summary.final_score})",
                duration_seconds=round(time.monotonic() - started, 3),
            )
        update_story_phase(db_path, sid, "phase_7_done")

        # ---------------- pipeline stops here (Q6=B 决策) ----------------
        # 流水线在审核通过后停止；后续分发不属于 ANW。
        # The pipeline stops after review; distribution/export is outside ANW.
        # 内容意外上线 + 让用户在每篇前显式审核。
        return AtomicResult(
            story_id=sid,
            status="approved",
            phase="review",
            message=f"AI 审核通过 (score={review_summary.final_score})，等待人工复核",
            duration_seconds=round(time.monotonic() - started, 3),
        )

    finally:
        state.clear_current()
        state.release()


def kick_off_async(
    config: LoadedConfig,
    story_id: int | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> int | None:
    """Start one atomic generate/review task in a daemon thread.

    The dashboard calls this from ``POST /api/console/run-now`` and expects a
    quick response while the long-running pipeline continues in the background.
    """

    if state.is_busy():
        raise RuntimeError("Another atomic task is already running.")

    def _target() -> None:
        try:
            task_kwargs: dict[str, Any] = {"story_id": story_id}
            if overrides is not None:
                task_kwargs["overrides"] = overrides
            result = run_full_atomic_task(config, **task_kwargs)
            logger.info(
                "Async atomic task finished: story_id=%s status=%s phase=%s",
                result.story_id,
                result.status,
                result.phase,
            )
        except Exception:
            logger.exception("Async atomic task crashed")

    thread = threading.Thread(target=_target, daemon=True, name="anw-atomic-runner")
    thread.start()
    return story_id


__all__ = [
    "AtomicResult",
    "AtomicRunnerState",
    "kick_off_async",
    "run_full_atomic_task",
    "run_generate_with_retry",
    "state",
]
