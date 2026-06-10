"""全自动（autopilot）路由：一键跑完 设定 → 大纲 → 入库 →〔正文 × N〕，
以及实时进度 / 取消 / 恢复。

由 ``generator.long_novel.api`` 聚合进主 router。LLM 客户端经
``deps._deepseek_client(...)`` 模块属性获取（统一 monkeypatch 缝）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from generator.long_novel import deps
from generator.long_novel.chapter_resets import _cleanup_stale_step_outputs, _has_later_saved_chapter
from generator.long_novel.chapter_steps import (
    _api_revise_chapter_step_blocking,
    _api_write_chapter_step_blocking,
)
from generator.long_novel.db import (
    get_book,
    get_chapter,
    list_chapters,
    update_book,
    upsert_chapter,
)
from generator.long_novel.deps import _db_path, _json_payload, _upsert_chapter_preserving
from generator.long_novel.jobs import (
    _autopilot_job_active,
    _autopilot_job_mark,
    _is_cancelled,
    _set_cancel,
)
from generator.long_novel.l0_book_setup import setup_file_read
from generator.long_novel.review_gate import _review_rewrite_reason
from generator.long_novel.setup_routes import _finalize_book_setup

logger = logging.getLogger(__name__)

router = APIRouter()

_AUTOPILOT_DEFAULT_MAX_REVISIONS = 2
_AUTOPILOT_MAX_REVISIONS = 3


def _autopilot_chapters_to_write(db: Path, book_id: int, count: int) -> list[int]:
    """Return the next ``count`` chapter numbers that still need a draft.

    A chapter "needs a draft" when it has no ``draft_path`` yet, so chapters the
    autopilot already finished — including ones flagged ``needs_human`` (they do
    have a saved draft) — are not rewritten on a later run.
    """
    chapters = list_chapters(db, book_id)
    pending = [
        int(c["chapter_number"])
        for c in sorted(chapters, key=lambda c: int(c.get("chapter_number") or 0))
        if not c.get("draft_path")
    ]
    return pending[: max(0, count)]


def _autopilot_chapters_to_write_range(db: Path, book_id: int, start: int, end: int) -> list[int]:
    """Return an explicit contiguous draft range, refusing to skip earlier gaps."""
    start = int(start)
    end = int(end)
    if start < 1 or end < 1:
        raise HTTPException(status_code=400, detail="正文起止章必须大于 0")
    if end < start:
        raise HTTPException(status_code=400, detail="正文结束章不能小于起始章")

    chapters = sorted(list_chapters(db, book_id), key=lambda c: int(c.get("chapter_number") or 0))
    if not chapters:
        raise HTTPException(status_code=400, detail="章节队列还没有生成，请先完成章节细纲并入库")

    by_number = {int(c.get("chapter_number") or 0): c for c in chapters}
    pending = [
        int(c["chapter_number"])
        for c in chapters
        if not c.get("draft_path")
    ]
    if not pending:
        raise HTTPException(status_code=400, detail="所有章节已有正文")

    earliest = pending[0]
    if start != earliest:
        raise HTTPException(
            status_code=400,
            detail=f"需要从第{earliest}章开始连续写，不能跳到第{start}章，否则追踪/伏笔会断。",
        )

    for chapter_number in range(start, end + 1):
        chapter = by_number.get(chapter_number)
        if not chapter:
            raise HTTPException(status_code=400, detail=f"章节队列缺少第{chapter_number}章，请先生成章节细纲并入库")
        if chapter.get("draft_path"):
            raise HTTPException(status_code=400, detail=f"第{chapter_number}章已有正文，请从最早未写章节连续生成")

    return list(range(start, end + 1))


def _autopilot_write_one_chapter(
    client: Any,
    db: Path,
    book_id: int,
    book: dict[str, Any],
    work_dir: Path,
    chapter_number: int,
    report: Callable[..., None],
    *,
    max_revisions: int = _AUTOPILOT_DEFAULT_MAX_REVISIONS,
) -> dict[str, Any]:
    """Run the visible per-step chapter flow and persist one chapter.

    Autopilot intentionally uses the same step functions as the manual
    workbench so every intermediate artifact remains visible:

    ``初稿 → 扩写判断 → 润色 → 去AI → 审查 → 按建议修改/复审 → 成稿``.

    A failed review triggers up to ``max_revisions`` concrete review-driven
    rewrites. If the gate still does not pass, the saved chapter is marked for
    human review and the multi-chapter autopilot can continue.
    """
    from generator.long_novel.l2_chapter_write import count_chinese_chars

    ch = get_chapter(db, book_id, chapter_number) or {}
    chapter_title = str(ch.get("title") or "")
    volume_number = int(ch.get("volume_number") or 1)
    target_words = int(ch.get("target_words") or book.get("target_words_per_chapter") or 3000)
    revision_limit = max(0, min(_AUTOPILOT_MAX_REVISIONS, int(max_revisions or 0)))
    revisions = 0
    step_trace: list[dict[str, Any]] = []

    def _report(status: str, detail: str = "", revisions: int = 0, **extra: Any) -> None:
        try:
            report(status, detail, revisions, **extra)
        except TypeError:
            report(status, detail, revisions)

    def _trace_step(
        step: str,
        label: str,
        status: str,
        *,
        word_count: int = 0,
        message: str = "",
    ) -> None:
        step_trace.append({
            "step": step,
            "label": label,
            "status": status,
            "word_count": int(word_count or 0),
            "message": message,
        })

    def _run_step(step: str, label: str, live_status: str) -> dict[str, Any]:
        _report(
            live_status,
            f"第{chapter_number}章：正在{label}",
            revisions,
            step=step,
            step_label=label,
            step_status="running",
            steps=list(step_trace),
        )
        result = _api_write_chapter_step_blocking(
            book_id,
            chapter_number,
            step,
            client=client,
        )
        status = "skipped" if result.get("skipped") else "done"
        message = str(result.get("message") or "")
        _trace_step(
            step,
            label,
            status,
            word_count=int(result.get("word_count") or result.get("final_words") or 0),
            message=message,
        )
        _report(
            live_status,
            message or f"第{chapter_number}章：{label}{'已跳过' if status == 'skipped' else '已完成'}",
            revisions,
            step=step,
            step_label=label,
            step_status=status,
            steps=list(step_trace),
        )
        return result

    # Mark writing without clobbering existing metadata (upsert overwrites all columns).
    upsert_chapter(
        db, book_id, volume_number, chapter_number,
        title=chapter_title, status="writing", target_words=target_words,
        actual_words=int(ch.get("actual_words") or 0),
        outline_path=ch.get("outline_path"), draft_path=ch.get("draft_path"),
        review_status=ch.get("review_status"), ai_review_json=ch.get("ai_review_json"),
    )
    if not _has_later_saved_chapter(db, book_id, chapter_number):
        update_book(db, book_id, current_chapter=chapter_number)

    # An interrupted unwritten chapter may have partial intermediate files.
    # Start the unattended run from a clean per-step chain.
    _cleanup_stale_step_outputs(work_dir, chapter_number, ["draft", "expand", "polish", "deslop", "review"])

    _run_step("draft", "生成初稿", "drafting")
    _run_step("expand", "扩写判断", "expanding")
    _run_step("polish", "润色", "polishing")
    _run_step("deslop", "去 AI", "deslopping")
    review_result = _run_step("review", "六维审查", "reviewing")
    review = dict(review_result.get("review") or {})

    while not review.get("passed") and revisions < revision_limit:
        revisions += 1
        reason = _review_rewrite_reason(review)
        _report(
            "revising",
            f"第{chapter_number}章：审查未通过，正在按建议修改（{revisions}/{revision_limit}）"
            + (f"：{reason}" if reason else ""),
            revisions,
            step="review_fix",
            step_label="按审查建议修改",
            step_status="running",
            steps=list(step_trace),
            reason=reason,
        )
        revised = _api_revise_chapter_step_blocking(
            book_id,
            chapter_number,
            "review",
            {},
            client=client,
        )
        review = dict(revised.get("review") or {})
        _trace_step(
            f"review_fix_{revisions}",
            f"按建议修改 #{revisions}",
            "done" if review.get("passed") else "needs_revision",
            word_count=int(revised.get("word_count") or 0),
            message=str(revised.get("message") or ""),
        )
        _report(
            "reviewing",
            f"第{chapter_number}章：第 {revisions} 次修改后已复审"
            + ("，审查通过" if review.get("passed") else "，仍有待修问题"),
            revisions,
            step="review",
            step_label="复审",
            step_status="done" if review.get("passed") else "needs_revision",
            steps=list(step_trace),
            reason="" if review.get("passed") else _review_rewrite_reason(review),
        )

    final_result = _run_step("finalize", "保存成稿", "finalizing")
    final_text = str(final_result.get("content") or "")
    final_words = int(final_result.get("final_words") or count_chinese_chars(final_text))
    reason = "" if review.get("passed") else _review_rewrite_reason(review)
    status = "passed" if review.get("passed") else "needs_human"
    if status == "needs_human":
        saved_chapter = get_chapter(db, book_id, chapter_number)
        if saved_chapter:
            _upsert_chapter_preserving(db, saved_chapter, status="needs_human")

    return {
        "chapter": chapter_number,
        "status": status,
        "words": final_words,
        "score": int(review.get("score") or 0),
        "review_overall": str(review.get("overall") or ""),
        "revisions": revisions,
        "reason": reason,
        "steps": step_trace,
        "review_summary": str(review.get("summary") or ""),
    }


@router.post("/books/{book_id}/autopilot/start")
async def api_autopilot_start(book_id: int, request: Request) -> dict[str, Any]:
    """Run 设定 → 大纲 → 入库 →〔正文 × N〕as one background job.

    Body: ``{"additional_prompt": "...", "chapter_count": N, "chapter_start": 1,
    "chapter_end": 3, "max_revisions": 2}``.
    When ``chapter_count > 0`` the job continues past 入库 into the 正文 autopilot:
    it writes the next ``chapter_count`` unwritten chapters, each 初稿 → 扩写判断
    → 润色 → 去 AI → 审查 → 按建议修改/复审 → 保存成稿并更新追踪/伏笔/进度
    → 下一章.
    Everything streams to the same ``/autopilot/status`` /
    monitor panel. ``POST /cancel`` stops after the current stage or chapter.
    Stages and chapters already complete are skipped, so re-running resumes an
    interrupted book (and writing more chapters later just adds to it).
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    if _autopilot_job_active(book_id):
        raise HTTPException(status_code=409, detail="该书的全自动生成任务仍在运行")

    payload = await _json_payload(request)
    additional_prompt = str(payload.get("additional_prompt") or "").strip()
    range_start_raw = payload.get("chapter_start")
    range_end_raw = payload.get("chapter_end")
    has_chapter_range = range_start_raw not in (None, "") or range_end_raw not in (None, "")
    chapter_range: tuple[int, int] | None = None
    if has_chapter_range:
        if range_start_raw in (None, "") or range_end_raw in (None, ""):
            raise HTTPException(status_code=400, detail="正文范围需要同时填写起始章和结束章")
        try:
            chapter_start = int(range_start_raw)
            chapter_end = int(range_end_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="正文范围必须是章节数字") from None
        if chapter_start < 1 or chapter_end < chapter_start:
            raise HTTPException(status_code=400, detail="正文范围无效")
        chapter_range = (chapter_start, chapter_end)
        chapter_count = chapter_end - chapter_start + 1
        if list_chapters(_db_path(), book_id):
            _autopilot_chapters_to_write_range(_db_path(), book_id, chapter_start, chapter_end)
    else:
        chapter_count = max(0, int(payload.get("chapter_count") or 0))
    try:
        max_revisions = int(payload.get("max_revisions", _AUTOPILOT_DEFAULT_MAX_REVISIONS))
    except (TypeError, ValueError):
        max_revisions = _AUTOPILOT_DEFAULT_MAX_REVISIONS
    max_revisions = max(0, min(_AUTOPILOT_MAX_REVISIONS, max_revisions))

    work_dir = Path(book["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    _set_cancel(book_id, False)

    def _run() -> None:
        from generator.long_novel.autopilot import (
            AutopilotStage,
            build_l0_stages,
            run_chapter_loop,
            run_stages,
            write_autopilot_file,
        )

        try:
            client = deps._deepseek_client(book)
            stages = build_l0_stages(
                client, work_dir,
                title=book["title"], genre=book["genre"], premise=book.get("premise", ""),
                target_chapters=book["target_chapters"],
                words_per_chapter=book["target_words_per_chapter"],
                additional_prompt=additional_prompt,
            )
            stages.append(AutopilotStage(
                phase="finalize", label="入库",
                run=lambda: _finalize_book_setup(book_id, book, work_dir),
                is_done=lambda: (get_book(_db_path(), book_id) or {}).get("status") == "writing",
            ))

            def _setup_progress(snap: dict[str, Any]) -> None:
                # When chapters will follow, don't let setup's terminal "done"
                # stop the frontend poller — bridge it to a "running" handoff.
                if chapter_count > 0 and snap.get("state") == "done":
                    snap = {**snap, "state": "running", "detail": "设定完成，开始写正文…"}
                write_autopilot_file(work_dir, snap)

            setup_result = run_stages(
                stages,
                write_progress=_setup_progress,
                is_cancelled=lambda: _is_cancelled(book_id),
            )
            if setup_result.get("state") != "done":
                return  # error / cancelled snapshot already written
            if chapter_count <= 0 or _is_cancelled(book_id):
                return  # setup-only run; real terminal "done" already written

            db = _db_path()
            fresh_book = get_book(db, book_id) or book
            setup_completed = [s.phase for s in stages]
            if chapter_range:
                chapter_numbers = _autopilot_chapters_to_write_range(db, book_id, chapter_range[0], chapter_range[1])
            else:
                chapter_numbers = _autopilot_chapters_to_write(db, book_id, chapter_count)

            def _write_one(ch_num: int, report: Callable[..., None]) -> dict[str, Any]:
                return _autopilot_write_one_chapter(
                    client, db, book_id, fresh_book, work_dir, ch_num, report,
                    max_revisions=max_revisions,
                )

            run_chapter_loop(
                chapter_numbers,
                write_chapter=_write_one,
                write_progress=lambda snap: write_autopilot_file(work_dir, snap),
                is_cancelled=lambda: _is_cancelled(book_id),
                setup_completed=setup_completed,
            )
        except Exception as exc:
            from generator.long_novel.autopilot import write_autopilot_file
            write_autopilot_file(work_dir, {
                "state": "error", "stage": "", "detail": str(exc)[:300],
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            })
            logger.exception("autopilot failed for book %s", book_id)
        finally:
            _autopilot_job_mark(book_id, False)

    from generator.long_novel.autopilot import write_autopilot_file
    write_autopilot_file(work_dir, {
        "state": "running", "stage": "", "detail": "启动中...", "total": 0,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    })
    _autopilot_job_mark(book_id, True)
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _autopilot_job_mark(book_id, False)
        raise
    msg = "autopilot 已启动"
    if chapter_count > 0:
        if chapter_range:
            msg = f"autopilot 已启动（设定 + 正文 第{chapter_range[0]}-{chapter_range[1]}章）"
        else:
            msg = f"autopilot 已启动（设定 + 正文 {chapter_count} 章）"
    return {"ok": True, "message": msg, "chapter_count": chapter_count, "chapter_range": chapter_range}


@router.get("/books/{book_id}/autopilot/status")
def api_autopilot_status(book_id: int) -> dict[str, Any]:
    """Poll autopilot progress. Flags a dead worker (>5 min without update)."""
    db = _db_path()
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    from generator.long_novel.autopilot import AUTOPILOT_FILE, read_autopilot_file, write_autopilot_file

    work_dir = Path(book["work_dir"])
    data = read_autopilot_file(work_dir)
    if not data:
        return {"ok": True, "state": "idle"}
    if data.get("state") == "running" and not _autopilot_job_active(book_id):
        progress_file = setup_file_read(work_dir, AUTOPILOT_FILE)
        if progress_file.exists() and (time.time() - progress_file.stat().st_mtime) > 300:
            data["state"] = "cancelled"
            data["detail"] = "进程中断（服务重启或超时），可重新开始"
            data["updated_at"] = datetime.now().strftime("%H:%M:%S")
            write_autopilot_file(work_dir, data)
    data = _repair_invalid_autopilot_writing_snapshot(work_dir, data)
    data = _sync_paused_autopilot_snapshot(book_id, work_dir, data)
    return {"ok": True, **data}


def _repair_invalid_autopilot_writing_snapshot(work_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Fix stale snapshots that say done while chapter writing is incomplete."""
    writing = data.get("writing")
    if data.get("state") not in {"done", "error"} or not isinstance(writing, dict):
        return data
    total = int(writing.get("total") or 0)
    done = int(writing.get("done") or 0)
    if total <= 0 or done >= total:
        return data

    from generator.long_novel.autopilot import write_autopilot_file

    failed_at = data.get("failed_at") or writing.get("current") or 1
    detail = str(data.get("detail") or "")
    if "完成" in detail or "同步" in detail or "继续全自动" in detail or not detail:
        detail = f"第{failed_at}章生成失败，正文完成 {done}/{total} 章"
    repaired = {
        **data,
        "state": "error",
        "phase": "writing",
        "stage": "writing",
        "detail": detail,
        "failed_at": failed_at,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }
    write_autopilot_file(work_dir, repaired)
    return repaired


def _sync_paused_autopilot_snapshot(book_id: int, work_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Merge manually completed setup phases into a paused autopilot snapshot."""
    if _autopilot_job_active(book_id) or data.get("state") not in {"cancelled", "error"}:
        return data

    from generator.long_novel.autopilot import write_autopilot_file

    stages = [
        ("premise", "题材定位"),
        ("world", "世界观"),
        ("characters", "角色设计"),
        ("factions", "势力"),
        ("relations", "关系"),
        ("outline", "全书大纲"),
        ("volume_outline", "卷纲"),
        ("chapter_outlines", "章节细纲"),
        ("finalize", "入库"),
    ]
    completed = {str(phase) for phase in (data.get("completed") or [])}
    for phase, _label in stages:
        progress_file = setup_file_read(work_dir, f"_setup_{phase}.json")
        if not progress_file.exists():
            continue
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if progress.get("status") == "done":
            completed.add(phase)

    ordered_completed = [phase for phase, _label in stages if phase in completed]
    next_index = next((index for index, (phase, _label) in enumerate(stages) if phase not in completed), len(stages))
    next_phase, next_label = stages[next_index] if next_index < len(stages) else ("", "")
    setup_done = next_index == len(stages)
    writing = data.get("writing")
    has_writing = isinstance(writing, dict) and int(writing.get("total") or 0) > 0
    writing_done = bool(
        has_writing
        and int(writing.get("done") or 0) >= int(writing.get("total") or 0)
    )
    all_done = setup_done and (not has_writing or writing_done)
    display_stage = "writing" if setup_done and has_writing and not writing_done else next_phase
    display_label = "正文" if display_stage == "writing" else next_label
    if (
        ordered_completed == list(data.get("completed") or [])
        and display_stage == str(data.get("stage") or "")
        and (not setup_done or data.get("state") == "done" or has_writing)
    ):
        return data

    detail = "全自动生成完成" if all_done else "已同步手动生成结果，可继续全自动"
    if has_writing and not writing_done:
        detail = str(data.get("detail") or detail)
    synced = {
        **data,
        "state": "done" if all_done else data.get("state"),
        "stage": display_stage,
        "label": display_label,
        "index": next_index,
        "total": len(stages),
        "stage_status": "done" if setup_done else "",
        "detail": detail,
        "completed": ordered_completed,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }
    write_autopilot_file(work_dir, synced)
    return synced


@router.get("/books/{book_id}/progress")
def api_book_progress(book_id: int) -> dict[str, Any]:
    """Get current progress of all operations for this book.

    Returns status of each setup phase (pending/running/done/error/cancelled)
    and chapter writing progress.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    import json as _json_lib
    work_dir = Path(book["work_dir"])

    phases = ["premise", "world", "characters", "outline", "volume_outline", "chapter_outlines", "extend_chapters"]
    phase_statuses = {}
    active_phase = None

    for ph in phases:
        fname = "_extend_chapters.json" if ph == "extend_chapters" else f"_setup_{ph}.json"
        pf = setup_file_read(work_dir, fname)
        if pf.exists():
            data = _json_lib.loads(pf.read_text(encoding="utf-8"))
            st = data.get("status", "?")
            phase_statuses[ph] = {
                "status": st,
                "detail": data.get("detail", ""),
                "updated_at": data.get("updated_at", ""),
            }
            if st == "running":
                active_phase = ph
        else:
            phase_statuses[ph] = {"status": "pending", "detail": "尚未开始", "updated_at": ""}

    return {
        "ok": True,
        "book_status": book.get("status"),
        "phase_statuses": phase_statuses,
        "active_phase": active_phase,
        "cancelled": _is_cancelled(book_id),
    }


@router.post("/books/{book_id}/cancel")
def api_cancel_book_operation(book_id: int) -> dict[str, Any]:
    """Cancel any running operation for this book."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    _set_cancel(book_id, True)
    logger.info("Cancel requested for book %s", book_id)
    return {"ok": True, "message": "已发送取消信号，当前操作将在下一个检查点停止"}


@router.post("/books/{book_id}/resume")
def api_resume_book_operation(book_id: int) -> dict[str, Any]:
    """Clear the cancel flag so new operations can start."""
    _set_cancel(book_id, False)
    return {"ok": True, "message": "已清除取消状态"}
