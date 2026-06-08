"""K2 concurrency primitive — at most ``max_concurrent_pipelines`` parallel
pipeline runs (decision #32). Phases inside one pipeline are always serial,
so the only contention point is between independent ``run_pipeline`` calls
(spawned by the orchestrator's batch entry, scheduler slots, or the local
Web UI).

This module deliberately uses a process-local ``threading.BoundedSemaphore``
rather than an OS-level lock — ANW runs as a single Python process and
SQLite serialisation means cross-process pipelines are not a target. If a
future deployment splits the worker into multiple processes, swap this for
a file-lock or DB-row-lock implementation.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from config_loader import LoadedConfig

logger = logging.getLogger(__name__)


_DEFAULT_MAX_CONCURRENT = 2


@dataclass(frozen=True)
class PipelineSlotStats:
    """Snapshot for monitoring/debugging."""

    max_concurrent: int
    in_use: int
    available: int


class PipelineSemaphore:
    """Process-local semaphore bounding parallel pipeline runs.

    Use as a context manager:

        with semaphore.acquire_slot():
            run_pipeline(story_id)

    ``acquire_slot()`` blocks until a slot frees. Pass ``timeout`` to fail
    fast when the queue is hot. Stats are exposed via ``stats()`` for the
    Web UI.
    """

    def __init__(self, max_concurrent: int = _DEFAULT_MAX_CONCURRENT) -> None:
        if max_concurrent < 1:
            raise ValueError(
                f"max_concurrent must be ≥ 1, got {max_concurrent}"
            )
        self._max = int(max_concurrent)
        self._sem = threading.BoundedSemaphore(self._max)
        self._in_use_lock = threading.Lock()
        self._in_use = 0

    @property
    def max_concurrent(self) -> int:
        return self._max

    @property
    def in_use(self) -> int:
        with self._in_use_lock:
            return self._in_use

    @property
    def available(self) -> int:
        return self._max - self.in_use

    def stats(self) -> PipelineSlotStats:
        with self._in_use_lock:
            return PipelineSlotStats(
                max_concurrent=self._max,
                in_use=self._in_use,
                available=self._max - self._in_use,
            )

    @contextmanager
    def acquire_slot(self, timeout: float | None = None) -> Iterator[None]:
        """Block (or fail) until one of the K2 slots is free."""
        if timeout is None:
            self._sem.acquire()
        else:
            acquired = self._sem.acquire(timeout=timeout)
            if not acquired:
                raise SlotUnavailableError(
                    f"could not acquire pipeline slot within {timeout}s "
                    f"(in_use={self.in_use}/{self._max})"
                )
        with self._in_use_lock:
            self._in_use += 1
        try:
            yield
        finally:
            with self._in_use_lock:
                self._in_use = max(0, self._in_use - 1)
            self._sem.release()


class SlotUnavailableError(RuntimeError):
    """Raised by acquire_slot(timeout=...) when no slot frees in time."""


def make_semaphore_from_config(config: LoadedConfig) -> PipelineSemaphore:
    """Build a ``PipelineSemaphore`` whose size matches
    ``c_pipeline.max_concurrent_pipelines`` (default 2 = decision #32)."""
    section = config.data.get("c_pipeline", {}) or {}
    raw = section.get("max_concurrent_pipelines")
    if raw is None or raw == "":
        n = _DEFAULT_MAX_CONCURRENT
    else:
        n = int(raw)
    return PipelineSemaphore(max_concurrent=n)


# A process-singleton wrapper so all callers share one semaphore instance.
# Tests that need isolation can call ``reset_global_semaphore`` between runs.
_global_lock = threading.Lock()
_global_semaphore: PipelineSemaphore | None = None


def get_global_semaphore(config: LoadedConfig | None = None) -> PipelineSemaphore:
    """Return the process-global semaphore, lazily built from config."""
    global _global_semaphore
    with _global_lock:
        if _global_semaphore is None:
            if config is None:
                _global_semaphore = PipelineSemaphore()
            else:
                _global_semaphore = make_semaphore_from_config(config)
        return _global_semaphore


def reset_global_semaphore() -> None:
    """Drop the cached singleton (used by tests)."""
    global _global_semaphore
    with _global_lock:
        _global_semaphore = None


__all__ = [
    "PipelineSemaphore",
    "PipelineSlotStats",
    "SlotUnavailableError",
    "get_global_semaphore",
    "make_semaphore_from_config",
    "reset_global_semaphore",
]
