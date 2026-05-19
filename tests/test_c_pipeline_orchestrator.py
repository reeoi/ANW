"""Tests for generator/c_pipeline/orchestrator.py (Phase C.10).

End-to-end pipeline state-machine coverage:

- run_pipeline creates a placeholder story when story_id is None
- All 6 phases run, all artifacts land in data/works/{id}/
- stories.current_phase advances through each phase to phase_5_done
- stories.title / summary / final_content_path are set after Phase 1 / 5
- pipeline_cost_log accumulates rows for each phase
- failure path: exception sets status=failed and current_phase=failed_at_phase_N
- resume_from='phase_3' skips phases 0-2 (uses existing artifacts)
- K2 semaphore is acquired and released
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.api_client import ChatCompletion, ChatUsage
from generator.c_pipeline.concurrency import PipelineSemaphore
from generator.c_pipeline.orchestrator import (
    PHASES,
    PipelineError,
    PipelineResult,
    run_pipeline,
)
from review_queue.db import get_database_path, get_story, initialize_database, insert_story
from review_queue.models import Story


# ============================================================ helpers


def _setup_project(tmp_path: Path, *, n_pool_items: int = 1) -> Path:
    """Create a minimal project layout (data/scan_seeds.yaml + theme_pool.json)
    rooted at ``tmp_path``. Phase modules will use this as ``project_root``."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    shutil.copy(ROOT / "data" / "scan_seeds.yaml", data_dir / "scan_seeds.yaml")

    pool = {
        "version": 1,
        "iso_week": "2026W19",
        "weekly_topics": ["拆迁分房"],
        "items": [
            {
                "id": f"tp_2026w19_{i:03d}",
                "theme": f"白领姐弟拆迁分房纠纷复仇{i}",
                "emotion": "shuang_gan_shi_fang",
                "genre": "xian_dai_fu_chou",
                "formula_used": "...",
                "target_platform": "番茄短篇",
                "target_length": [10000, 12000],
                "hint_title": f"测试标题{i}",
                "title_pattern_used": "番茄主流",
                "opening_mode": "leng_xiao_fa_xian",
                "ending_mode": "da_chang_jing_tou",
                "reversal_type": "shi_jiao_fan_zhuan",
                "expected_audience": "女频",
                "seasonal_or_topic_seed": "拆迁分房",
                "consumed_count": 0,
                "created_at": "2026-05-06T03:00:00Z",
            }
            for i in range(1, n_pool_items + 1)
        ],
    }
    (data_dir / "theme_pool.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (data_dir / "works").mkdir()
    return tmp_path


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"dry_run": True, "project_root": str(tmp_path)},
            "deepseek": {
                "api_key": "",
                "model": "deepseek-v4-pro",
                "flash_model": "deepseek-v4-flash",
                "thinking_mode": True,
                "mock": True,
            },
            "database": {"sqlite_path": str(tmp_path / "anp.sqlite3")},
            "cost_limits": {
                "monthly_budget_cny": 100.0,
                "degrade_phases": ["phase_3", "phase_5"],
            },
            "c_pipeline": {"max_concurrent_pipelines": 2},
        },
        path=Path("config.yaml"),
    )


class FakeClient:
    """All-mock client that always uses the project's mock-fallback paths.

    The real DeepSeekClient mock returns a placeholder string; phase modules
    have explicit fallback synthesis for that case. Reusing the real client
    would require config wiring; this stub does the same thing more directly.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

        class _Settings:
            model = "deepseek-v4-pro"
            flash_model = "deepseek-v4-flash"

        self.settings = _Settings()

    def is_mock(self) -> bool:
        return True

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        thinking_mode: bool | None = None,
        model: str | None = None,
        temperature: float = 0.8,
        response_format: Any = None,
        purpose: str = "chat",
    ) -> ChatCompletion:
        self.calls.append({"thinking_mode": thinking_mode, "purpose": purpose})
        # Mock-style placeholder text → triggers each phase's mock fallback.
        return ChatCompletion(
            text="[mock] DeepSeek 客户端运行在 mock/dry-run 模式。",
            reasoning="(mock)" if thinking_mode else None,
            model=model or "deepseek-v4-pro",
            usage=ChatUsage(input_tokens=200, cached_tokens=50, output_tokens=400),
            finish_reason="stop",
            cached=False,
        )


# ============================================================ happy path


def test_run_pipeline_creates_story_and_advances_through_all_phases(
    tmp_path: Path,
) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)
    client = FakeClient()

    result = run_pipeline(story_id=None, config=config, client=client)

    assert isinstance(result, PipelineResult)
    assert result.story_id >= 1
    assert result.final_phase == "phase_6_done"
    assert result.status in ("pending", "needs_human")
    assert result.final_content_path is not None
    assert result.final_content_path.exists()

    # Database state
    db = get_database_path(config)
    story = get_story(db, result.story_id)
    assert story is not None
    assert story.current_phase == "phase_6_done"
    assert story.final_content_path == str(result.final_content_path)
    assert story.title  # set from Phase 1 final_title
    assert story.summary  # set from Phase 1 summary

    # All artifacts
    work = result.work_dir
    assert (work / "0_选题.json").exists()
    assert (work / "1_设定.md").exists()
    assert (work / "2_小节大纲.md").exists()
    assert (work / "3_正文_合稿.md").exists()
    # 8 fallback sections expected
    for i in range(1, 9):
        assert (work / f"3_正文_第 {i:02d} 节.md").exists()
    assert (work / "4_精修稿.md").exists()
    assert (work / "5_最终稿.md").exists()
    assert (work / "6_最终稿_带章节.md").exists()


def test_run_pipeline_writes_pipeline_cost_log(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)
    result = run_pipeline(config=config, client=FakeClient())

    db = get_database_path(config)
    with sqlite3.connect(db) as conn:
        rows = list(
            conn.execute(
                "SELECT phase FROM pipeline_cost_log WHERE story_id = ?",
                (result.story_id,),
            )
        )
    phases = [r[0] for r in rows]
    # phases 0, 1, 2, 4, 5 each log once; phase_3_aggregate logs once
    assert "phase_0" in phases
    assert "phase_1" in phases
    assert "phase_2" in phases
    assert "phase_3_aggregate" in phases
    assert "phase_4" in phases
    assert "phase_5" in phases


def test_run_pipeline_consumed_count_increments_pool(tmp_path: Path) -> None:
    _setup_project(tmp_path, n_pool_items=2)
    config = _config(tmp_path)
    run_pipeline(config=config, client=FakeClient())

    pool = json.loads((tmp_path / "data" / "theme_pool.json").read_text(encoding="utf-8"))
    by_id = {it["id"]: it for it in pool["items"]}
    # Phase 0 picks lowest-consumed first; with two items both at 0, it picks
    # item id 001 (sorts first).
    assert by_id["tp_2026w19_001"]["consumed_count"] == 1
    assert by_id["tp_2026w19_002"]["consumed_count"] == 0


def test_run_pipeline_with_existing_story_id_does_not_create_new_row(
    tmp_path: Path,
) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)
    db = initialize_database(config)
    sid = insert_story(db, Story(title="预创建", work_dir=str(tmp_path / "data" / "works" / "99")))

    result = run_pipeline(story_id=sid, config=config, client=FakeClient())
    assert result.story_id == sid
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT COUNT(*) FROM stories").fetchone()
    assert row[0] == 1


def test_run_pipeline_uses_overrides(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)

    result = run_pipeline(
        config=config,
        client=FakeClient(),
        overrides={"target_length": 9000},
    )
    db = get_database_path(config)
    story = get_story(db, result.story_id)
    # target_length midpoint of [9000*0.95, 9000*1.05] = 9000
    assert story.target_length == 9000


# ============================================================ failure path


def test_run_pipeline_failure_sets_status_failed(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)

    # Force phase 1 to raise by mocking run_framework
    with patch(
        "generator.c_pipeline.orchestrator.phase1_framework.run_framework",
        side_effect=RuntimeError("phase 1 boom"),
    ):
        with pytest.raises(PipelineError) as exc_info:
            run_pipeline(config=config, client=FakeClient())
        assert exc_info.value.failed_phase == "phase_1"

    db = get_database_path(config)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT id, status, current_phase FROM stories ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row[1] == "failed"
    assert row[2].startswith("failed_at_phase_1")


def test_run_pipeline_phase_0_failure_marks_failed_at_phase_0(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)

    with patch(
        "generator.c_pipeline.orchestrator.phase0_select.select_theme",
        side_effect=RuntimeError("phase 0 boom"),
    ):
        with pytest.raises(PipelineError):
            run_pipeline(config=config, client=FakeClient())

    db = get_database_path(config)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT status, current_phase FROM stories ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row[0] == "failed"
    assert "phase_0" in row[1]


# ============================================================ resume


def test_run_pipeline_resume_from_phase_3_skips_earlier_phases(
    tmp_path: Path,
) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)
    db = initialize_database(config)

    # Pre-populate work_dir as if Phases 0-2 had already produced their artifacts.
    sid = insert_story(db, Story(title="resume-test", work_dir=""))
    work = tmp_path / "data" / "works" / str(sid)
    work.mkdir(parents=True)
    # Phase 0 artifact
    (work / "0_选题.json").write_text(
        json.dumps(
            {"target_length": [10000, 12000], "genre_id": "xian_dai_fu_chou"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # Phase 1 artifact
    framework_md = """# 设定

## final_title
预先存在的标题

## summary
""" + ("我盯着银行短信。" * 12) + """

## 一句话核心
test
"""
    (work / "1_设定.md").write_text(framework_md, encoding="utf-8")
    # Phase 2 artifact (valid 8-section table)
    from generator.c_pipeline.phase2_outline import OutlineSection, render_outline_md

    sections = [
        OutlineSection(
            index=i,
            main_event=f"主{i}",
            sub_events=["a", "b", "c"],
            emotion="爆发",
            new_info=f"info{i}",
            hook=f"hook{i}",
            foreshadowing="物件",
            static_dynamic="动",
            dialogue_ratio="30%",
            target_words=1300,
        )
        for i in range(1, 9)
    ]
    (work / "2_小节大纲.md").write_text(
        render_outline_md(sections, target_length=10400), encoding="utf-8"
    )

    client = FakeClient()
    result = run_pipeline(
        story_id=sid, config=config, client=client, resume_from="phase_3"
    )
    assert result.final_phase == "phase_6_done"
    # Phase 0/1/2 should NOT have been called → no calls with those purposes
    purposes = [c["purpose"] for c in client.calls]
    assert not any(p in ("phase_0", "phase_1", "phase_2") for p in purposes)
    # Phase 3+ purposes are present
    assert any(p.startswith("phase_3_section_") for p in purposes)
    assert "phase_4" in purposes
    assert "phase_5" in purposes
    assert "phase_6" in purposes


def test_run_pipeline_resume_does_not_emit_phase_0_reset(tmp_path: Path) -> None:
    """When resuming, ``current_phase`` and the transition log must not be
    rewritten back to ``phase_0`` — atomic_runner relies on this so the
    dashboard timeline shows the retry continuing from the failed phase.
    """

    _setup_project(tmp_path)
    config = _config(tmp_path)
    db = initialize_database(config)
    sid = insert_story(
        db,
        Story(title="resume-test-no-reset", current_phase="failed_at_phase_4"),
    )
    work = tmp_path / "data" / "works" / str(sid)
    work.mkdir(parents=True)
    # Minimal artifacts so phases 0-3 are skipped via resume_from.
    (work / "0_选题.json").write_text(
        json.dumps({"target_length": [10000, 12000], "genre_id": "xian_dai_fu_chou"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (work / "1_设定.md").write_text(
        "# 设定\n\n## final_title\n标题\n\n## summary\n" + ("我盯着银行短信。" * 12) + "\n\n## 一句话核心\nx\n",
        encoding="utf-8",
    )
    from generator.c_pipeline.phase2_outline import OutlineSection, render_outline_md

    sections = [
        OutlineSection(
            index=i, main_event=f"主{i}", sub_events=["a","b","c"], emotion="爆发",
            new_info=f"info{i}", hook=f"hook{i}", foreshadowing="物件",
            static_dynamic="动", dialogue_ratio="30%", target_words=1300,
        )
        for i in range(1, 9)
    ]
    (work / "2_小节大纲.md").write_text(render_outline_md(sections, target_length=10400), encoding="utf-8")
    # Phase 3 combined output — required before phase_4 resume.
    (work / "3_正文_合稿.md").write_text("正文" * 1000, encoding="utf-8")

    run_pipeline(story_id=sid, config=config, client=FakeClient(), resume_from="phase_4")

    # No phase_0 / phase_0_running marker should appear in this resume run.
    with sqlite3.connect(db) as conn:
        rows = list(
            conn.execute(
                "SELECT phase FROM phase_transitions WHERE story_id = ? ORDER BY id",
                (sid,),
            )
        )
    markers = [r[0] for r in rows]
    assert "phase_0" not in markers
    assert "phase_0_running" not in markers
    # phase_4 was actually re-entered.
    assert "phase_4_running" in markers


# ============================================================ semaphore


def test_run_pipeline_uses_semaphore_slot(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)
    sem = PipelineSemaphore(max_concurrent=2)
    assert sem.in_use == 0
    run_pipeline(config=config, client=FakeClient(), semaphore=sem)
    # After the run completes the slot is back to 0
    assert sem.in_use == 0


def test_run_pipeline_writes_work_dir_into_stories(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)
    result = run_pipeline(config=config, client=FakeClient())
    db = get_database_path(config)
    story = get_story(db, result.story_id)
    assert story.work_dir.endswith(str(result.story_id))


# ============================================================ cancellation


def test_run_pipeline_cancellation_marks_story_cancelled(tmp_path: Path) -> None:
    """``cancel_requested=1`` set before run_pipeline → cancelled status."""

    from review_queue.db import insert_story, request_story_cancel

    _setup_project(tmp_path)
    config = _config(tmp_path)
    db = initialize_database(config)
    sid = insert_story(db, Story(title="待取消", status="pending"))
    request_story_cancel(db, sid)

    result = run_pipeline(story_id=sid, config=config, client=FakeClient())
    assert result.status == "cancelled"
    assert result.final_phase.startswith("cancelled_at_")
    story = get_story(db, sid)
    assert story is not None
    assert story.status == "cancelled"
    assert story.current_phase.startswith("cancelled_at_")
