"""Tests for generator/c_pipeline/concurrency.py (Phase C.9).

Verifies the K2 semaphore (decision #32):
- at most ``max_concurrent_pipelines`` slots active simultaneously
- a 3rd thread blocks until one finishes
- timeout path raises ``SlotUnavailableError`` when slots are busy
- stats() reflects in_use / available correctly
- config-driven sizing reads c_pipeline.max_concurrent_pipelines
- global singleton lifecycle
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.c_pipeline.concurrency import (
    PipelineSemaphore,
    PipelineSlotStats,
    SlotUnavailableError,
    get_global_semaphore,
    make_semaphore_from_config,
    reset_global_semaphore,
)

# ============================================================ size constraints


def test_invalid_size_rejected() -> None:
    with pytest.raises(ValueError):
        PipelineSemaphore(max_concurrent=0)
    with pytest.raises(ValueError):
        PipelineSemaphore(max_concurrent=-1)


def test_default_size_is_two() -> None:
    sem = PipelineSemaphore()
    assert sem.max_concurrent == 2


# ============================================================ basic acquire / release


def test_single_slot_acquire_release() -> None:
    sem = PipelineSemaphore(max_concurrent=1)
    assert sem.in_use == 0
    with sem.acquire_slot():
        assert sem.in_use == 1
        assert sem.available == 0
    assert sem.in_use == 0
    assert sem.available == 1


def test_two_slots_can_coexist() -> None:
    sem = PipelineSemaphore(max_concurrent=2)
    with sem.acquire_slot():
        with sem.acquire_slot():
            assert sem.in_use == 2
            assert sem.available == 0
        assert sem.in_use == 1
    assert sem.in_use == 0


# ============================================================ K2 enforcement


def test_third_acquire_blocks_until_release() -> None:
    """A third acquire_slot must wait for one of the first two to finish."""
    sem = PipelineSemaphore(max_concurrent=2)
    barrier = threading.Barrier(2)
    third_acquired_at: list[float] = []
    second_released_at: list[float] = []

    def first_two() -> None:
        with sem.acquire_slot():
            barrier.wait()  # synchronize start
            time.sleep(0.20)  # hold slot
            second_released_at.append(time.monotonic())

    def third() -> None:
        # wait until first two have started
        time.sleep(0.05)
        with sem.acquire_slot():
            third_acquired_at.append(time.monotonic())

    threads = [
        threading.Thread(target=first_two),
        threading.Thread(target=first_two),
        threading.Thread(target=third),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
    assert all(not t.is_alive() for t in threads)
    assert len(third_acquired_at) == 1
    # Third slot must come AFTER one of the first two releases.
    assert third_acquired_at[0] >= min(second_released_at) - 0.001


def test_timeout_path_raises_when_slots_busy() -> None:
    sem = PipelineSemaphore(max_concurrent=1)
    started = threading.Event()
    release = threading.Event()
    errors: list[str] = []

    def hold_slot() -> None:
        with sem.acquire_slot():
            started.set()
            release.wait(timeout=2.0)

    def try_with_timeout() -> None:
        started.wait(timeout=1.0)
        try:
            with sem.acquire_slot(timeout=0.05):
                errors.append("should not have acquired")
        except SlotUnavailableError as exc:
            errors.append(f"raised: {exc}")

    holder = threading.Thread(target=hold_slot)
    waiter = threading.Thread(target=try_with_timeout)
    holder.start()
    waiter.start()
    waiter.join(timeout=1.0)
    release.set()
    holder.join(timeout=1.0)

    assert errors and "raised" in errors[0]


def test_in_use_returns_to_zero_on_exception() -> None:
    sem = PipelineSemaphore(max_concurrent=1)
    with pytest.raises(RuntimeError):
        with sem.acquire_slot():
            assert sem.in_use == 1
            raise RuntimeError("boom")
    assert sem.in_use == 0
    # Slot must be reclaimable.
    with sem.acquire_slot():
        assert sem.in_use == 1


# ============================================================ stats


def test_stats_reports_current_state() -> None:
    sem = PipelineSemaphore(max_concurrent=3)
    s0 = sem.stats()
    assert isinstance(s0, PipelineSlotStats)
    assert s0.max_concurrent == 3
    assert s0.in_use == 0
    assert s0.available == 3

    with sem.acquire_slot():
        s1 = sem.stats()
        assert s1.in_use == 1
        assert s1.available == 2


# ============================================================ config integration


def test_make_semaphore_reads_config() -> None:
    config = LoadedConfig(
        data={"c_pipeline": {"max_concurrent_pipelines": 4}},
        path=Path("config.yaml"),
    )
    sem = make_semaphore_from_config(config)
    assert sem.max_concurrent == 4


def test_make_semaphore_falls_back_to_default_when_unset() -> None:
    config = LoadedConfig(data={}, path=Path("config.yaml"))
    sem = make_semaphore_from_config(config)
    assert sem.max_concurrent == 2  # K2 default


def test_make_semaphore_zero_or_negative_rejected() -> None:
    config = LoadedConfig(
        data={"c_pipeline": {"max_concurrent_pipelines": 0}},
        path=Path("config.yaml"),
    )
    with pytest.raises(ValueError):
        make_semaphore_from_config(config)


# ============================================================ global singleton


def test_global_singleton_returns_same_instance() -> None:
    reset_global_semaphore()
    config = LoadedConfig(
        data={"c_pipeline": {"max_concurrent_pipelines": 2}},
        path=Path("config.yaml"),
    )
    a = get_global_semaphore(config)
    b = get_global_semaphore()
    assert a is b
    reset_global_semaphore()
    c = get_global_semaphore(config)
    assert c is not a


def test_global_singleton_default_when_no_config() -> None:
    reset_global_semaphore()
    sem = get_global_semaphore()
    assert sem.max_concurrent == 2
    reset_global_semaphore()
