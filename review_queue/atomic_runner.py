"""Atomic pipeline task runner — generate → AI review.

This module is the single execution surface used by both the "立即执行一次"
button on the dashboard execution console and the per-slot scheduler trigger
when no approved inventory exists.

Decisions (UI-rebuild plan §three):
- 严格串行: process-global lock, 1 concurrent atomic task.
- generate (Phase 0-6) failure → retry up to 3 times; otherwise status=failed.
- AI review failure: handled inside ``review_story_in_database`` (R2 rerun).
- publish failure: NOT retried this slot; story remains approved; next slot
  retries it. After 3 consecutive publish failures across any slot, the most
  recent story is marked needs_human and the scheduler is paused (red banner).
- cancel_requested honoured inside the orchestrator between phases.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from config_loader import LoadedConfig
from review_queue.db import (
    get_story,
    initialize_database,
    update_story_status,
)
from review_queue.models import Story
from review_queue.notification_bus import Severity, bus

logger = logging.getLogger(__name__)


_GENERATE_MAX_ATTEMPTS = 3
_PUBLISH_FAIL_STREAK_THRESHOLD = 3


@dataclass(frozen=True)
class AtomicResult:
    """Outcome of one atomic generate→review→publish run."""

    story_id: int | None
    status: str  # 'published' | 'failed' | 'cancelled' | 'paused' | 'needs_human' | 'no_story'
    phase: str  # last phase reached: 'generate' | 'review' | 'publish'
    message: str
    publish_status: str | None = None
    duration_seconds: float = 0.0


class AtomicRunnerState:
    """Process-global state for atomic task: busy flag, current phase, streak."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._busy_lock = threading.Lock()
        self._current: dict[str, Any] | None = None
        self._publish_fail_streak = 0

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

    # -- publish fail streak (per-process, scheduler-only) -------------------
    def get_publish_fail_streak(self) -> int:
        with self._lock:
            return int(self._publish_fail_streak)

    def increment_publish_fail_streak(self) -> int:
        with self._lock:
            self._publish_fail_streak += 1
            return int(self._publish_fail_streak)

    def reset_publish_fail_streak(self) -> None:
        with self._lock:
            self._publish_fail_streak = 0

    def reset(self) -> None:
        """Reset process-local runner state for tests and fresh manual runs."""
        with self._lock:
            self._current = None
            self._publish_fail_streak = 0
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
            result = run_pipeline(
                story_id=story_id,
                config=config,
                resume_from=resume_from,
            )
            if result.status == "cancelled":
                return int(result.story_id), "cancelled"
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
    on_publish_fail_streak_exceeded: Callable[[int], None] | None = None,
) -> AtomicResult:
    """Run the full atomic pipeline: generate → AI review → publish.

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
        sid, gen_status = run_generate_with_retry(config, story_id=story_id)
        if gen_status == "cancelled":
            return AtomicResult(
                story_id=sid or None,
                status="cancelled",
                phase="generate",
                message="生成阶段被取消",
                duration_seconds=round(time.monotonic() - started, 3),
            )
        if gen_status != "generated":
            bus.publish(
                Severity.WARNING,
                "⚠️ 生成失败",
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

        review_summary = review_story_in_database(db_path, sid, config=config)
        if review_summary.decision != "approved":
            return AtomicResult(
                story_id=sid,
                status="needs_human",
                phase="review",
                message=f"AI 审核未通过 → needs_human (score={review_summary.final_score})",
                duration_seconds=round(time.monotonic() - started, 3),
            )

        # ---------------- pipeline stops here (Q6=B 决策) ----------------
        # 流水线在审核通过后停止；发布永远等用户在 UI 中点"立即发布"。
        # 旧版本会自动调 ``_publish_one``，已在 v2.0 移除——避免未把关
        # 内容意外上线 + 让用户在每篇前显式审核。
        return AtomicResult(
            story_id=sid,
            status="approved",
            phase="review",
            message=f"AI 审核通过 (score={review_summary.final_score})，等待人工触发发布",
            duration_seconds=round(time.monotonic() - started, 3),
        )

    finally:
        state.clear_current()
        state.release()


def kick_off_async(config: LoadedConfig, story_id: int | None = None) -> int | None:
    """Start one atomic generate/review task in a daemon thread.

    The dashboard calls this from ``POST /api/console/run-now`` and expects a
    quick response while the long-running pipeline continues in the background.
    """

    if state.is_busy():
        raise RuntimeError("Another atomic task is already running.")

    def _target() -> None:
        try:
            result = run_full_atomic_task(config, story_id=story_id)
            logger.info(
                "Async atomic task finished: story_id=%s status=%s phase=%s",
                result.story_id,
                result.status,
                result.phase,
            )
        except Exception:
            logger.exception("Async atomic task crashed")

    thread = threading.Thread(target=_target, daemon=True, name="anp-atomic-runner")
    thread.start()
    return story_id


def _publish_one(config: LoadedConfig, story: Story) -> Any:
    """Publish a single story through the configured platform adapter."""

    from publisher.fansq import FansqPublisher

    publisher = FansqPublisher(config)
    return publisher.publish_story(story)


def _publish_result_status(raw_status: Any) -> str:
    value = str(raw_status or "").lower()
    if value == "published" or value.endswith(".published"):
        return "published"
    if value in {"publish_paused", "paused"} or value.endswith(".paused"):
        return "paused"
    return "failed"


def run_publish_only(config: LoadedConfig, story_id: int) -> AtomicResult:
    """Publish one already-approved story.

    This is the manual publish path used after the generate/review pipeline has
    stopped at ``approved`` for human verification.
    """

    if not state.try_acquire():
        return AtomicResult(
            story_id=story_id,
            status="busy",
            phase="busy",
            message="Another atomic task is already running.",
        )

    started = time.monotonic()
    db_path = initialize_database(config)
    try:
        state.set_current(story_id, "publish")
        story = get_story(db_path, story_id)
        if story is None:
            return AtomicResult(
                story_id=story_id,
                status="failed",
                phase="publish",
                message=f"Story #{story_id} not found.",
                duration_seconds=round(time.monotonic() - started, 3),
            )
        if story.status != "approved":
            return AtomicResult(
                story_id=story_id,
                status="failed",
                phase="publish",
                message=f"Only approved stories can be published; current status is {story.status}.",
                duration_seconds=round(time.monotonic() - started, 3),
            )

        result = _publish_one(config, story)
        status = _publish_result_status(getattr(result, "status", None))
        raw_status = str(getattr(result, "status", status))
        message = str(getattr(result, "message", "") or status)
        should_update = bool(getattr(result, "should_update_status", True))
        if should_update:
            if status == "published":
                update_story_status(db_path, story_id, "published", summary=message)
                state.reset_publish_fail_streak()
            elif status == "paused":
                update_story_status(db_path, story_id, "publish_paused", summary=message)
            else:
                update_story_status(db_path, story_id, "publish_failed", summary=message)
                state.increment_publish_fail_streak()

        return AtomicResult(
            story_id=story_id,
            status=status,
            phase="publish",
            message=message,
            publish_status=raw_status,
            duration_seconds=round(time.monotonic() - started, 3),
        )
    except Exception as exc:
        logger.exception("manual publish failed: story_id=%s", story_id)
        update_story_status(db_path, story_id, "publish_failed", summary=str(exc))
        return AtomicResult(
            story_id=story_id,
            status="failed",
            phase="publish",
            message=f"Publish failed: {exc.__class__.__name__}: {exc}",
            duration_seconds=round(time.monotonic() - started, 3),
        )
    finally:
        state.clear_current()
        state.release()




__all__ = [
    "AtomicResult",
    "AtomicRunnerState",
    "kick_off_async",
    "run_full_atomic_task",
    "run_generate_with_retry",
    "run_publish_only",
    "state",
]
