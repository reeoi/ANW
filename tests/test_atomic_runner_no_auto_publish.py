"""TDD: atomic_runner pipeline must STOP at approved (Q6=B).

Per design grilling Q6=B: ``run_full_atomic_task`` runs gen → AI review and
stops at approved. Publish is a separate human-triggered step
(``run_publish_only``) so users can verify each story before posting.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue import atomic_runner


@pytest.fixture()
def cfg(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(tmp_path)},
            "deepseek": {"api_key": "", "mock": True},
            "database": {"sqlite_path": str(tmp_path / "anw.sqlite3")},
            "publisher": {"fansq": {"enabled": True}},
            "logging": {"file": str(tmp_path / "anw.log")},
        },
        path=Path("config.yaml"),
    )


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    while atomic_runner.state.is_busy():
        atomic_runner.state.release()
    atomic_runner.state.clear_current()
    atomic_runner.state.reset_publish_fail_streak()
    yield
    while atomic_runner.state.is_busy():
        atomic_runner.state.release()
    atomic_runner.state.clear_current()


def test_run_full_atomic_task_stops_at_approved(cfg: LoadedConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """生成 → 审核 → 不再自动发布。"""
    publish_calls: list[int] = []

    def fake_generate(config, story_id=None):
        return 42, "generated"

    def fake_review(db_path, sid, config=None):
        return SimpleNamespace(decision="approved", final_score=92)

    def fake_publish(config, story):
        publish_calls.append(story.id)
        raise AssertionError("Q6=B: 流水线不应自动发布")

    monkeypatch.setattr(atomic_runner, "run_generate_with_retry", fake_generate)

    import review_queue.ai_review as ai_review_module

    monkeypatch.setattr(ai_review_module, "review_story_in_database", fake_review)
    monkeypatch.setattr(atomic_runner, "_publish_one", fake_publish)

    # Insert a fake story so get_story can succeed in the publish stage
    # (even though publish should NEVER run).
    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    db = initialize_database(cfg)
    insert_story(db, Story(title="测试", status="pending", id=42))

    out = atomic_runner.run_full_atomic_task(cfg, story_id=42)
    assert out.status == "approved", f"流水线应停在 approved，实际：{out.status}"
    assert out.phase == "review"
    assert publish_calls == [], "_publish_one 不应被调用"


def test_run_full_atomic_task_still_returns_needs_human_on_review_fail(
    cfg: LoadedConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 审核未通过仍然走 needs_human 路径。"""

    def fake_generate(config, story_id=None):
        return 7, "generated"

    def fake_review(db_path, sid, config=None):
        return SimpleNamespace(decision="needs_human", final_score=60)

    monkeypatch.setattr(atomic_runner, "run_generate_with_retry", fake_generate)

    import review_queue.ai_review as ai_review_module

    monkeypatch.setattr(ai_review_module, "review_story_in_database", fake_review)
    monkeypatch.setattr(atomic_runner, "_publish_one", lambda *a, **k: pytest.fail("不应发布"))

    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    db = initialize_database(cfg)
    insert_story(db, Story(title="测试", status="pending", id=7))

    out = atomic_runner.run_full_atomic_task(cfg, story_id=7)
    assert out.status == "needs_human"
    assert out.phase == "review"


def test_run_publish_only_function_exists() -> None:
    """暴露独立的手动发布触发函数。"""
    assert hasattr(atomic_runner, "run_publish_only"), "需要 run_publish_only(config, story_id)"
    assert callable(atomic_runner.run_publish_only)


def test_run_publish_only_calls_publish_with_approved_story(cfg: LoadedConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_publish_only 拿到 approved story 调 _publish_one。"""
    from publisher.base_publisher import PublishStatus

    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    db = initialize_database(cfg)
    sid = insert_story(db, Story(title="测试", status="approved"))

    captured: list[int] = []

    def fake_publish(config, story):
        captured.append(story.id)
        return SimpleNamespace(status=PublishStatus.PUBLISHED, message="ok")

    monkeypatch.setattr(atomic_runner, "_publish_one", fake_publish)
    out = atomic_runner.run_publish_only(cfg, sid)
    assert out.status == "published"
    assert captured == [sid]


def test_run_publish_only_rejects_non_approved_status(cfg: LoadedConfig) -> None:
    """没批准的 story 不能直接发布。"""
    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    db = initialize_database(cfg)
    sid = insert_story(db, Story(title="测试", status="pending"))

    out = atomic_runner.run_publish_only(cfg, sid)
    assert out.status == "failed"
    assert "approved" in out.message or "批准" in out.message


def test_run_publish_only_records_paused_status(cfg: LoadedConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    """发布暂停（风控）→ status=paused。"""
    from publisher.base_publisher import PublishStatus

    from review_queue.db import initialize_database, insert_story
    from review_queue.models import Story

    db = initialize_database(cfg)
    sid = insert_story(db, Story(title="测试", status="approved"))

    def fake_publish(config, story):
        return SimpleNamespace(status=PublishStatus.PAUSED, message="风控触发")

    monkeypatch.setattr(atomic_runner, "_publish_one", fake_publish)
    out = atomic_runner.run_publish_only(cfg, sid)
    assert out.status == "paused"
