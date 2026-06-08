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
from typing import Any

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
    _autopilot_job_active,
    _autopilot_job_mark,
    _autopilot_write_one_chapter,
    _finalize_book_setup,
    _inferred_setup_phase_status,
    _repair_invalid_autopilot_writing_snapshot,
    _sync_paused_autopilot_snapshot,
    api_autopilot_status,
    api_reset_chapter_for_regeneration,
    api_reset_chapter_range_for_regeneration,
    api_rewrite_chapter,
    api_rewrite_chapter_range,
    api_write_chapter_step_output,
)
from generator.long_novel.autopilot import read_autopilot_file, write_autopilot_file

_REVIEW_DIMS = ["continuity", "logic", "plot_progress", "character_integrity", "environment", "empathy"]


class _PayloadRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def json(self) -> dict[str, Any]:
        return self.payload


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
    """Minimal ANW_CONFIG + initialised long-novel schema on a temp SQLite db."""
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "anw.sqlite3"
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
  file: "{str(tmp_path / "anw.log").replace(chr(92), "/")}"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANW_CONFIG", str(cfg_path))
    monkeypatch.setenv("ANW_SQLITE_PATH", str(db_path))
    ln_db.initialize_long_novel_tables(db_path)
    return {"cfg": cfg_path, "db": db_path}


def _make_book(
    env: dict[str, Path],
    tmp_path: Path,
    *,
    target_chapters: int = 3,
    target_words_per_chapter: int = 1000,
) -> tuple[int, dict, Path]:
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
        target_words_per_chapter=target_words_per_chapter,
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


def test_draft_step_can_rerun_after_chapter_is_draft(
    env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=1)
    _finalize_book_setup(book_id, book, work_dir)

    calls = {"n": 0}

    def fake_run_draft(*_args: Any, **_kwargs: Any) -> str:
      calls["n"] += 1
      return f"第{calls['n']}次初稿正文"

    monkeypatch.setattr(ln_api, "_deepseek_client", lambda _book: object())
    monkeypatch.setattr(l2_write, "run_draft", fake_run_draft)

    first = ln_api._api_write_chapter_step_blocking(book_id, 1, "draft")
    assert first["run_count"] == 1

    ch = ln_db.get_chapter(env["db"], book_id, 1)
    assert ch is not None
    ln_db.upsert_chapter(
        env["db"],
        book_id,
        int(ch.get("volume_number") or 1),
        1,
        title=str(ch.get("title") or ""),
        status="draft",
        target_words=int(ch.get("target_words") or 1000),
        actual_words=int(ch.get("actual_words") or 0),
        outline_path=ch.get("outline_path"),
    )

    second = ln_api._api_write_chapter_step_blocking(book_id, 1, "draft")
    assert second["ok"] is True
    assert second["run_count"] == 2
    assert second["content"] == "第2次初稿正文"

    status = ln_api.api_write_chapter_step_status(book_id, 1)
    draft_status = next(item for item in status["steps_progress"] if item["step"] == "draft")
    assert draft_status["run_count"] == 2

    history = ln_api.api_list_step_history(book_id, 1, "draft")
    assert len(history["versions"]) == 1


# ── _autopilot_chapters_to_write ──────────────────────────────────────


def test_rerunning_draft_invalidates_downstream_outputs(
    env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=1)
    _finalize_book_setup(book_id, book, work_dir)
    ch = ln_db.get_chapter(env["db"], book_id, 1)
    assert ch is not None

    chapter_folder = l2_write.chapter_dir(work_dir, 1, str(ch.get("title") or ""))
    chapter_folder.mkdir(parents=True, exist_ok=True)
    for step in ("expand", "polish", "deslop"):
        (chapter_folder / l2_write.CHAPTER_STEP_FILES[step]).write_text(f"stale {step}", encoding="utf-8")
    (chapter_folder / l2_write.CHAPTER_STEP_FILES["review"]).write_text("{}", encoding="utf-8")
    final_path = ln_api.chapter_final_path(work_dir, 1, str(ch.get("title") or ""))
    final_path.write_text("stale final", encoding="utf-8")
    ln_db.upsert_chapter(
        env["db"],
        book_id,
        int(ch.get("volume_number") or 1),
        1,
        title=str(ch.get("title") or ""),
        status="draft",
        target_words=int(ch.get("target_words") or 1000),
        actual_words=999,
        draft_path=str(final_path),
        outline_path=ch.get("outline_path"),
        review_status="APPROVE",
        ai_review_json="{}",
    )

    monkeypatch.setattr(ln_api, "_deepseek_client", lambda _book: object())
    monkeypatch.setattr(l2_write, "run_draft", lambda *_args, **_kwargs: "fresh draft")

    result = ln_api._api_write_chapter_step_blocking(book_id, 1, "draft")

    assert result["ok"] is True
    for step in ("expand", "polish", "deslop", "review"):
        assert not (chapter_folder / l2_write.CHAPTER_STEP_FILES[step]).exists()
        assert (chapter_folder / "_history" / "invalidated" / step).exists()
    assert not final_path.exists()
    assert list(final_path.parent.glob(f"{final_path.stem}.*.md.bak"))
    refreshed = ln_db.get_chapter(env["db"], book_id, 1)
    assert refreshed is not None
    assert refreshed["status"] == "writing"
    assert refreshed["actual_words"] == 0
    assert not refreshed["draft_path"]
    assert not refreshed["review_status"]
    assert not refreshed["ai_review_json"]


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
    chapter_dir = l2_write.chapter_dir(work_dir, 1, ch["title"])
    assert (chapter_dir / "初稿.md").exists()
    assert (chapter_dir / "扩写.md").exists()
    assert (chapter_dir / "润色.md").exists()
    assert (chapter_dir / "去AI.md").exists()
    assert (chapter_dir / "审查.json").exists()
    assert (chapter_dir / "正文.md").exists()


def test_autopilot_write_one_chapter_revises_review_issues_before_human_fallback(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["CONCERNS"])  # never passes

    reports: list[tuple[str, str, int]] = []

    def report(status: str, detail: str = "", revisions: int = 0) -> None:
        reports.append((status, detail, revisions))

    result = _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, report, max_revisions=3)

    assert result["status"] == "needs_human"
    assert result["revisions"] == 3
    assert result["reason"]
    assert client.review_calls == 4
    assert any(s == "revising" for s, _, _ in reports)

    ch = ln_db.get_chapter(env["db"], book_id, 1)
    assert ch["status"] == "needs_human"
    assert ch["review_status"] == "CONCERNS"
    # The draft is saved + tracking updated so writing can continue immediately,
    # while the unresolved gate remains visible for human follow-up.
    assert ch["draft_path"] and Path(ch["draft_path"]).exists()
    assert (work_dir / "追踪" / "全书进展.md").exists()


def test_autopilot_write_one_chapter_applies_review_fix_until_passed(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["CONCERNS", "APPROVE"])  # fail once, then pass

    result = _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, lambda *a, **k: None, max_revisions=3)

    assert result["status"] == "passed"
    assert result["revisions"] == 1
    assert client.review_calls == 2
    assert ln_db.get_chapter(env["db"], book_id, 1)["status"] == "draft"


def test_review_revise_returns_revised_content_and_auto_rechecks(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["CONCERNS", "APPROVE"])

    for step_name in ("draft", "expand", "polish", "deslop", "review"):
        ln_api._api_write_chapter_step_blocking(book_id, 1, step_name, client=client)

    result = ln_api._api_revise_chapter_step_blocking(
        book_id,
        1,
        "review",
        {},
        client=client,
    )

    assert result["ok"] is True
    assert result["step"] == "review"
    assert result["revised_step"] == "deslop"
    assert result["content"]
    assert result["revised_content"] == result["content"]
    assert result["review"]["passed"] is True
    assert result["review"]["revision_audit"]["mode"] == "review_fix_then_auto_recheck"
    assert result["batch_count"] >= 1
    assert client.review_calls == 2

    saved = api_write_chapter_step_output(book_id, 1, "review")
    assert saved["review"]["passed"] is True
    assert saved["revised_content"] == result["content"]
    assert saved["revised_word_count"] == result["word_count"]


def test_review_revise_start_persists_progress_until_done(
    env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["CONCERNS", "APPROVE"])
    monkeypatch.setattr(ln_api, "_deepseek_client", lambda _book: client)

    for step_name in ("draft", "expand", "polish", "deslop", "review"):
        ln_api._api_write_chapter_step_blocking(book_id, 1, step_name, client=client)

    accepted = asyncio.run(
        ln_api.api_start_revise_chapter_step(
            book_id,
            1,
            "review",
            _PayloadRequest({"prompt": ""}),
        )
    )
    deadline = time.time() + 3
    status: dict[str, Any] = {}
    while time.time() < deadline:
        status = ln_api.api_revise_chapter_step_progress(book_id, 1, "review")
        if status["status"] == "done":
            break
        time.sleep(0.01)

    assert accepted["accepted"] is True
    assert accepted["progress_step"] == "review_revise"
    assert status["status"] == "done"
    assert status["step"] == "review"
    assert status["progress_step"] == "review_revise"
    assert status["result"]["review_passed"] is True
    assert api_write_chapter_step_output(book_id, 1, "review")["review"]["passed"] is True


def test_autopilot_step_output_uses_saved_intermediate_artifacts(env: dict[str, Path], tmp_path: Path) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    client = _ReviewFakeClient(["APPROVE"])

    _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, lambda *a, **k: None)

    output = api_write_chapter_step_output(book_id, 1, "draft")
    assert output["ok"] is True
    assert "fallback_from_final" not in output
    assert output["content"]
    assert output["word_count"] > 0

    for step_name in ("expand", "polish", "deslop", "review", "finalize"):
        assert api_write_chapter_step_output(book_id, 1, step_name)["ok"] is True


def test_autopilot_skips_expand_after_target_but_still_runs_polish_and_deslop(
    env: dict[str, Path],
    tmp_path: Path,
) -> None:
    book_id, book, work_dir = _make_book(
        env,
        tmp_path,
        target_chapters=1,
        target_words_per_chapter=3000,
    )
    _finalize_book_setup(book_id, book, work_dir)

    class LongDraftClient(_ReviewFakeClient):
        def chat_completion(self, messages, **kwargs):  # noqa: ANN001, ANN003
            user = str(messages[-1]["content"])
            if "Review chapter" in user or "six dimensions" in user or "summary_short" in user:
                return super().chat_completion(messages, **kwargs)
            return SimpleNamespace(text="足够长的正文。" * 700)

    client = LongDraftClient(["APPROVE"])
    _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, lambda *a, **k: None)

    expand = api_write_chapter_step_output(book_id, 1, "expand")
    assert expand["skipped"] is True
    assert expand["word_count"] >= 3000

    ch = ln_db.get_chapter(env["db"], book_id, 1)
    chapter_dir = l2_write.chapter_dir(work_dir, 1, ch["title"])
    assert not (chapter_dir / "扩写.md").exists()
    assert (chapter_dir / ".skip_expand.json").exists()
    assert (chapter_dir / "润色.md").exists()
    assert (chapter_dir / "去AI.md").exists()
    assert (chapter_dir / "正文.md").exists()


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


def test_sync_paused_autopilot_snapshot_preserves_failed_writing(
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
            "state": "error",
            "stage": "writing",
            "completed": phases,
            "failed_at": 1,
            "writing": {"total": 3, "done": 0, "current": 1, "current_status": "error"},
        },
    )

    assert synced["state"] == "error"
    assert synced["writing"]["done"] == 0
    assert synced["failed_at"] == 1


def test_repair_invalid_autopilot_writing_snapshot_marks_error(tmp_path: Path) -> None:
    repaired = _repair_invalid_autopilot_writing_snapshot(
        tmp_path,
        {
            "state": "done",
            "detail": "全自动生成完成",
            "failed_at": 1,
            "writing": {"total": 3, "done": 0, "current": 1, "current_status": "error"},
        },
    )

    assert repaired["state"] == "error"
    assert repaired["stage"] == "writing"
    assert repaired["failed_at"] == 1
    assert "0/3" in repaired["detail"]


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
    chapter_folder = l2_write.chapter_dir(work_dir, 1, first["title"])
    assert (chapter_folder / l2_write.CHAPTER_STEP_FILES["draft"]).exists()
    assert (chapter_folder / l2_write.CHAPTER_STEP_FILES["polish"]).exists()
    assert (chapter_folder / l2_write.CHAPTER_STEP_FILES["deslop"]).exists()
    assert result["batch_count"] >= 1


def test_reset_chapter_archives_outputs_and_clears_db_for_fresh_generation(
    env: dict[str, Path],
    tmp_path: Path,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    first = ln_db.get_chapter(env["db"], book_id, 1)
    second = ln_db.get_chapter(env["db"], book_id, 2)
    assert first and second

    first_dir = l2_write.chapter_dir(work_dir, 1, first["title"])
    final_path = first_dir / "正文.md"
    final_path.write_text("# 第1章\n\n旧正文", encoding="utf-8")
    (first_dir / "初稿.md").write_text("旧初稿", encoding="utf-8")
    (first_dir / "润色.md").write_text("旧润色", encoding="utf-8")
    (first_dir / "去AI.md").write_text("旧去AI", encoding="utf-8")
    (first_dir / "审查.json").write_text('{"overall":"APPROVE"}', encoding="utf-8")
    (first_dir / ".skip_expand.json").write_text('{"skipped":true}', encoding="utf-8")
    second_path = l2_write.chapter_final_path(work_dir, 2, second["title"])
    second_path.write_text("# 第2章\n\n第二章正文", encoding="utf-8")
    for chapter, path in ((first, final_path), (second, second_path)):
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

    tracking_dir = work_dir / "追踪"
    tracking_dir.mkdir(exist_ok=True)
    (tracking_dir / "全书进展.md").write_text(
        "## 全书进展\n\n"
        "- 当前进度：第2章已完成\n\n"
        "## 第1章\n- 摘要：第一章旧追踪\n\n"
        "## 第2章\n- 摘要：第二章追踪\n",
        encoding="utf-8",
    )
    (tracking_dir / "伏笔.md").write_text(
        "## 伏笔状态表\n\n"
        "## 第1章\n- 第一章伏笔\n\n"
        "## 第2章\n- 第二章伏笔\n",
        encoding="utf-8",
    )
    write_autopilot_file(work_dir, {"state": "done", "stage": "writing", "detail": "全自动生成完成"})

    result = asyncio.run(api_reset_chapter_for_regeneration(book_id, 1))
    reset = ln_db.get_chapter(env["db"], book_id, 1)
    assert reset

    assert result["ok"] is True
    assert result["later_written_chapters"] == [2]
    assert reset["status"] == "outline_only"
    assert reset["draft_path"] is None
    assert reset["actual_words"] == 0
    assert reset["review_status"] is None
    assert not final_path.exists()
    assert not (first_dir / "初稿.md").exists()
    assert not (first_dir / "润色.md").exists()
    assert not (first_dir / "去AI.md").exists()
    assert not (first_dir / "审查.json").exists()
    assert not (first_dir / ".skip_expand.json").exists()
    archive_dir = work_dir / result["archive_dir"]
    assert archive_dir.exists()
    assert (archive_dir / "正文.md").exists()
    assert (archive_dir / "初稿.md").exists()
    assert second_path.exists()
    progress = (tracking_dir / "全书进展.md").read_text(encoding="utf-8")
    foreshadowing = (tracking_dir / "伏笔.md").read_text(encoding="utf-8")
    assert "## 第1章" not in progress
    assert "第一章伏笔" not in foreshadowing
    assert "## 第2章" in progress
    assert "第二章伏笔" in foreshadowing
    assert (read_autopilot_file(work_dir) or {})["state"] == "idle"


def test_reset_chapter_range_archives_and_clears_each_selected_chapter(
    env: dict[str, Path],
    tmp_path: Path,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    chapters = ln_db.list_chapters(env["db"], book_id)
    for chapter in chapters:
        path = l2_write.chapter_final_path(work_dir, chapter["chapter_number"], chapter["title"])
        path.write_text("# chapter\n\nold draft", encoding="utf-8")
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
        )

    tracking_dir = work_dir / "追踪"
    tracking_dir.mkdir(exist_ok=True)
    (tracking_dir / "全书进展.md").write_text(
        "## 全书进展\n\n"
        "- 当前进度：第2章已完成\n\n"
        "## 第1章\n- 摘要：第一章旧追踪\n\n"
        "## 第2章\n- 摘要：第二章旧追踪\n",
        encoding="utf-8",
    )
    write_autopilot_file(work_dir, {"state": "done", "stage": "writing", "detail": "全自动生成完成"})

    result = asyncio.run(
        api_reset_chapter_range_for_regeneration(
            book_id,
            _PayloadRequest({"chapter_start": 1, "chapter_end": 2}),
        )
    )

    assert result["ok"] is True
    assert result["reset_chapters"] == [1, 2]
    assert result["skipped_chapters"] == []
    for chapter_number in (1, 2):
        reset = ln_db.get_chapter(env["db"], book_id, chapter_number)
        assert reset
        assert reset["status"] == "outline_only"
        assert reset["draft_path"] is None
    progress = (tracking_dir / "全书进展.md").read_text(encoding="utf-8")
    assert "当前进度：第0章尚未开始" in progress
    assert "## 第1章" not in progress
    assert "## 第2章" not in progress
    assert (ln_db.get_book(env["db"], book_id) or {})["current_chapter"] == 0
    assert (read_autopilot_file(work_dir) or {})["state"] == "idle"


def test_reset_chapter_range_cleans_tracking_when_outputs_already_deleted(
    env: dict[str, Path],
    tmp_path: Path,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    tracking_dir = work_dir / "追踪"
    tracking_dir.mkdir(exist_ok=True)
    (tracking_dir / "全书进展.md").write_text(
        "## 全书进展\n\n"
        "- 当前进度：第2章已完成\n\n"
        "## 第1章\n- 摘要：第一章残留追踪\n\n"
        "## 第2章\n- 摘要：第二章残留追踪\n",
        encoding="utf-8",
    )
    (tracking_dir / "伏笔.md").write_text(
        "## 伏笔状态表\n\n"
        "## 第1章\n- 第一章残留伏笔\n\n"
        "## 第2章\n- 第二章残留伏笔\n",
        encoding="utf-8",
    )
    write_autopilot_file(work_dir, {"state": "done", "stage": "writing", "detail": "全自动生成完成"})

    result = asyncio.run(
        api_reset_chapter_range_for_regeneration(
            book_id,
            _PayloadRequest({"chapter_start": 1, "chapter_end": 2}),
        )
    )

    assert result["ok"] is True
    assert result["reset_chapters"] == [1, 2]
    assert result["results"] == []
    progress = (tracking_dir / "全书进展.md").read_text(encoding="utf-8")
    foreshadowing = (tracking_dir / "伏笔.md").read_text(encoding="utf-8")
    assert "残留追踪" not in progress
    assert "残留伏笔" not in foreshadowing
    assert "当前进度：第0章尚未开始" in progress
    assert (read_autopilot_file(work_dir) or {})["state"] == "idle"


def test_rewrite_chapter_range_runs_in_background_and_reports_progress(
    env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    chapters = ln_db.list_chapters(env["db"], book_id)
    for chapter in chapters:
        path = l2_write.chapter_final_path(work_dir, chapter["chapter_number"], chapter["title"])
        path.write_text(f"# chapter {chapter['chapter_number']}\n\nold draft", encoding="utf-8")
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
        )

    monkeypatch.setattr(ln_api, "_deepseek_client", lambda _book: object())
    monkeypatch.setattr(
        l2_write,
        "rewrite_chapter_from_source",
        lambda _client, _source, chapter_number, _title, _outline: f"# chapter {chapter_number}\n\nrewritten",
    )
    monkeypatch.setattr(l2_write, "run_polish", lambda _client, text: text)
    monkeypatch.setattr(l2_write, "run_deslop", lambda _client, text: text)
    monkeypatch.setattr(
        l2_write,
        "run_continuity_check",
        lambda *_args, **_kwargs: {"issue_count": 0, "issues": []},
    )
    monkeypatch.setattr(l2_write, "update_tracking_files", lambda *_args, **_kwargs: None)

    accepted = asyncio.run(
        api_rewrite_chapter_range(
            book_id,
            _PayloadRequest({"chapter_start": 1, "chapter_end": 2}),
        )
    )
    deadline = time.time() + 3
    while _autopilot_job_active(book_id) and time.time() < deadline:
        time.sleep(0.01)
    snapshot = read_autopilot_file(work_dir)

    assert accepted["accepted"] is True
    assert snapshot
    assert snapshot["state"] == "done"
    assert snapshot["operation"] == "batch_rewrite"
    assert snapshot["writing"]["done"] == 2
    assert [item["status"] for item in snapshot["writing"]["results"]] == ["rewritten", "rewritten"]


def test_autopilot_regenerating_earlier_chapter_preserves_latest_progress(
    env: dict[str, Path],
    tmp_path: Path,
) -> None:
    book_id, book, work_dir = _make_book(env, tmp_path, target_chapters=2)
    _finalize_book_setup(book_id, book, work_dir)
    second = ln_db.get_chapter(env["db"], book_id, 2)
    assert second
    second_path = l2_write.chapter_final_path(work_dir, 2, second["title"])
    second_path.write_text("# 第2章\n\n第二章正文", encoding="utf-8")
    ln_db.upsert_chapter(
        env["db"],
        book_id,
        int(second["volume_number"]),
        2,
        title=str(second["title"]),
        status="draft",
        target_words=int(second["target_words"]),
        actual_words=100,
        outline_path=second["outline_path"],
        draft_path=str(second_path),
        review_status="APPROVE",
        ai_review_json="{}",
    )
    ln_db.update_book(env["db"], book_id, current_chapter=2)
    l2_write.refresh_tracking_head(work_dir, 2, "# 第2章\n\n第二章正文", summary_short="第二章摘要")

    client = _ReviewFakeClient(["APPROVE"])
    _autopilot_write_one_chapter(client, env["db"], book_id, book, work_dir, 1, lambda *a, **k: None)

    refreshed_book = ln_db.get_book(env["db"], book_id)
    assert refreshed_book
    assert refreshed_book["current_chapter"] == 2
    tracking_context = (work_dir / "追踪" / "上下文.md").read_text(encoding="utf-8")
    assert "当前进度：第2章已完成" in tracking_context
