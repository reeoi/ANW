"""Tests for Sprint 4 AI review scoring, rewrite, and batch CLI workflow."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "queue" in sys.modules and not hasattr(sys.modules["queue"], "__path__"):
    del sys.modules["queue"]

from config_loader import LoadedConfig
from queue.ai_review import DIMENSIONS, load_ai_review_settings, review_story
from queue.db import get_story, initialize_database, insert_story
from queue.models import Story


def _config(tmp_path: Path, **audit_overrides) -> LoadedConfig:
    data = {
        "database": {"sqlite_path": str(tmp_path / "ai_review.sqlite3")},
        "runtime": {"dry_run": True},
        "deepseek": {"mock": True, "model": "deepseek-chat", "timeout_seconds": 30, "max_retries": 2},
        "audit": {
            "approval_threshold": 80,
            "max_rewrite_attempts": 3,
            "dimensions": {
                "plot": 0.20,
                "character": 0.15,
                "pacing": 0.15,
                "language": 0.15,
                "originality": 0.15,
                "safety": 0.10,
                "platform_fit": 0.10,
            },
        },
    }
    data["audit"].update(audit_overrides)
    return LoadedConfig(data=data, path=Path("config.yaml"))


def test_review_story_returns_parseable_json_with_seven_dimensions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    story = Story(title="好故事", content="这是一篇结构完整的温情小说。" * 40, status="pending")

    result = review_story(story, config)
    parsed = json.loads(result.to_json())

    assert set(parsed) >= {"total_score", "dimension_scores", "issues", "suggestions", "decision"}
    assert set(parsed["dimension_scores"]) == set(DIMENSIONS)
    assert parsed["decision"] == "approved"
    assert parsed["total_score"] >= 80


def test_low_score_story_rewrites_until_approved_and_increments_retry_count(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db_path = initialize_database(config)
    story_id = insert_story(db_path, Story(title="过短", content="太短。", status="pending"))

    summary = review_story_in_database(db_path, story_id, config)
    stored = get_story(db_path, story_id)

    assert summary.decision == "approved"
    assert stored is not None
    assert stored.status == "approved"
    assert stored.retry_count == 1
    assert stored.score is not None and stored.score >= 80
    assert "AI 审核通过" in (stored.review_notes or "")
    assert len(stored.content) > len("太短。")


def test_story_moves_to_needs_human_after_configured_retry_limit(tmp_path: Path) -> None:
    config = _config(tmp_path, max_rewrite_attempts=1)
    db_path = initialize_database(config)
    bad_content = "违规 暴力 色情" * 20
    story_id = insert_story(db_path, Story(title="不安全", content=bad_content, status="pending"))

    summary = review_story_in_database(db_path, story_id, config)
    stored = get_story(db_path, story_id)

    assert summary.decision == "needs_human"
    assert stored is not None
    assert stored.status == "needs_human"
    assert stored.retry_count == 1
    assert "issues" in (stored.review_notes or "")
    assert "安全" in (stored.review_notes or "")


def test_settings_can_be_overridden_by_environment(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, approval_threshold=60, max_rewrite_attempts=1)
    monkeypatch.setenv("ANP_AI_REVIEW_THRESHOLD", "88")
    monkeypatch.setenv("ANP_MAX_REWRITE_ATTEMPTS", "2")
    monkeypatch.setenv("ANP_AI_REVIEW_MODEL", "mock-reviewer")
    monkeypatch.setenv("ANP_AI_REVIEW_TEMPERATURE", "0.2")

    settings = load_ai_review_settings(config)

    assert settings.approval_threshold == 88
    assert settings.max_rewrite_attempts == 2
    assert settings.model == "mock-reviewer"
    assert settings.temperature == 0.2


def test_cli_batch_outputs_counts_and_failure_reasons(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_ai_review.sqlite3"
    config = LoadedConfig(data={"database": {"sqlite_path": str(db_path)}}, path=Path("config.yaml"))
    initialize_database(config)
    insert_story(db_path, Story(title="好", content="完整故事内容。" * 40, status="pending"))
    insert_story(db_path, Story(title="坏", content="违规 暴力 色情" * 20, status="pending"))

    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("ANP_MAX_REWRITE_ATTEMPTS", "1")
    completed = subprocess.run(
        [sys.executable, "-m", "cli.ai_review", "--limit", "10"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "reviewed=2" in completed.stdout
    assert "approved=1" in completed.stdout
    assert "needs_human=1" in completed.stdout
    assert "failure_reasons" in completed.stdout


# Import after module-level tests are defined to keep the public API visible for assertions.
from queue.ai_review import review_story_in_database  # noqa: E402
