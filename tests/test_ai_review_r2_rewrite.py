"""Phase E R2 rewrite tests (decision #31).

Verifies the AI review loop in ``review_queue.ai_review`` and the
``generator.c_pipeline.rewrite.rerun_phase_4_5`` helper:

- Approval on the first review skips the rewrite path entirely.
- Failing reviews trigger ``rerun_phase_4_5`` up to
  ``settings.max_rewrite_attempts`` times.
- Approval after a rerun marks ``status='approved'`` and reflects the
  rerun count in ``ai_review_attempts``.
- All attempts failing surfaces ``status='needs_human'`` with a
  preserved failure reason.
- ``rerun_phase_4_5`` raises ``RewriteError`` when the story or its
  work_dir / phase 3 合稿 are missing.
- ``rerun_phase_4_5`` actually re-runs Phase 4 + Phase 5 modules and
  re-points ``stories.final_content_path`` at the regenerated 5_最终稿.md.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.c_pipeline import rewrite as rewrite_module
from generator.c_pipeline.rewrite import RewriteError, rerun_phase_4_5
from review_queue import ai_review as ai_review_module
from review_queue.ai_review import ReviewResult, review_story_in_database
from review_queue.db import get_story, initialize_database, insert_story
from review_queue.models import Story


# ============================================================ helpers


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={
            "database": {"sqlite_path": str(tmp_path / "ai_review.sqlite3")},
            "audit": {
                "approval_threshold": 90,
                "max_rewrite_attempts": 3,
                "rewrite_strategy": "phase_4_5_only",
            },
            "deepseek": {"mock": True, "api_key": ""},
            "runtime": {"dry_run": True, "project_root": str(tmp_path)},
            "logging": {"file": str(tmp_path / "anp.log")},
        },
        path=Path("ai_review.yaml"),
    )


def _seed_pending_story(tmp_path: Path, db_path: Path) -> int:
    work_dir = tmp_path / "data" / "works" / "1"
    work_dir.mkdir(parents=True, exist_ok=True)
    final_path = work_dir / "5_最终稿.md"
    final_path.write_text("正文" * 1500, encoding="utf-8")
    sid = insert_story(
        db_path,
        Story(
            title="待审核故事",
            status="pending",
            current_phase="phase_5_done",
            work_dir=str(work_dir),
            final_content_path=str(final_path),
            target_length=10000,
            emotion="意难平",
        ),
    )
    return sid


def _approving_review() -> ReviewResult:
    return ReviewResult(
        total_score=95,
        dimension_scores={k: 90 for k in (
            "plot", "character", "pacing", "language", "originality", "safety", "platform_fit"
        )},
        issues=[],
        suggestions=[],
        decision="approved",
    )


def _failing_review() -> ReviewResult:
    return ReviewResult(
        total_score=70,
        dimension_scores={k: 70 for k in (
            "plot", "character", "pacing", "language", "originality", "safety", "platform_fit"
        )},
        issues=["情节强度不足"],
        suggestions=["加强反转"],
        decision="rewrite",
    )


# ============================================================ ai_review path


def test_review_approves_on_first_pass_skips_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    sid = _seed_pending_story(tmp_path, db_path)

    rerun_calls: list[int] = []
    monkeypatch.setattr(
        ai_review_module,
        "_do_rewrite_phase_4_5",
        lambda story_id, *, config=None: rerun_calls.append(story_id),
    )
    monkeypatch.setattr(
        ai_review_module,
        "review_story",
        lambda story, config=None, settings=None: _approving_review(),
    )

    summary = review_story_in_database(db_path, sid, config=cfg)
    assert summary.decision == "approved"
    assert summary.attempts == 0
    assert summary.final_score == 95
    assert rerun_calls == []  # no rewrite invoked

    fetched = get_story(db_path, sid)
    assert fetched is not None
    assert fetched.status == "approved"
    assert fetched.ai_review_score == 95
    assert fetched.ai_review_attempts == 0


def test_review_failing_triggers_max_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    sid = _seed_pending_story(tmp_path, db_path)

    rerun_calls: list[int] = []
    monkeypatch.setattr(
        ai_review_module,
        "_do_rewrite_phase_4_5",
        lambda story_id, *, config=None: rerun_calls.append(story_id),
    )
    monkeypatch.setattr(
        ai_review_module,
        "review_story",
        lambda story, config=None, settings=None: _failing_review(),
    )

    summary = review_story_in_database(db_path, sid, config=cfg)
    assert summary.decision == "needs_human"
    assert summary.attempts == 3  # max_rewrite_attempts
    assert rerun_calls == [sid, sid, sid]

    fetched = get_story(db_path, sid)
    assert fetched is not None
    assert fetched.status == "needs_human"
    assert fetched.ai_review_attempts == 3


def test_review_approved_after_one_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    sid = _seed_pending_story(tmp_path, db_path)

    rerun_calls: list[int] = []
    monkeypatch.setattr(
        ai_review_module,
        "_do_rewrite_phase_4_5",
        lambda story_id, *, config=None: rerun_calls.append(story_id),
    )

    decisions = iter([_failing_review(), _approving_review()])
    monkeypatch.setattr(
        ai_review_module,
        "review_story",
        lambda story, config=None, settings=None: next(decisions),
    )

    summary = review_story_in_database(db_path, sid, config=cfg)
    assert summary.decision == "approved"
    assert summary.attempts == 1
    assert rerun_calls == [sid]
    fetched = get_story(db_path, sid)
    assert fetched is not None
    assert fetched.status == "approved"
    assert fetched.ai_review_attempts == 1


def test_review_rewrite_exception_short_circuits_to_needs_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    sid = _seed_pending_story(tmp_path, db_path)

    def failing_rerun(story_id: int, *, config: Any | None = None) -> None:
        raise RewriteError("simulated work_dir corruption")

    monkeypatch.setattr(ai_review_module, "_do_rewrite_phase_4_5", failing_rerun)
    monkeypatch.setattr(
        ai_review_module,
        "review_story",
        lambda story, config=None, settings=None: _failing_review(),
    )

    summary = review_story_in_database(db_path, sid, config=cfg)
    assert summary.decision == "needs_human"
    assert summary.attempts == 1  # one attempt incremented before break
    assert summary.failure_reason is not None
    assert "Phase 4-5 rerun #1 failed" in summary.failure_reason
    assert "simulated work_dir corruption" in summary.failure_reason
    fetched = get_story(db_path, sid)
    assert fetched is not None
    assert fetched.status == "needs_human"
    assert fetched.ai_review_attempts == 1


def test_review_rewrite_strategy_other_skips_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    cfg.data["audit"]["rewrite_strategy"] = "full_pipeline"  # not implemented
    db_path = initialize_database(cfg)
    sid = _seed_pending_story(tmp_path, db_path)

    monkeypatch.setattr(
        ai_review_module,
        "_do_rewrite_phase_4_5",
        lambda story_id, *, config=None: pytest.fail("rerun should not run"),
    )
    monkeypatch.setattr(
        ai_review_module,
        "review_story",
        lambda story, config=None, settings=None: _failing_review(),
    )

    summary = review_story_in_database(db_path, sid, config=cfg)
    assert summary.decision == "needs_human"
    assert summary.attempts == 0
    assert summary.failure_reason is not None
    assert "unsupported rewrite_strategy" in summary.failure_reason


# ============================================================ rerun_phase_4_5


def test_rerun_phase_4_5_missing_story_raises(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    initialize_database(cfg)
    with pytest.raises(RewriteError):
        rerun_phase_4_5(99999, config=cfg)


def test_rerun_phase_4_5_missing_work_dir_raises(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    sid = insert_story(
        db_path,
        Story(
            title="t",
            status="pending",
            work_dir="(pending)",
        ),
    )
    with pytest.raises(RewriteError) as exc:
        rerun_phase_4_5(sid, config=cfg)
    assert "work_dir" in str(exc.value)


def test_rerun_phase_4_5_missing_phase3_合稿_raises(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    work_dir = tmp_path / "data" / "works" / "x"
    work_dir.mkdir(parents=True)
    sid = insert_story(
        db_path,
        Story(
            title="t",
            status="pending",
            work_dir=str(work_dir),
            current_phase="phase_3_done",
        ),
    )
    with pytest.raises(RewriteError) as exc:
        rerun_phase_4_5(sid, config=cfg)
    assert "3_正文_合稿.md" in str(exc.value)


def test_rerun_phase_4_5_calls_phase4_phase5_and_updates_final_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    work_dir = tmp_path / "data" / "works" / "7"
    work_dir.mkdir(parents=True)
    (work_dir / "3_正文_合稿.md").write_text("第一稿内容" * 200, encoding="utf-8")
    sid = insert_story(
        db_path,
        Story(
            title="t",
            status="pending",
            work_dir=str(work_dir),
            current_phase="phase_5_done",
            final_content_path=str(work_dir / "5_最终稿.md"),
        ),
    )

    polish_calls: list[Path] = []
    deslop_calls: list[Path] = []

    @dataclass
    class FakeUsage:
        input_tokens: int = 100
        cached_tokens: int = 0
        output_tokens: int = 200
        raw: dict = None

        def __post_init__(self) -> None:
            if self.raw is None:
                self.raw = {}

    @dataclass
    class FakeCompletion:
        text: str = "ok"
        reasoning: str | None = None
        model: str = "deepseek-v4-pro"
        usage: Any = None
        finish_reason: str = "stop"
        cached: bool = False

        def __post_init__(self) -> None:
            if self.usage is None:
                self.usage = FakeUsage()

    @dataclass
    class FakePhase4:
        polished_md: str = "二稿"
        polished_path: Path = Path()
        char_count: int = 5000
        llm_completion: Any = None
        used_fallback: bool = False
        warnings: list = None

        def __post_init__(self) -> None:
            if self.warnings is None:
                self.warnings = []
            if self.llm_completion is None:
                self.llm_completion = FakeCompletion()

    @dataclass
    class FakePhase5:
        deslopped_md: str = "终稿"
        final_path: Path = Path()
        char_count: int = 5000
        llm_completion: Any = None
        used_fallback: bool = False
        warnings: list = None

        def __post_init__(self) -> None:
            if self.warnings is None:
                self.warnings = []
            if self.llm_completion is None:
                self.llm_completion = FakeCompletion()

    final_path_new = work_dir / "5_最终稿.md"

    def fake_polish(config, *, work_dir, client=None, **kwargs):
        polish_calls.append(work_dir)
        return FakePhase4(polished_path=work_dir / "4_精修稿.md")

    def fake_deslop(config, *, work_dir, client=None, **kwargs):
        deslop_calls.append(work_dir)
        return FakePhase5(final_path=final_path_new)

    monkeypatch.setattr(rewrite_module.phase4_polish, "run_polish", fake_polish)
    monkeypatch.setattr(rewrite_module.phase5_deslop, "run_deslop", fake_deslop)

    # Provide a stub client so DeepSeekClient(config) is never instantiated.
    class _StubClient:
        def is_mock(self) -> bool:
            return True

    result = rerun_phase_4_5(sid, config=cfg, client=_StubClient())
    assert result.story_id == sid
    assert result.final_content_path == final_path_new
    assert polish_calls == [work_dir]
    assert deslop_calls == [work_dir]

    fetched = get_story(db_path, sid)
    assert fetched is not None
    assert fetched.current_phase == "phase_5_done"
    assert fetched.final_content_path == str(final_path_new)


# ============================================================ batch path


def test_run_review_batch_threads_r2_through_each_story(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke check: batch path delegates per-story to review_story_in_database."""

    cfg = _config(tmp_path)
    db_path = initialize_database(cfg)
    s1 = _seed_pending_story(tmp_path, db_path)
    work_dir2 = tmp_path / "data" / "works" / "2"
    work_dir2.mkdir(parents=True, exist_ok=True)
    final_path2 = work_dir2 / "5_最终稿.md"
    final_path2.write_text("正文" * 1500, encoding="utf-8")
    s2 = insert_story(
        db_path,
        Story(
            title="第二篇",
            status="pending",
            current_phase="phase_5_done",
            work_dir=str(work_dir2),
            final_content_path=str(final_path2),
            target_length=10000,
            emotion="爽感释放",
        ),
    )

    rerun_calls: list[int] = []
    monkeypatch.setattr(
        ai_review_module,
        "_do_rewrite_phase_4_5",
        lambda story_id, *, config=None: rerun_calls.append(story_id),
    )

    decision_lookup = {s1: _approving_review(), s2: _failing_review()}

    def review_dispatcher(story, config=None, settings=None):
        return decision_lookup.get(story.id, _failing_review())

    monkeypatch.setattr(ai_review_module, "review_story", review_dispatcher)

    from review_queue.ai_review import run_review_batch

    result = run_review_batch(db_path, threshold=90, limit=10, config=cfg)
    assert result.reviewed == 2
    assert result.approved == 1  # s1 approved
    assert result.needs_human == 1  # s2 escalated
    assert rerun_calls == [s2, s2, s2]  # s2 retried up to max
