"""Integration tests for the autopilot API glue (Phase 1).

The pure orchestration is covered by ``test_autopilot.py``. This module covers
the DB-coupled pieces the API layer injects and exposes:

- ``_finalize_book_setup`` — builds the chapter queue from the generated 细纲,
  flips the book to ``writing``, and is idempotent (never clobbers a draft).
- ``GET /autopilot/status`` (``api_autopilot_status``) — idle → running snapshot
  round-trip and 404 for an unknown book.

Functions are called directly (no HTTP layer) so the tests stay fast and
deterministic; both are synchronous.
"""

from __future__ import annotations

import json
import os
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from generator.long_novel import db as ln_db
from generator.long_novel import api as ln_api
from generator.long_novel import l2_chapter_write as l2_write
from generator.long_novel.api import (
    _autopilot_chapters_to_write,
    _autopilot_chapters_to_write_range,
    _autopilot_job_mark,
    _autopilot_write_one_chapter,
    _finalize_book_setup,
    _inferred_setup_phase_status,
    _sync_paused_autopilot_snapshot,
    api_autopilot_status,
    api_rewrite_chapter,
    api_write_chapter_step_output,
)
from generator.long_novel.autopilot import write_autopilot_file

_REVIEW_DIMS = ["continuity", "logic", "plot_progress", "character_integrity", "environment", "empathy"]


def _review_json(verdict: str) -> str:
    """A story-review payload that normalizes to passed (APPROVE) / not-passed."""
    if verdict == "APPROVE":
        dims = {k: {"verdict": "APPROVE", "score": 92, "strengths": [], "findings": [], "recommendations": []} for k in _REVIEW_DIMS}
        return json.dumps({"overall": "APPROVE", "score": 92, "pass_score": 80, "dimensions": dims, "recommendations": []})
    dims = {k: {"verdict": "APPROVE", "score": 90, "strengths": [], "findings": [], "recommendations": []} for k in _REVIEW_DIMS}
    dims["logic"] = {"verdict": "CONCERNS", "score": 70, "strengths": [], "findings": ["第二段存在逻辑矛盾问题"], "recommendations": ["必须补足因果"]}
    return json.dumps({"overall": "CONCERNS", "score": 70, "pass_score": 80, "dimensions": dims, "recommendations": []})


class _ReviewFakeClient:
    """Offline DeepSeek stand-in: review JSON for review calls, prose otherwise."""

    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.review_calls = 0
        self.settings = SimpleNamespace(model="m", flash_model="f")

    def chat_completion(self, messages, **kwargs):  # noqa: ANN001, ANN003
        user = str(messages[-1]["content"])
        if "Review chapter" in user or "six dimensions" in user:
            verdict = self.verdicts[min(self.review_calls, len(self.verdicts) - 1)]
            self.review_calls += 1
            return SimpleNamespace(text=_review_json(verdict))
        if "summary_short" in user:  # tracking-memory extraction
            return SimpleNamespace(text='{"summary_short":"测试摘要","summary_long":"较长的测试章节摘要内容。"}')
        return SimpleNamespace(text="这是用于测试的章节正文内容。" * 6)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Minimal ANP_CONFIG + initialised long-novel schema on a temp SQLite db."""
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "anp.sqlite3"
    cfg_path.write_text(
        f"""
deepseek:
  api_key: ""
runtime:
  mode: "semi-auto"
  dry_run: true
database:
  sqlite_path: "{str(db_path).replace(chr(92), "/")}"
logging:
  file: "{str(tmp_path / "anp.log").replace(chr(92), "/")}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_CONFIG", str(cfg_path))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    ln_db.initialize_long_novel_tables(db_path)
    return {"cfg": cfg_path, "db": db_path}


def _make_book(env: dict[str, Path], tmp_path: Path, *, target_chapters: int = 3) -> tuple[int, dict, Path]:
    """Create a book row + a work_dir whose 大纲 has 细纲 files for every chapter."""
    work_dir = tmp_path / "book_work"
    outline_dir = work_dir / "大纲"
    outline_dir.mkdir(parents=True)
    for ch in range(1, target_chapters + 1):
        (outline_dir / f"细纲_第{ch:03d}章.md").write_text(f"第{ch}章细纲", encoding="utf-8")

    book_id = ln_db.create_book(
        env["db"],
        "测试书",
        genre="都市",
        premise="一句话梗概",
        target_chapters=target_chapters,
        target_words_per_chapter=1000,
        work_dir=str(work_dir),
    )
    book = ln_db.get_book(env["db"], book_id)
    assert book is not None
    return book_id, book, work_dir


# ── _finalize_book_setup ──────────────────────────────────────────────


def test_finalize_book_setup_builds_chapter_queue(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=3)

    _finalize_book_setup(book_id, book, work_dir)

    chapters = ln_db.list_chapters(env["db"], book_id)
    assert [c["chapter_number"] for c in chapters] == [1, 2, 3]
    assert all(c["status"] == "outline_only" for c in chapters)
    assert all(c["outline_path"] and c["outline_path"].endswith(".md") for c in chapters)

    volumes = ln_db.list_volumes(env["db"], book_id)
    assert len(volumes) == 1
    assert volumes[0]["status"] == "outlined"
    assert volumes[0]["chapter_count"] == 3

    refreshed = ln_db.get_book(env["db"], book_id)
    assert refreshed["status"] == "writing"
    assert refreshed["total_volumes"] == 1


def test_finalize_book_setup_preserves_existing_drafts(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=3)

    # Chapter 2 already has a written draft — finalize must not clobber it.
    ln_db.upsert_chapter(
        env["db"],
        book_id,
        volume_number=1,
        chapter_number=2,
        title="已写好的第二章",
        status="reviewed",
        draft_path="/some/draft/第002章.md",
        actual_words=1234,
    )

    _finalize_book_setup(book_id, book, work_dir)

    ch2 = ln_db.get_chapter(env["db"], book_id, 2)
    assert ch2["draft_path"] == "/some/draft/第002章.md"
    assert ch2["status"] == "reviewed"
    assert ch2["actual_words"] == 1234

    # The other chapters were still queued.
    assert {c["chapter_number"] for c in ln_db.list_chapters(env["db"], book_id)} == {1, 2, 3}


def test_finalize_book_setup_is_rerunnable(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)

    _finalize_book_setup(book_id, book, work_dir)
    _finalize_book_setup(book_id, book, work_dir)  # second run must not duplicate rows

    chapters = ln_db.list_chapters(env["db"], book_id)
    assert [c["chapter_number"] for c in chapters] == [1, 2]
    assert len(ln_db.list_volumes(env["db"], book_id)) == 1


# ── _autopilot_chapters_to_write ──────────────────────────────────────


def test_autopilot_chapters_to_write_picks_next_unwritten(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=5)
    _finalize_book_setup(book_id, book, work_dir)

    # Chapter 1 already has a draft → it must be skipped; pick the next two.
    ln_db.upsert_chapter(
        env["db"],
        book_id,
        1,
        1,
        title="第1章",
        status="draft",
        draft_path="/x/正文.md",
        actual_words=100,
        outline_path=str(work_dir / "大纲" / "细纲_第001章.md"),
    )

    assert _autopilot_chapters_to_write(env["db"], book_id, 2) == [2, 3]
    assert _autopilot_chapters_to_write(env["db"], book_id, 99) == [2, 3, 4, 5]


def test_autopilot_chapters_to_write_range_refuses_skipping_unwritten(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=15)
    _finalize_book_setup(book_id, book, work_dir)

    with pytest.raises(HTTPException) as exc:
        _autopilot_chapters_to_write_range(env["db"], book_id, 11, 15)

    assert exc.value.status_code == 400
    assert "第1章开始连续写" in str(exc.value.detail)


def test_autopilot_chapters_to_write_range_allows_contiguous_after_existing_drafts(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=15)
    _finalize_book_setup(book_id, book, work_dir)

    for chapter_number in range(1, 11):
        ln_db.upsert_chapter(
            env["db"],
            book_id,
            1,
            chapter_number,
            title=f"第{chapter_number}章",
            status="draft",
            draft_path=f"/x/第{chapter_number:03d}章.md",
            actual_words=100,
            outline_path=str(work_dir / "大纲" / f"细纲_第{chapter_number:03d}章.md"),
        )

    assert _autopilot_chapters_to_write_range(env["db"], book_id, 11, 15) == [11, 12, 13, 14, 15]


# ── _autopilot_write_one_chapter ──────────────────────────────────────


def test_autopilot_write_one_chapter_passes_first_try(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["APPROVE"])

    result = _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, lambda *a, **k: None, max_revisions=3)

    assert result["status"] == "passed"
    assert result["revisions"] == 0
    assert client.review_calls == 1  # reviewed once, no rewrite

    ch = ln_db.get_chapter(env["db"], book_id, 1)
    assert ch["status"] == "draft"
    assert ch["review_status"] == "APPROVE"
    assert ch["draft_path"] and Path(ch["draft_path"]).exists()
    assert ch["actual_words"] > 0
    # tracking memory refreshed so the next chapter has continuity
    assert (work_dir / "追踪" / "全书进展.md").exists()


def test_autopilot_write_one_chapter_keeps_low_score_as_reference_without_retry(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["CONCERNS"])  # never passes

    reports: list[tuple[str, str, int]] = []

    def report(status: str, detail: str = "", revisions: int = 0) -> None:
        reports.append((status, detail, revisions))

    result = _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, report, max_revisions=3)

    assert result["status"] == "passed"
    assert result["revisions"] == 0
    assert result["reason"]
    assert result["review_reference_only"] is True
    assert client.review_calls == 1
    assert not any(s == "revising" for s, _, _ in reports)

    ch = ln_db.get_chapter(env["db"], book_id, 1)
    assert ch["status"] == "draft"
    assert ch["review_status"] == "CONCERNS"
    # The draft is saved + tracking updated so writing can continue immediately.
    assert ch["draft_path"] and Path(ch["draft_path"]).exists()
    assert (work_dir / "追踪" / "全书进展.md").exists()


def test_autopilot_write_one_chapter_ignores_legacy_max_revisions_argument(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["CONCERNS", "APPROVE"])  # fail once, then pass

    result = _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, lambda *a, **k: None, max_revisions=3)

    assert result["status"] == "passed"
    assert result["revisions"] == 0
    assert client.review_calls == 1
    assert ln_db.get_chapter(env["db"], book_id, 1)["status"] == "draft"


def test_autopilot_step_output_falls_back_to_saved_final_draft(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["APPROVE"])

    _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, lambda *a, **k: None)

    output = api_write_chapter_step_output(book_id, 1, "draft")
    assert output["ok"] is True
    assert output["fallback_from_final"] is True
    assert output["content"]
    assert output["word_count"] > 0


# ── api_autopilot_status ──────────────────────────────────────────────


def test_autopilot_status_idle_then_running(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, _book, work_dir = _make_book(env, tmp_path)

    assert api_autopilot_status(book_id) == {"ok": True, "state": "idle"}

    write_autopilot_file(work_dir, {"state": "running", "stage": "world", "detail": "正在生成：世界观"})

    status = api_autopilot_status(book_id)
    assert status["ok"] is True
    assert status["state"] == "running"
    assert status["stage"] == "world"


def test_autopilot_status_reports_writing_phase(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, _book, work_dir = _make_book(env, tmp_path)

    write_autopilot_file(
        work_dir,
        {
            "state": "running",
            "phase": "writing",
            "completed": ["premise", "world", "finalize"],
            "writing": {"total": 3, "done": 1, "current": 2, "current_status": "reviewing", "needs_human": []},
        },
    )

    status = api_autopilot_status(book_id)
    assert status["state"] == "running"
    assert status["phase"] == "writing"
    assert status["writing"]["done"] == 1
    assert status["writing"]["current"] == 2


def test_autopilot_status_does_not_cancel_active_long_stage(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, _book, work_dir = _make_book(env, tmp_path)
    write_autopilot_file(work_dir, {"state": "running", "stage": "world", "detail": "正在生成：世界观"})
    progress_file = work_dir / ".setup" / "_autopilot.json"
    stale = time.time() - 600
    os.utime(progress_file, (stale, stale))

    _autopilot_job_mark(book_id, True)
    try:
        assert api_autopilot_status(book_id)["state"] == "running"
    finally:
        _autopilot_job_mark(book_id, False)

    assert api_autopilot_status(book_id)["state"] == "cancelled"


def test_autopilot_status_unknown_book_404(env: dict[str, Path]) -> None:
    with pytest.raises(HTTPException) as exc:
        api_autopilot_status(999999)
    assert exc.value.status_code == 404


def test_sync_paused_autopilot_snapshot_advances_after_manual_phase(
    env: dict[str, Path],
    tmp_path: Path,
) -> None:
    book_id, _book, work_dir = _make_book(env, tmp_path)
    setup_dir = work_dir / ".setup"
    setup_dir.mkdir(parents=True, exist_ok=True)
    (setup_dir / "_setup_factions.json").write_text(
        json.dumps({"status": "done", "detail": "手动生成完成"}, ensure_ascii=False),
        encoding="utf-8",
    )
    _autopilot_job_mark(book_id, False)

    synced = _sync_paused_autopilot_snapshot(
        book_id,
        work_dir,
        {
            "state": "cancelled",
            "stage": "factions",
            "label": "势力",
            "completed": ["premise", "world", "characters"],
        },
    )

    assert synced["completed"] == ["premise", "world", "characters", "factions"]
    assert synced["stage"] == "relations"
    assert synced["label"] == "关系"
    assert synced["detail"] == "已同步手动生成结果，可继续全自动"


def test_sync_paused_autopilot_snapshot_finishes_when_all_phases_exist(
    env: dict[str, Path],
    tmp_path: Path,
) -> None:
    book_id, _book, work_dir = _make_book(env, tmp_path)
    setup_dir = work_dir / ".setup"
    setup_dir.mkdir(parents=True, exist_ok=True)
    phases = [
        "premise",
        "world",
        "characters",
        "factions",
        "relations",
        "outline",
        "volume_outline",
        "chapter_outlines",
        "finalize",
    ]
    for phase in phases:
        (setup_dir / f"_setup_{phase}.json").write_text(
            json.dumps({"status": "done", "detail": "done"}),
            encoding="utf-8",
        )
    _autopilot_job_mark(book_id, False)

    synced = _sync_paused_autopilot_snapshot(
        book_id,
        work_dir,
        {
            "state": "cancelled",
            "stage": "finalize",
            "label": "入库",
            "completed": phases[:-1],
        },
    )

    assert synced["state"] == "done"
    assert synced["stage"] == ""
    assert synced["label"] == ""
    assert synced["stage_status"] == "done"
    assert synced["detail"] == "全自动生成完成"
    assert synced["completed"] == phases

    stale_done = _sync_paused_autopilot_snapshot(
        book_id,
        work_dir,
        {
            "state": "cancelled",
            "stage": "",
            "label": "",
            "completed": phases,
        },
    )
    assert stale_done["state"] == "done"


def test_inferred_setup_phase_status_uses_autopilot_completed_snapshot(tmp_path: Path) -> None:
    write_autopilot_file(
        tmp_path,
        {
            "state": "running",
            "stage": "chapter_outlines",
            "detail": "正在生成章节细纲",
            "completed": ["outline", "volume_outline"],
            "updated_at": "15:00:00",
        },
    )

    assert _inferred_setup_phase_status(tmp_path, "outline") == {
        "status": "done",
        "detail": "全自动生成已完成",
        "updated_at": "15:00:00",
    }
    assert _inferred_setup_phase_status(tmp_path, "volume_outline")["status"] == "done"
    assert _inferred_setup_phase_status(tmp_path, "chapter_outlines")["status"] == "running"


def test_rewrite_chapter_uses_existing_source_and_preserves_latest_progress(
    env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    first = ln_db.get_chapter(env["db"], book_id, 1)
    second = ln_db.get_chapter(env["db"], book_id, 2)
    assert first and second

    first_path = l2_write.chapter_final_path(work_dir, 1, first["title"])
    second_path = l2_write.chapter_final_path(work_dir, 2, second["title"])
    first_path.write_text("# 第1章\n\n第一章原稿", encoding="utf-8")
    second_path.write_text("# 第2章\n\n第二章原稿", encoding="utf-8")
    for chapter, path in ((first, first_path), (second, second_path)):
        ln_db.upsert_chapter(
            env["db"],
            book_id,
            int(chapter["volume_number"]),
            int(chapter["chapter_number"]),
            title=str(chapter["title"]),
            status="draft",
            target_words=int(chapter["target_words"]),
            actual_words=100,
            outline_path=chapter["outline_path"],
            draft_path=str(path),
            review_status="APPROVE",
            ai_review_json="{}",
        )

    captured: dict[str, object] = {}
    monkeypatch.setattr(ln_api, "_deepseek_client", lambda _book: object())
    monkeypatch.setattr(
        l2_write,
        "rewrite_chapter_from_source",
        lambda _client, source, chapter_number, _title, _outline: (
            captured.update(source=source, chapter_number=chapter_number) or "# 第6章\n\n改稿"
        ),
    )
    monkeypatch.setattr(l2_write, "run_polish", lambda _client, text: text)
    monkeypatch.setattr(l2_write, "run_deslop", lambda _client, text: text)
    monkeypatch.setattr(
        l2_write,
        "run_continuity_check",
        lambda *_args, **_kwargs: {"issue_count": 0, "issues": []},
    )
    monkeypatch.setattr(
        l2_write,
        "update_tracking_files",
        lambda *_args, **kwargs: captured.update(advance_current=kwargs["advance_current"]),
    )

    result = asyncio.run(api_rewrite_chapter(book_id, 1))
    updated = ln_db.get_chapter(env["db"], book_id, 1)
    assert updated

    assert result["ok"] is True
    assert captured["source"] == "# 第1章\n\n第一章原稿"
    assert captured["chapter_number"] == 1
    assert captured["advance_current"] is False
    assert first_path.read_text(encoding="utf-8") == "# 第1章\n\n改稿\n"
    assert first_path.with_suffix(".md.bak").read_text(encoding="utf-8") == "# 第1章\n\n第一章原稿"
    assert updated["title"] == first["title"]
    assert updated["outline_path"] == first["outline_path"]
