"""Tests for Sprint 2 generation and queue flow."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "queue" in sys.modules and not hasattr(sys.modules["queue"], "__path__"):
    del sys.modules["queue"]

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.prompt_builder import build_short_story_prompt
from queue.db import get_story, initialize_database, insert_story
from queue.models import Story


def test_prompt_builder_includes_theme_word_count_and_style() -> None:
    prompt = build_short_story_prompt("海边旧书店", word_count=1200, style="悬疑温情")

    assert "海边旧书店" in prompt
    assert "1200" in prompt
    assert "悬疑温情" in prompt
    assert "短篇小说" in prompt


def test_mock_client_returns_reasonable_fixed_story() -> None:
    config = LoadedConfig(
        data={"runtime": {"dry_run": True}, "deepseek": {"mock": True, "api_key": ""}},
        path=Path("config.yaml"),
    )

    story = DeepSeekClient(config).generate_story("请写主题《雨夜归人》的短篇小说")

    assert "《雨夜归人》" in story.title
    assert "雨夜归人" in story.content
    assert len(story.content) > 200


def test_database_schema_and_insert_pending_story(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.sqlite3"
    config = LoadedConfig(
        data={"database": {"sqlite_path": str(db_path)}},
        path=Path("config.yaml"),
    )

    initialize_database(config)
    story_id = insert_story(db_path, Story(title="测试标题", content="测试内容"))
    stored = get_story(db_path, story_id)

    assert story_id > 0
    assert stored is not None
    assert stored.status == "pending"
    assert stored.score is None
    assert stored.retry_count == 0

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(stories)")}

    assert {
        "id",
        "title",
        "content",
        "status",
        "score",
        "retry_count",
        "review_notes",
        "created_at",
        "updated_at",
        "published_at",
    }.issubset(columns)
