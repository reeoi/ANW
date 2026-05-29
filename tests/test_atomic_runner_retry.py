"""Tests for atomic_runner retry resume behaviour.

When a phase fails, the next attempt must pass ``resume_from=<failed_phase>``
to ``run_pipeline`` so the orchestrator skips the already-completed phases.
This file covers the contract between ``PipelineError.failed_phase`` and
``run_generate_with_retry``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.c_pipeline.orchestrator import PipelineError
from review_queue.atomic_runner import run_generate_with_retry
from review_queue.atomic_runner import state as atomic_state


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={"database": {"sqlite_path": str(tmp_path / "x.sqlite3")}},
        path=Path("x.yaml"),
    )


def _ok_result(story_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(story_id=story_id, status="generated")


def test_retry_passes_failed_phase_as_resume_from(tmp_path: Path) -> None:
    """First attempt fails at phase_4, second attempt should resume there."""

    atomic_state.reset()
    calls: list[dict] = []

    def fake_run_pipeline(*, story_id, config, resume_from=None):
        calls.append({"story_id": story_id, "resume_from": resume_from})
        if len(calls) == 1:
            raise PipelineError("boom phase 4", failed_phase="phase_4")
        return _ok_result(story_id=story_id or 7)

    with patch(
        "generator.c_pipeline.orchestrator.run_pipeline",
        side_effect=fake_run_pipeline,
    ):
        sid, status = run_generate_with_retry(
            _config(tmp_path), story_id=7, max_attempts=3
        )

    assert status == "generated"
    assert sid == 7
    assert calls[0]["resume_from"] is None  # first attempt is fresh
    assert calls[1]["resume_from"] == "phase_4"  # retry resumes from failure


def test_retry_falls_back_to_full_restart_when_failed_phase_unknown(
    tmp_path: Path,
) -> None:
    """A bare PipelineError without ``failed_phase`` restarts at phase_0."""

    atomic_state.reset()
    calls: list[dict] = []

    def fake_run_pipeline(*, story_id, config, resume_from=None):
        calls.append({"story_id": story_id, "resume_from": resume_from})
        if len(calls) == 1:
            raise PipelineError("boom unknown", failed_phase=None)
        return _ok_result(story_id=story_id or 7)

    with patch(
        "generator.c_pipeline.orchestrator.run_pipeline",
        side_effect=fake_run_pipeline,
    ):
        sid, status = run_generate_with_retry(
            _config(tmp_path), story_id=7, max_attempts=3
        )

    assert status == "generated"
    assert calls[1]["resume_from"] is None


def test_retry_propagates_resume_from_across_multiple_failures(
    tmp_path: Path,
) -> None:
    """If attempt 1 fails at phase_3 and attempt 2 fails at phase_4,
    attempt 3 should resume from phase_4 (the latest failure)."""

    atomic_state.reset()
    calls: list[dict] = []

    def fake_run_pipeline(*, story_id, config, resume_from=None):
        calls.append({"story_id": story_id, "resume_from": resume_from})
        if len(calls) == 1:
            raise PipelineError("boom 3", failed_phase="phase_3")
        if len(calls) == 2:
            raise PipelineError("boom 4", failed_phase="phase_4")
        return _ok_result(story_id=story_id or 7)

    with patch(
        "generator.c_pipeline.orchestrator.run_pipeline",
        side_effect=fake_run_pipeline,
    ):
        sid, status = run_generate_with_retry(
            _config(tmp_path), story_id=7, max_attempts=3
        )

    assert status == "generated"
    assert [c["resume_from"] for c in calls] == [None, "phase_3", "phase_4"]
