"""Phase G.3 — failure work_dir retention tests (decision #12).

When any pipeline phase raises, the orchestrator must:
- mark stories.status='failed'
- mark current_phase='failed_at_phase_N'
- leave data/works/{story_id}/ on disk untouched (no cleanup)
- keep all partial artifacts (0_选题.json, 1_设定.md, ...) readable

A subsequent ``run_pipeline(resume_from='phase_2')`` must then complete
the run by re-using those partial artifacts, advancing through the
remaining phases without redoing 0-1.
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
from generator.c_pipeline.orchestrator import PipelineError, run_pipeline
from review_queue.db import get_database_path, get_story, initialize_database


# ============================================================ helpers


def _setup_project(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    shutil.copy(ROOT / "data" / "scan_seeds.yaml", data_dir / "scan_seeds.yaml")
    pool = {
        "version": 1,
        "iso_week": "2026W19",
        "weekly_topics": ["拆迁分房"],
        "items": [
            {
                "id": "tp_2026w19_001",
                "theme": "白领姐弟拆迁分房纠纷复仇",
                "emotion": "shuang_gan_shi_fang",
                "genre": "xian_dai_fu_chou",
                "formula_used": "...",
                "target_platform": "番茄短篇",
                "target_length": [10000, 12000],
                "hint_title": "测试标题",
                "title_pattern_used": "番茄主流",
                "opening_mode": "leng_xiao_fa_xian",
                "ending_mode": "da_chang_jing_tou",
                "reversal_type": "shi_jiao_fan_zhuan",
                "expected_audience": "女频",
                "seasonal_or_topic_seed": "拆迁分房",
                "consumed_count": 0,
                "created_at": "2026-05-06T03:00:00Z",
            }
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
            "cost_limits": {"monthly_budget_cny": 100.0, "degrade_phases": []},
            "c_pipeline": {"max_concurrent_pipelines": 2},
        },
        path=Path("config.yaml"),
    )


class _MockClient:
    """All-mock client that drives each phase's mock fallback path."""

    def __init__(self) -> None:
        class _Settings:
            model = "deepseek-v4-pro"
            flash_model = "deepseek-v4-flash"

        self.settings = _Settings()
        self.calls: list[dict[str, Any]] = []

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
        return ChatCompletion(
            text="[mock] DeepSeek 客户端运行在 mock/dry-run 模式。",
            reasoning="(mock)" if thinking_mode else None,
            model=model or "deepseek-v4-pro",
            usage=ChatUsage(input_tokens=200, cached_tokens=50, output_tokens=400),
            finish_reason="stop",
            cached=False,
        )


# ============================================================ retention


def test_phase_2_failure_keeps_work_dir_and_partial_artifacts(tmp_path: Path) -> None:
    _setup_project(tmp_path)
    config = _config(tmp_path)

    # Force Phase 2 to raise after Phase 0/1 wrote their artifacts.
    with patch(
        "generator.c_pipeline.orchestrator.phase2_outline.run_outline",
        side_effect=RuntimeError("phase 2 boom"),
    ):
        with pytest.raises(PipelineError):
            run_pipeline(config=config, client=_MockClient())

    db = get_database_path(config)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT id, status, current_phase, work_dir FROM stories ORDER BY id DESC LIMIT 1"
        ).fetchone()
    sid, status, current_phase, work_dir_str = row
    assert status == "failed"
    assert "phase_2" in current_phase
    assert current_phase.startswith("failed_at_phase_2")

    # Decision #12: work_dir must NOT be cleaned up.
    work = Path(work_dir_str)
    assert work.exists(), f"work_dir disappeared: {work}"
    pitch = work / "0_选题.json"
    framework = work / "1_设定.md"
    assert pitch.exists(), "phase 0 artifact 0_选题.json should be retained"
    assert framework.exists(), "phase 1 artifact 1_设定.md should be retained"

    # Files are readable and parsable.
    pitch_data = json.loads(pitch.read_text(encoding="utf-8"))
    assert isinstance(pitch_data, dict)
    framework_text = framework.read_text(encoding="utf-8")
    assert framework_text.strip(), "1_设定.md should not be empty"

    # Phase 2 artifact (2_小节大纲.md) should not exist (failure happened before write).
    outline = work / "2_小节大纲.md"
    assert not outline.exists()


def test_resume_from_phase_2_completes_with_partial_artifacts(tmp_path: Path) -> None:
    """After a Phase 2 failure leaves partial artifacts, ``--resume-from
    phase_2`` should rerun Phase 2 onwards and finish the pipeline."""
    _setup_project(tmp_path)
    config = _config(tmp_path)

    # Drive a Phase 2 failure first.
    with patch(
        "generator.c_pipeline.orchestrator.phase2_outline.run_outline",
        side_effect=RuntimeError("phase 2 boom"),
    ):
        with pytest.raises(PipelineError):
            run_pipeline(config=config, client=_MockClient())

    db = get_database_path(config)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT id, work_dir FROM stories ORDER BY id DESC LIMIT 1"
        ).fetchone()
    sid, work_dir_str = row
    work = Path(work_dir_str)
    assert (work / "0_选题.json").exists()
    assert (work / "1_设定.md").exists()

    # Resume — no patch this time so Phase 2 actually runs.
    result = run_pipeline(
        story_id=sid,
        config=config,
        client=_MockClient(),
        resume_from="phase_2",
    )

    assert result.story_id == sid
    assert result.final_phase == "phase_5_done"
    assert result.status in {"pending", "needs_human"}
    assert result.final_content_path is not None and Path(result.final_content_path).exists()

    # Phase 2 artifact must now exist, and Phase 5 must have written final.
    assert (work / "2_小节大纲.md").exists()
    assert (work / "5_最终稿.md").exists()

    # Story row updated.
    refreshed = get_story(db, sid)
    assert refreshed is not None
    assert refreshed.status in {"pending", "needs_human"}
    assert refreshed.current_phase == "phase_5_done"
    assert refreshed.final_content_path
    assert Path(refreshed.final_content_path).exists()


def test_phase_3_failure_keeps_phase_2_artifact(tmp_path: Path) -> None:
    """Symmetry check: a later-stage failure also retains earlier outputs."""
    _setup_project(tmp_path)
    config = _config(tmp_path)

    with patch(
        "generator.c_pipeline.orchestrator.phase3_sections.run_sections",
        side_effect=RuntimeError("phase 3 boom"),
    ):
        with pytest.raises(PipelineError):
            run_pipeline(config=config, client=_MockClient())

    db = get_database_path(config)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT status, current_phase, work_dir FROM stories ORDER BY id DESC LIMIT 1"
        ).fetchone()
    status, current_phase, work_dir_str = row
    assert status == "failed"
    assert "phase_3" in current_phase

    work = Path(work_dir_str)
    for filename in ("0_选题.json", "1_设定.md", "2_小节大纲.md"):
        assert (work / filename).exists(), f"Phase 0-2 artifact missing: {filename}"
