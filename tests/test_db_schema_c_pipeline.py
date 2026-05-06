"""Verify the c_pipeline SQLite schema (PLAN.md §3.2): three tables, indexes,
new Story columns, CRUD helpers, and cost-log writes."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue.db import (
    add_pipeline_cost,
    get_daily_publish_plan,
    get_story,
    initialize_database,
    insert_pipeline_cost_log,
    insert_story,
    update_story_ai_review,
    update_story_metadata,
    update_story_phase,
    update_story_status,
    upsert_daily_publish_plan,
)
from review_queue.models import DailyPublishPlan, PipelineCostLogEntry, Story


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={"database": {"sqlite_path": str(tmp_path / "c.sqlite3")}},
        path=Path("c.yaml"),
    )


def test_initialize_creates_three_tables_with_indexes(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"stories", "daily_publish_plan", "pipeline_cost_log"}.issubset(tables)
    assert {"idx_stories_status", "idx_stories_current_phase", "idx_cost_log_occurred_at"}.issubset(indexes)


def test_stories_columns_match_plan(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stories)")}
    expected = {
        "id", "title", "status", "pipeline_version", "work_dir", "current_phase",
        "final_content_path", "pipeline_cost_cny", "target_length",
        "emotion", "genre", "hint_title", "summary",
        "ai_review_score", "ai_review_attempts", "content",
        "created_at", "updated_at",
    }
    assert expected == cols


def test_cost_log_columns_match_plan(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_cost_log)")}
    expected = {
        "id", "story_id", "phase", "model",
        "input_tokens", "cached_tokens", "output_tokens", "cost_cny",
        "occurred_at",
    }
    assert expected == cols


def test_daily_publish_plan_columns_match_plan(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_publish_plan)")}
    assert {"date", "planned_count", "slots_json", "created_at"} == cols


def test_insert_and_get_story_roundtrips_all_fields(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    sid = insert_story(
        db,
        Story(
            title="测试题材",
            status="pending",
            pipeline_version="c1",
            work_dir="data/works/42",
            current_phase="phase_3_section_05",
            final_content_path=None,
            pipeline_cost_cny=1.23,
            target_length=10500,
            emotion="意难平",
            genre="xian_dai_fu_chou",
            hint_title="丈夫给情人买学区房,我连夜做了三件事",
            summary="情境设定 → 冲突 → ...",
            ai_review_score=None,
            ai_review_attempts=0,
        ),
    )
    fetched = get_story(db, sid)
    assert fetched is not None
    assert fetched.title == "测试题材"
    assert fetched.work_dir == "data/works/42"
    assert fetched.current_phase == "phase_3_section_05"
    assert fetched.target_length == 10500
    assert fetched.emotion == "意难平"
    assert fetched.genre == "xian_dai_fu_chou"
    assert fetched.pipeline_cost_cny == 1.23


def test_update_story_phase_and_final_content_path(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    sid = insert_story(db, Story(title="t"))
    assert update_story_phase(db, sid, "phase_5_done", final_content_path="data/works/1/5_最终稿.md")
    fetched = get_story(db, sid)
    assert fetched is not None
    assert fetched.current_phase == "phase_5_done"
    assert fetched.final_content_path == "data/works/1/5_最终稿.md"


def test_update_story_ai_review_persists_score_attempts_status(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    sid = insert_story(db, Story(title="t"))
    assert update_story_ai_review(db, sid, score=92.5, attempts=2, status="approved")
    fetched = get_story(db, sid)
    assert fetched is not None
    assert fetched.ai_review_score == 92.5
    assert fetched.ai_review_attempts == 2
    assert fetched.status == "approved"


def test_update_story_metadata_only_changes_named_fields(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    sid = insert_story(db, Story(title="原标题", emotion="意难平"))
    assert update_story_metadata(db, sid, title="新标题", summary="新简介")
    fetched = get_story(db, sid)
    assert fetched is not None
    assert fetched.title == "新标题"
    assert fetched.summary == "新简介"
    # 未提供的字段保留
    assert fetched.emotion == "意难平"


def test_add_pipeline_cost_accumulates(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    sid = insert_story(db, Story(title="t"))
    add_pipeline_cost(db, sid, 0.4)
    add_pipeline_cost(db, sid, 0.7)
    fetched = get_story(db, sid)
    assert fetched is not None
    assert round(fetched.pipeline_cost_cny, 2) == 1.10


def test_insert_pipeline_cost_log_persists_cached_tokens(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    sid = insert_story(db, Story(title="t"))
    log_id = insert_pipeline_cost_log(
        db,
        PipelineCostLogEntry(
            story_id=sid,
            phase="phase_3_section_05",
            model="deepseek-v4-pro",
            input_tokens=12000,
            cached_tokens=11500,
            output_tokens=1500,
            cost_cny=0.36,
        ),
    )
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT story_id, phase, model, input_tokens, cached_tokens, output_tokens, cost_cny "
            "FROM pipeline_cost_log WHERE id = ?",
            (log_id,),
        ).fetchone()
    assert row == (sid, "phase_3_section_05", "deepseek-v4-pro", 12000, 11500, 1500, 0.36)


def test_upsert_daily_publish_plan_replaces_for_same_date(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    slots_v1 = json.dumps([{"slot_time": "2026-05-06T14:23:00", "story_id": 1, "published_at": None, "skipped_reason": None}])
    upsert_daily_publish_plan(db, DailyPublishPlan(date="2026-05-06", planned_count=1, slots_json=slots_v1))
    slots_v2 = json.dumps([
        {"slot_time": "2026-05-06T10:11:00", "story_id": 7, "published_at": None, "skipped_reason": None},
        {"slot_time": "2026-05-06T18:42:00", "story_id": 8, "published_at": None, "skipped_reason": None},
    ])
    upsert_daily_publish_plan(db, DailyPublishPlan(date="2026-05-06", planned_count=2, slots_json=slots_v2))
    fetched = get_daily_publish_plan(db, "2026-05-06")
    assert fetched is not None
    assert fetched.planned_count == 2
    assert json.loads(fetched.slots_json) == json.loads(slots_v2)


def test_update_story_status_with_summary_and_score(tmp_path: Path) -> None:
    db = initialize_database(_config(tmp_path))
    sid = insert_story(db, Story(title="t"))
    assert update_story_status(db, sid, "needs_human", summary="阈值未达", ai_review_score=82.0)
    fetched = get_story(db, sid)
    assert fetched is not None
    assert fetched.status == "needs_human"
    assert fetched.summary == "阈值未达"
    assert fetched.ai_review_score == 82.0


def test_story_read_final_content_returns_none_when_missing(tmp_path: Path) -> None:
    story = Story(title="t", final_content_path=str(tmp_path / "no_such.md"))
    assert story.read_final_content() is None


def test_story_read_final_content_reads_file(tmp_path: Path) -> None:
    file_path = tmp_path / "5_最终稿.md"
    file_path.write_text("# 标题\n\n正文段落。", encoding="utf-8")
    story = Story(title="t", final_content_path=str(file_path))
    text = story.read_final_content()
    assert text is not None
    assert "正文段落" in text
