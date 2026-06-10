"""Long novel REST API — book library, writing workbench, review."""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from generator.long_novel import book_routes, deps, prompt_registry
from generator.long_novel.chapter_resets import (
    _archive_and_reset_chapter_outputs,
    _chapter_range,
    _cleanup_stale_step_outputs,
    _has_later_saved_chapter,
    _invalidate_outputs_after_step,
    _path_within,
    _reset_chapter_range_for_regeneration,
    _reset_chapter_row_for_deleted_outputs,
    _sync_tracking_after_chapter_reset,
    _write_reset_idle_autopilot_snapshot,
)
from generator.long_novel.chapter_steps import (
    _api_force_pass_chapter_step_blocking,
    _api_revise_chapter_step_blocking,
    _api_write_chapter_blocking,
    _api_write_chapter_step_blocking,
    _revise_progress_step,
)
from generator.long_novel.db import (
    get_book,
    get_chapter,
    list_chapters,
    list_volumes,
    update_book,
    upsert_chapter,
    upsert_volume,
)
from generator.long_novel.deps import (
    _chat_text,
    _db_path,
    _json_payload,
    _upsert_chapter_preserving,
)
from generator.long_novel.jobs import (
    _autopilot_job_active,
    _autopilot_job_mark,
    _is_cancelled,
    _set_cancel,
    _step_job_active,
    _step_job_mark,
)
from generator.long_novel.l0_book_setup import (
    setup_dir,
    setup_file_read,
    setup_glob,
)
from generator.long_novel.l2_chapter_write import (
    CHAPTER_STEP_FILES,
    chapter_final_path,
)
from generator.long_novel.review_gate import (
    _EXPAND_AUTO_SKIP_WORDS,
    _normalize_review_gate,
    _review_rewrite_reason,
    _score_deai_result,
)
from generator.long_novel.step_artifacts import (
    _archive_step_version,
    _chapter_batch_count,
    _finalize_run_count,
    _max_outline_chapter,
    _outline_for_chapter,
    _outline_title,
    _read_json_file,
    _read_step_source,
    _step_file_path,
    _step_file_read,
    _step_force_read,
    _step_gate_read,
    _step_history_count,
    _step_history_dir,
    _step_progress_path,
    _step_run_count,
    _step_skip_path,
    _step_skip_read,
    _step_status_snapshot,
    _write_step_progress,
)

logger = logging.getLogger(__name__)

# 兼容旧引用：测试/外部以 deps._deepseek_client 为唯一 patch 缝。
_deepseek_client = deps._deepseek_client

router = APIRouter(prefix="/api/long-novel", tags=["long-novel"])
router.include_router(book_routes.router)
router.include_router(prompt_registry.router)

# 旧私有名仍被本模块的 revise 流程使用；实现在 prompt_registry/prompt_kit。
_prompt_file_text = prompt_registry._prompt_file_text
_render_prompt_template = prompt_registry._render_prompt_template
_load_prompt_template = prompt_registry._load_prompt_template

_AUTOPILOT_DEFAULT_MAX_REVISIONS = 2
_AUTOPILOT_MAX_REVISIONS = 3


# ── Pipeline: Book Setup (L0) - async with polling ──────────────────


def _finalize_book_setup(book_id: int, book: dict[str, Any], work_dir: Path) -> None:
    """Create chapter rows from the generated 细纲 and flip the book to 'writing'.

    Idempotent: chapters that already have a draft are left untouched, so this
    is safe to re-run from either the manual finalize phase or the autopilot.
    """
    db = _db_path()
    for ch_num in range(1, book["target_chapters"] + 1):
        outline_path = work_dir / "大纲" / f"细纲_第{ch_num:03d}章.md"
        existing_chapter = get_chapter(db, book_id, ch_num)
        if existing_chapter and existing_chapter.get("draft_path"):
            continue
        upsert_chapter(
            db, book_id, volume_number=1, chapter_number=ch_num,
            title=(existing_chapter or {}).get("title") or f"第{ch_num}章",
            status=(existing_chapter or {}).get("status") or "outline_only",
            target_words=book["target_words_per_chapter"],
            outline_path=str(outline_path) if outline_path.exists() else None,
        )
    upsert_volume(db, book_id, 1, title="第一卷", chapter_count=book["target_chapters"], status="outlined")
    update_book(db, book_id, status="writing", total_volumes=1, current_volume=1)


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


@router.post("/books/{book_id}/setup-phase/{phase}")
async def api_start_setup_phase(book_id: int, phase: str, request: Request) -> dict[str, Any]:
    """Start a single L0 phase in background. Poll /setup-phase/{phase}/status for progress."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    work_dir = Path(book["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    progress_file = setup_dir(work_dir) / f"_setup_{phase}.json"

    import json as _json_lib

    def _write(s, d=""):
        progress_file.write_text(_json_lib.dumps({
            "status": s, "detail": d,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }, ensure_ascii=False), encoding="utf-8")

    valid_phases = ["premise", "world", "characters", "factions", "relations", "outline", "volume_outline", "chapter_outlines", "finalize"]
    if phase not in valid_phases:
        raise HTTPException(status_code=400, detail=f"未知阶段：{phase}")

    payload = await _json_payload(request)
    additional_prompt = str(payload.get("additional_prompt") or "").strip()

    # Clear any lingering cancel flag when explicitly starting a phase
    _set_cancel(book_id, False)

    def _run():
        from generator.long_novel.l0_book_setup import (
            run_l0_book_outline,
            run_l0_chapter_outlines,
            run_l0_characters,
            run_l0_factions,
            run_l0_premise,
            run_l0_relations,
            run_l0_volume_outline,
            run_l0_world,
        )
        client = deps._deepseek_client(book)

        def _cancelled() -> bool:
            if _is_cancelled(book_id):
                _write("cancelled", "已取消")
                return True
            return False

        try:
            if phase == "premise":
                _write("running", "AI正在分析题材趋势，生成题材定位文档...")
                if _cancelled():
                    return
                run_l0_premise(client, work_dir, book["title"], book["genre"], book["premise"], additional_prompt)
                fp = work_dir / "设定" / "题材定位.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "world":
                _write("running", "AI正在构建世界观背景和力量体系...")
                if _cancelled():
                    return
                run_l0_world(client, work_dir, book["title"], book["genre"], additional_prompt)
                fp = work_dir / "设定" / "世界观" / "背景设定.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "characters":
                _write("running", "AI正在设计主要角色和关系网络...")
                if _cancelled():
                    return
                result = run_l0_characters(client, work_dir, book["title"], book["genre"], additional_prompt)
                outputs = result.get("outputs", []) if isinstance(result, dict) else []
                # 选择第一个非索引文件作为预览
                preview = ""
                for rel in outputs:
                    if "_角色索引" in rel:
                        continue
                    fp = work_dir / rel
                    if fp.exists():
                        preview = fp.read_text(encoding="utf-8")[:2000]
                        break
                if not preview:
                    fp = work_dir / "设定" / "角色" / "角色设定.md"
                    preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else f"已生成 {len(outputs)} 个角色文件"
                _write("done", preview)
            elif phase == "factions":
                _write("running", "AI正在两阶段生成势力档案（先清单后并发详写）...")
                if _cancelled():
                    return
                result = run_l0_factions(client, work_dir, book["title"], book["genre"], additional_prompt)
                outputs = result.get("outputs", []) if isinstance(result, dict) else []
                preview = ""
                for rel in outputs:
                    if "_势力索引" in rel:
                        continue
                    fp = work_dir / rel
                    if fp.exists():
                        preview = fp.read_text(encoding="utf-8")[:2000]
                        break
                if not preview:
                    preview = f"已生成 {len(outputs)} 个势力文件"
                _write("done", preview)
            elif phase == "relations":
                _write("running", "AI正在梳理角色与势力之间的关系网络...")
                if _cancelled():
                    return
                run_l0_relations(client, work_dir, book["title"], book["genre"], additional_prompt)
                fp = work_dir / "设定" / "关系.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "outline":
                _write("running", "AI正在生成全书级大纲...")
                if _cancelled():
                    return
                run_l0_book_outline(client, work_dir, book["title"], book["genre"],
                                    book["target_chapters"], book["target_words_per_chapter"], additional_prompt)
                fp = work_dir / "大纲" / "大纲.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "volume_outline":
                _write("running", "AI正在把全书大纲拆成卷纲...")
                if _cancelled():
                    return
                result = run_l0_volume_outline(client, work_dir, book["title"], book["genre"],
                                               book["target_chapters"], book["target_words_per_chapter"], additional_prompt)
                outputs = result.get("outputs", []) if isinstance(result, dict) else []
                preview = ""
                for rel in outputs:
                    fp = work_dir / rel
                    if fp.exists():
                        preview = fp.read_text(encoding="utf-8")[:2000]
                        break
                if not preview:
                    preview = f"已生成 {len(outputs)} 个卷纲文件"
                _write("done", preview)
            elif phase == "chapter_outlines":
                _write("running", "AI正在根据大纲和卷纲生成章节细纲...")
                if _cancelled():
                    return
                result = run_l0_chapter_outlines(client, work_dir, book["title"], book["genre"],
                                                book["target_chapters"], book["target_words_per_chapter"], additional_prompt)
                count = result.get("chapters_generated", 0)
                fp = work_dir / "大纲" / "细纲_第001章.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else f"已生成 {count} 章细纲"
                _write("done", preview)
            elif phase == "finalize":
                _write("running", "正在写入数据库...")
                if _cancelled():
                    return
                _finalize_book_setup(book_id, book, work_dir)
                _write("done", f"开书设定完成，共{book['target_chapters']}章")
        except Exception as e:
            _write("error", str(e)[:300])
            logger.exception("Setup phase %s failed for book %s", phase, book_id)

    import threading
    _write("starting", "启动中...")
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "phase": phase, "message": f"{phase} 已启动"}


@router.get("/books/{book_id}/setup-phase/{phase}/status")
def api_setup_phase_status(book_id: int, phase: str) -> dict[str, Any]:
    """Poll status of a running setup phase."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    progress_file = setup_file_read(work_dir, f"_setup_{phase}.json")
    if not progress_file.exists():
        inferred = _inferred_setup_phase_status(work_dir, phase)
        if inferred is not None:
            return {"ok": True, **inferred}
        return {"ok": True, "status": "pending", "detail": "尚未开始"}
    import json as _json_lib
    import time as _time
    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    st = data.get("status", "?")
    # If status is running/starting but file hasn't been updated in 5+ minutes,
    # the generation thread is dead (likely app restart). Reset to cancelled.
    if st in ("running", "starting"):
        file_age = _time.time() - progress_file.stat().st_mtime
        if file_age > 300:  # 5 minutes
            data["status"] = "cancelled"
            data["detail"] = "进程中断（服务重启或超时），可重新生成"
            progress_file.write_text(_json_lib.dumps({
                **data, "updated_at": datetime.now().strftime("%H:%M:%S"),
            }, ensure_ascii=False), encoding="utf-8")
            st = "cancelled"
    return {"ok": True, "status": st, "detail": data.get("detail", ""),
            "updated_at": data.get("updated_at", "")}


def _inferred_setup_phase_status(work_dir: Path, phase: str) -> dict[str, Any] | None:
    """Infer setup status for autopilot and legacy books without phase files."""
    from generator.long_novel.autopilot import l0_phase_done, read_autopilot_file

    autopilot = read_autopilot_file(work_dir) or {}
    completed = {str(item) for item in (autopilot.get("completed") or [])}
    if phase in completed:
        return {
            "status": "done",
            "detail": "全自动生成已完成",
            "updated_at": str(autopilot.get("updated_at") or ""),
        }
    if autopilot.get("state") == "running" and autopilot.get("stage") == phase:
        return {
            "status": "running",
            "detail": str(autopilot.get("detail") or "全自动生成中"),
            "updated_at": str(autopilot.get("updated_at") or ""),
        }
    if phase != "finalize" and l0_phase_done(work_dir, phase):
        return {"status": "done", "detail": "检测到已有产物", "updated_at": ""}
    return None


# ── Autopilot: run the whole open-book pipeline in one background job ──


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


@router.post("/books/{book_id}/extend-chapters")
async def api_extend_chapters(book_id: int, request: Request) -> dict[str, Any]:
    """Extend a book beyond its current planned chapter count.

    This only generates new chapter outlines and inserts new chapter rows; it
    does not rewrite existing outlines or finished drafts.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    payload = await _json_payload(request)
    work_dir = Path(book["work_dir"])
    chapters = list_chapters(_db_path(), book_id)
    max_db_chapter = max((int(c.get("chapter_number") or 0) for c in chapters), default=0)
    old_target = max(int(book.get("target_chapters") or 0), max_db_chapter, _max_outline_chapter(work_dir))

    if payload.get("new_target_chapters") is not None:
        new_target = int(payload.get("new_target_chapters") or 0)
    else:
        additional = int(payload.get("additional_chapters") or 0)
        new_target = old_target + additional

    if new_target <= old_target:
        raise HTTPException(status_code=400, detail=f"新总章数必须大于当前 {old_target} 章")
    if new_target > 2000:
        raise HTTPException(status_code=400, detail="总章数不能超过 2000")

    additional_prompt = str(payload.get("additional_prompt") or "").strip()
    work_dir.mkdir(parents=True, exist_ok=True)
    progress_file = setup_dir(work_dir) / "_extend_chapters.json"
    # Migrate legacy location if it exists
    legacy_extend = work_dir / "_extend_chapters.json"
    if legacy_extend.exists() and not progress_file.exists():
        try:
            progress_file.write_text(legacy_extend.read_text(encoding="utf-8"), encoding="utf-8")
            legacy_extend.unlink()
        except Exception:
            pass

    import json as _json_lib
    import time as _time

    if progress_file.exists():
        try:
            existing_progress = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
            if existing_progress.get("status") in ("starting", "running"):
                file_age = _time.time() - progress_file.stat().st_mtime
                if file_age <= 600:
                    raise HTTPException(status_code=409, detail="已有追加章节任务正在运行")
        except HTTPException:
            raise
        except Exception:
            pass

    def _write(s: str, d: str = "", extra: dict[str, Any] | None = None) -> None:
        progress_file.write_text(_json_lib.dumps({
            "status": s,
            "detail": d,
            "old_target_chapters": old_target,
            "new_target_chapters": new_target,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            **(extra or {}),
        }, ensure_ascii=False), encoding="utf-8")

    def _run() -> None:
        try:
            _write("running", f"正在生成第{old_target + 1}-{new_target}章续写规划与细纲...")
            from generator.long_novel.l0_book_setup import run_l0_extend_chapter_outlines

            client = deps._deepseek_client(book)
            result = run_l0_extend_chapter_outlines(
                client,
                work_dir,
                book["title"],
                book["genre"],
                old_target,
                new_target,
                int(book.get("target_words_per_chapter") or 3000),
                additional_prompt,
            )

            for ch_num in range(old_target + 1, new_target + 1):
                outline_path = work_dir / "大纲" / f"细纲_第{ch_num:03d}章.md"
                volume_number = max(1, ((ch_num - 1) // 30) + 1)
                upsert_chapter(
                    _db_path(),
                    book_id,
                    volume_number=volume_number,
                    chapter_number=ch_num,
                    title=_outline_title(outline_path, ch_num),
                    status="outline_only",
                    target_words=int(book.get("target_words_per_chapter") or 3000),
                    outline_path=str(outline_path) if outline_path.exists() else None,
                )

            total_volumes = max(int(book.get("total_volumes") or 1), ((new_target - 1) // 30) + 1)
            existing_volumes = {int(v.get("volume_number") or 0) for v in list_volumes(_db_path(), book_id)}
            for vol_num in range(1, total_volumes + 1):
                if vol_num not in existing_volumes:
                    first_ch = (vol_num - 1) * 30 + 1
                    chapter_count = max(0, min(30, new_target - first_ch + 1))
                    upsert_volume(
                        _db_path(),
                        book_id,
                        vol_num,
                        title=f"第{vol_num}卷",
                        chapter_count=chapter_count,
                        status="outlined",
                    )

            update_book(
                _db_path(),
                book_id,
                target_chapters=new_target,
                total_volumes=total_volumes,
                status="writing",
            )
            _write(
                "done",
                f"已追加第{old_target + 1}-{new_target}章，共{new_target - old_target}章",
                {"result": result},
            )
        except Exception as e:
            _write("error", str(e)[:500])
            logger.exception("Extend chapters failed for book %s", book_id)

    _write("starting", "启动中...")
    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True,
        "old_target_chapters": old_target,
        "new_target_chapters": new_target,
        "message": f"已启动追加章节：第{old_target + 1}-{new_target}章",
    }


@router.get("/books/{book_id}/extend-chapters/status")
def api_extend_chapters_status(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    progress_file = setup_file_read(Path(book["work_dir"]), "_extend_chapters.json")
    if not progress_file.exists():
        return {"ok": True, "status": "pending", "detail": "尚未开始"}
    import json as _json_lib
    import time as _time

    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    st = data.get("status", "pending")
    if st in ("running", "starting"):
        file_age = _time.time() - progress_file.stat().st_mtime
        if file_age > 600:
            data["status"] = "cancelled"
            data["detail"] = "进程中断（服务重启或超时），可重新追加"
            data["updated_at"] = datetime.now().strftime("%H:%M:%S")
            progress_file.write_text(_json_lib.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, **data}


@router.get("/books/{book_id}/setup-progress")
def api_setup_progress(book_id: int) -> dict[str, Any]:
    """Poll the current L0 setup progress."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    progress_file = setup_file_read(Path(book["work_dir"]), "_setup_progress.json")
    if not progress_file.exists():
        return {"ok": True, "progress": {"phase": "pending", "status": "not_started", "detail": "尚未开始"}}
    import json as _json_lib
    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    return {"ok": True, "progress": data}


# ── Real-time progress + cancel ────────────────────────────────────────


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


@router.get("/books/{book_id}/setup-phase/{phase}/trace")
def api_setup_phase_trace(book_id: int, phase: str) -> dict[str, Any]:
    """Return the recorded LLM trace JSON for a setup phase (system, real user, output, usage)."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    main_path = setup_file_read(work_dir, f"_setup_{phase}_trace.json")
    import json as _json_lib

    sub_traces: list[dict[str, Any]] = []
    if work_dir.exists():
        prefix = f"_setup_{phase}_"
        for p in setup_glob(work_dir, f"{prefix}*_trace.json"):
            if p.name == main_path.name:
                continue
            try:
                sub_traces.append({
                    "file": p.name,
                    "suffix": p.stem.replace(f"_setup_{phase}", "").replace("_trace", ""),
                    "data": _json_lib.loads(p.read_text(encoding="utf-8")),
                })
            except Exception:
                pass

    if not main_path.exists() and not sub_traces:
        return {"ok": True, "has_trace": False, "phase": phase}

    main_data: dict[str, Any] | None = None
    if main_path.exists():
        try:
            main_data = _json_lib.loads(main_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": True, "has_trace": False, "phase": phase, "error": f"trace 文件解析失败：{e}"}

    return {
        "ok": True,
        "has_trace": True,
        "phase": phase,
        "trace": main_data,
        "sub_traces": sub_traces,
    }


@router.get("/books/{book_id}/setup-pipeline")
def api_setup_pipeline(book_id: int) -> dict[str, Any]:
    """Return an overview of all 6 L0 phases: status + has_trace + inputs/outputs preview."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    import json as _json_lib

    phase_meta = [
        {"id": "premise", "label": "题材定位", "icon": "", "output": "设定/题材定位.md"},
        {"id": "world", "label": "世界观", "icon": "", "output": "设定/世界观/"},
        {"id": "characters", "label": "角色设计", "icon": "", "output": "设定/角色/"},
        {"id": "factions", "label": "势力", "icon": "", "output": "设定/势力/"},
        {"id": "relations", "label": "关系", "icon": "", "output": "设定/关系.md"},
        {"id": "outline", "label": "全书大纲", "icon": "", "output": "大纲/大纲.md"},
        {"id": "volume_outline", "label": "卷纲", "icon": "", "output": "大纲/卷纲_第N卷.md × N"},
        {"id": "chapter_outlines", "label": "章节细纲", "icon": "", "output": "大纲/细纲_第NNN章.md × N"},
    ]

    phases: list[dict[str, Any]] = []
    for meta in phase_meta:
        ph_id = meta["id"]
        status = "pending"
        detail = ""
        updated_at = ""
        pf = setup_file_read(work_dir, f"_setup_{ph_id}.json")
        if pf.exists():
            try:
                pdata = _json_lib.loads(pf.read_text(encoding="utf-8"))
                status = pdata.get("status", "pending")
                detail = (pdata.get("detail") or "")[:160]
                updated_at = pdata.get("updated_at", "")
            except Exception:
                pass
        trace_path = setup_file_read(work_dir, f"_setup_{ph_id}_trace.json")
        sub_trace_count = 0
        if work_dir.exists():
            sub_trace_count = sum(
                1 for _ in setup_glob(work_dir, f"_setup_{ph_id}_*_trace.json")
                if _.name != trace_path.name
            )
        from generator.long_novel.autopilot import l0_phase_done
        out_exists = l0_phase_done(work_dir, ph_id)
        if status == "pending":
            inferred = _inferred_setup_phase_status(work_dir, ph_id)
            if inferred is not None:
                status = str(inferred["status"])
                detail = str(inferred.get("detail") or "")
                updated_at = str(inferred.get("updated_at") or "")
        phases.append({
            **meta,
            "status": status,
            "detail": detail,
            "updated_at": updated_at,
            "has_trace": trace_path.exists(),
            "sub_trace_count": sub_trace_count,
            "output_exists": out_exists,
        })

    return {
        "ok": True,
        "book_id": book_id,
        "title": book.get("title", ""),
        "phases": phases,
    }


@router.get("/books/{book_id}/setup-files")
def api_setup_phase_files(book_id: int, phase: str) -> dict[str, Any]:
    """List all artifact files produced by a setup phase (for chip file-list UI).

    Returns ``{ok, phase, files: [{path, name, bytes, mtime, is_index}]}``.
    Single-file phases return one entry; multi-file phases (world/characters/factions)
    return one entry per .md file under the phase's output dir.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])

    # phase → (list_of_known_single_files, list_of_dirs_to_glob)
    layout = {
        "premise": (["设定/题材定位.md"], []),
        "world": (
            # legacy single files + new per-topic files
            ["设定/世界观/背景设定.md", "设定/世界观/力量体系.md", "设定/世界观/时代地理.md", "设定/世界观/历史大事件.md"],
            ["设定/世界观"],
        ),
        "characters": (["设定/角色/角色设定.md"], ["设定/角色"]),
        "factions": (["设定/势力/主要势力.md"], ["设定/势力"]),
        "relations": (["设定/关系.md"], []),
        "outline": (["大纲/大纲.md"], []),
        "volume_outline": ([], ["大纲"]),
        "chapter_outlines": ([], ["大纲"]),
    }
    if phase not in layout:
        raise HTTPException(status_code=400, detail=f"未知阶段：{phase}")

    explicit_files, dirs_to_glob = layout[phase]
    seen: set[str] = set()
    files: list[dict[str, Any]] = []

    def _add(rel_path: str) -> None:
        if rel_path in seen:
            return
        p = work_dir / rel_path
        if not p.exists() or not p.is_file():
            return
        seen.add(rel_path)
        try:
            st = p.stat()
            files.append({
                "path": rel_path,
                "name": p.name,
                "bytes": st.st_size,
                "mtime": st.st_mtime,
                "is_index": p.name.startswith("_"),
            })
        except Exception:
            pass

    for rel in explicit_files:
        _add(rel)
    for d in dirs_to_glob:
        dp = work_dir / d
        if dp.exists() and dp.is_dir():
            if phase == "volume_outline":
                try:
                    from generator.long_novel.l0_book_setup import ensure_volume_outlines_split
                    ensure_volume_outlines_split(work_dir)
                except Exception:
                    pass
                patterns = ["卷纲_*.md"]
            elif phase == "chapter_outlines":
                patterns = ["细纲_*.md", "续写规划_*.md"]
            else:
                patterns = ["*.md"]
            for pattern in patterns:
                for p in sorted(dp.glob(pattern)):
                    _add(str(p.relative_to(work_dir)).replace("\\", "/"))

    # Sort: index files first, then by name
    files.sort(key=lambda f: (not f["is_index"], f["name"]))

    return {"ok": True, "phase": phase, "files": files}


# ── Pipeline: Write Chapter (L2) ──────────────────────────────────────


@router.post("/books/{book_id}/write-chapter/{chapter_number}")
async def api_write_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    return await run_in_threadpool(_api_write_chapter_blocking, book_id, chapter_number)


# ── Pipeline: Step-by-step Chapter Writing ─────────────────────────────


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/start")
async def api_start_write_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    force = bool(payload.get("force"))
    valid_steps = {"draft", "expand", "polish", "review", "deslop", "continuity", "finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    progress_path = _step_progress_path(work_dir, chapter_number, chapter_title, step_name)
    current = _step_status_snapshot(book_id, work_dir, ch, chapter_number, step_name)
    if current.get("status") in {"starting", "running"}:
        return {
            "ok": True,
            "accepted": True,
            "already_running": True,
            "step": step_name,
            "status": current.get("status"),
            "detail": current.get("detail", ""),
            "updated_at": current.get("updated_at", ""),
            "run_count": current.get("run_count") or 0,
        }

    _write_step_progress(progress_path, "starting", "后台任务已启动", {"step": step_name})
    _step_job_mark(book_id, chapter_number, step_name, True)

    def _run() -> None:
        try:
            _write_step_progress(progress_path, "running", f"{step_name} 执行中…", {"step": step_name})
            result = _api_write_chapter_step_blocking(book_id, chapter_number, step_name, force)
            result_summary = {
                "word_count": int(result.get("word_count") or 0),
                "final_words": int(result.get("final_words") or 0),
                "skipped": bool(result.get("skipped")),
                "next_step": result.get("next_step") or "",
                "run_count": int(result.get("run_count") or 0),
                "batch_count": int(result.get("batch_count") or 0),
            }
            status = "skipped" if result.get("skipped") else "done"
            detail = str(result.get("message") or ("步骤已完成" if status == "done" else "步骤已跳过"))
            _write_step_progress(progress_path, status, detail, {"step": step_name, "result": result_summary})
        except HTTPException as exc:
            _write_step_progress(
                progress_path,
                "error",
                str(exc.detail)[:500],
                {"step": step_name, "http_status": exc.status_code},
            )
        except Exception as exc:
            logger.exception("chapter step failed book=%s chapter=%s step=%s", book_id, chapter_number, step_name)
            _write_step_progress(progress_path, "error", str(exc)[:500], {"step": step_name})
        finally:
            _step_job_mark(book_id, chapter_number, step_name, False)

    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True,
        "accepted": True,
        "step": step_name,
        "status": "starting",
        "detail": "后台任务已启动",
    }


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/status")
def api_write_chapter_step_progress(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    work_dir = Path(book["work_dir"])
    data = _step_status_snapshot(book_id, work_dir, ch, chapter_number, step_name)
    return {"ok": True, **data}


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}")
async def api_write_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    force = bool(payload.get("force"))
    return await run_in_threadpool(_api_write_chapter_step_blocking, book_id, chapter_number, step_name, force)


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step-status")
def api_write_chapter_step_status(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Get current step status and available intermediate outputs."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import count_chinese_chars

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    batch_count = _chapter_batch_count(work_dir, chapter_number, chapter_title)
    steps_available = []
    for step_name in CHAPTER_STEP_FILES.keys():
        fp = _step_file_read(work_dir, chapter_number, step_name)
        if fp and fp.exists():
            text = fp.read_text(encoding="utf-8")
            steps_available.append({
                "step": step_name,
                "word_count": count_chinese_chars(text),
                "has_content": True,
                "run_count": _step_run_count(work_dir, chapter_number, str(ch.get("title") or ""), step_name),
                "batch_count": batch_count,
            })
        elif _step_skip_read(work_dir, chapter_number, step_name):
            steps_available.append({
                "step": step_name,
                "word_count": 0,
                "has_content": False,
                "skipped": True,
                "run_count": _step_history_count(work_dir, chapter_number, str(ch.get("title") or ""), step_name),
                "batch_count": batch_count,
            })
    if ch.get("draft_path"):
        final_path = Path(str(ch.get("draft_path")))
        steps_available.append({
            "step": "finalize",
            "word_count": int(ch.get("actual_words") or 0),
            "has_content": True,
            "run_count": _finalize_run_count(final_path),
            "batch_count": batch_count,
        })
    steps_progress = [
        _step_status_snapshot(book_id, work_dir, ch, chapter_number, step_name)
        for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]
    ]

    return {
        "ok": True,
        "chapter_status": ch.get("status"),
        "review_status": ch.get("review_status"),
        "batch_count": batch_count,
        "steps_available": steps_available,
        "steps_progress": steps_progress,
    }


# ── Pipeline: Review Only (L4) ────────────────────────────────────────


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}")
def api_write_chapter_step_output(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    """Read saved output for one chapter-writing step."""
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import count_chinese_chars, strip_chapter_heading

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    batch_count = _chapter_batch_count(work_dir, chapter_number, chapter_title)
    if step_name == "finalize":
        draft_path = Path(ch["draft_path"]) if ch.get("draft_path") else None
        content = draft_path.read_text(encoding="utf-8") if draft_path and draft_path.exists() else ""
        review = {}
        if ch.get("ai_review_json"):
            try:
                review = json.loads(ch["ai_review_json"])
            except Exception:
                review = {}
        if review:
            review = _normalize_review_gate(review, chapter_number)
        return {
            "ok": True,
            "step": "finalize",
            "content": content,
            "word_count": count_chinese_chars(content),
            "final_words": int(ch.get("actual_words") or count_chinese_chars(content)),
            "draft_path": str(draft_path) if draft_path else "",
            "review": review,
            "run_count": _finalize_run_count(draft_path),
            "batch_count": batch_count,
        }

    step_path = _step_file_read(work_dir, chapter_number, step_name)
    if not step_path or not step_path.exists():
        skip_marker = _step_skip_read(work_dir, chapter_number, step_name)
        if step_name == "expand" and skip_marker:
            marker_data = _read_json_file(skip_marker)
            draft_path = _step_file_read(work_dir, chapter_number, "draft")
            draft = draft_path.read_text(encoding="utf-8") if draft_path and draft_path.exists() else ""
            word_count = count_chinese_chars(draft)
            threshold = int(marker_data.get("threshold") or _EXPAND_AUTO_SKIP_WORDS)
            reason = str(marker_data.get("reason") or "")
            message = (
                f"初稿已达到 {word_count} 字（目标 {threshold} 字），自动跳过扩写。"
                if reason in {"draft_reached_3000_words", "draft_reached_target_words"}
                else "扩写已跳过。"
            )
            return {
                "ok": True,
                "step": "expand",
                "skipped": True,
                "content": draft,
                "source_before": draft,
                "source_word_count": word_count,
                "word_count": word_count,
                "target_words": int(ch.get("target_words") or book.get("target_words_per_chapter") or 0),
                "skip": marker_data,
                "message": message,
                "threshold": threshold,
                "run_count": _step_history_count(work_dir, chapter_number, chapter_title, "expand"),
                "batch_count": batch_count,
            }
        if step_name == "review" and ch.get("ai_review_json"):
            raw = str(ch["ai_review_json"])
            try:
                review = json.loads(raw)
            except Exception:
                review = {"overall": "CONCERNS", "dimensions": {}, "raw": raw}
            review = _normalize_review_gate(review, chapter_number)
            revised_content = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
            revised_content = strip_chapter_heading(revised_content) if revised_content else ""
            return {
                "ok": True,
                "step": "review",
                "review": review,
                "force_pass": {},
                "content": raw,
                "revised_content": revised_content,
                "revised_word_count": count_chinese_chars(revised_content),
                "word_count": count_chinese_chars(raw),
                "fallback_from_final": True,
                "batch_count": batch_count,
            }
        final_draft_path = Path(ch["draft_path"]) if ch.get("draft_path") else None
        if step_name in {"draft", "expand", "polish", "deslop"} and final_draft_path and final_draft_path.exists():
            content = final_draft_path.read_text(encoding="utf-8")
            return {
                "ok": True,
                "step": step_name,
                "content": content,
                "source_before": "",
                "word_count": count_chinese_chars(content),
                "target_words": int(ch.get("target_words") or book.get("target_words_per_chapter") or 0),
                "fallback_from_final": True,
                "message": "该步骤的中间产物不存在，已显示最终成稿。",
                "run_count": 0,
                "batch_count": batch_count,
            }
        raise HTTPException(status_code=404, detail="步骤产物不存在")

    raw = step_path.read_text(encoding="utf-8")
    if step_name == "deslop":
        cleaned_raw = strip_chapter_heading(raw)
        if cleaned_raw != raw:
            step_path.write_text(cleaned_raw, encoding="utf-8")
            raw = cleaned_raw
    if step_name == "review":
        try:
            review = json.loads(raw)
        except Exception:
            review = {"overall": "CONCERNS", "dimensions": {}, "raw": raw}
        review = _normalize_review_gate(review, chapter_number)
        force_pass = _read_json_file(_step_force_read(work_dir, chapter_number, "review"))
        revised_content = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
        revised_content = strip_chapter_heading(revised_content) if revised_content else ""
        return {
            "ok": True,
            "step": "review",
            "review": review,
            "force_pass": force_pass,
            "content": raw,
            "revised_content": revised_content,
            "revised_word_count": count_chinese_chars(revised_content),
            "word_count": count_chinese_chars(raw),
            "run_count": _step_run_count(work_dir, chapter_number, chapter_title, "review"),
            "batch_count": batch_count,
        }

    gate = _read_json_file(_step_gate_read(work_dir, chapter_number, step_name))
    force_pass = _read_json_file(_step_force_read(work_dir, chapter_number, step_name))
    if step_name == "deslop" and (
        not gate.get("deai")
        or gate.get("deai", {}).get("source") != "local_text_quality"
    ):
        gate["deai"] = _score_deai_result(raw)

    # 给前端做"原文/修改后"对比用：找当前步骤的上一步内容。
    source_before = ""
    if step_name in ("expand", "polish", "deslop"):
        chain = {"expand": ["draft"], "polish": ["expand", "draft"], "deslop": ["polish", "expand", "draft"]}
        for prev in chain.get(step_name, []):
            prev_path = _step_file_read(work_dir, chapter_number, prev)
            if prev_path and prev_path.exists():
                source_before = prev_path.read_text(encoding="utf-8")
                break
        if step_name == "deslop" and source_before:
            source_before = strip_chapter_heading(source_before)

    return {
        "ok": True,
        "step": step_name,
        "content": raw,
        "source_before": source_before,
        "source_word_count": count_chinese_chars(source_before),
        "word_count": count_chinese_chars(raw),
        "target_words": int(ch.get("target_words") or book.get("target_words_per_chapter") or 0),
        "deai": gate.get("deai") if step_name == "deslop" else None,
        "force_pass": force_pass,
        "run_count": _step_run_count(work_dir, chapter_number, chapter_title, step_name),
        "batch_count": batch_count,
    }


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/skip")
def api_skip_chapter_step(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    """Mark one writing step as skipped so the UI can continue to the next step."""
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    if step_name == "draft" and not _read_step_source(work_dir, chapter_number, ["draft"]) and not ch.get("draft_path"):
        raise HTTPException(status_code=400, detail="初稿是后续步骤的正文来源。请先写初稿，或已有正文后再跳过。")
    if step_name == "finalize":
        raise HTTPException(status_code=400, detail="成稿步骤不能跳过；需要保存正文时请运行成稿。")

    marker = _step_skip_path(work_dir, chapter_number, chapter_title, step_name)
    marker.write_text(
        json.dumps({
            "step": step_name,
            "skipped": True,
            "chapter_number": chapter_number,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "step": step_name,
        "skipped": True,
        "message": f"已跳过 {step_name}",
    }


@router.put("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/content")
async def api_save_step_content(
    book_id: int,
    chapter_number: int,
    step_name: str,
    content: str = Body(..., embed=True),
) -> dict[str, Any]:
    """Save edited content for a chapter writing step."""
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = ch.get("title", "")
    from generator.long_novel.l2_chapter_write import count_chinese_chars, ensure_chapter_heading, strip_chapter_heading

    if step_name == "finalize":
        final_text = ensure_chapter_heading(content, chapter_number)
        step_file = Path(ch["draft_path"]) if ch.get("draft_path") else chapter_final_path(work_dir, chapter_number, chapter_title)
        step_file.parent.mkdir(parents=True, exist_ok=True)
        if step_file.exists():
            backup = step_file.with_suffix(".md.bak")
            if backup.exists():
                backup = step_file.with_name(
                    f"{step_file.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md.bak"
                )
            shutil.copy2(step_file, backup)
        step_file.write_text(final_text, encoding="utf-8")
        final_words = count_chinese_chars(final_text)
        _upsert_chapter_preserving(
            _db_path(),
            ch,
            status=str(ch.get("status") or "draft"),
            draft_path=str(step_file),
            actual_words=final_words,
        )
        return {
            "ok": True,
            "message": "内容已保存",
            "step": step_name,
            "content": final_text,
            "word_count": final_words,
            "final_words": final_words,
            "draft_path": str(step_file),
        }

    if step_name == "deslop":
        content = strip_chapter_heading(content)
    step_file = _step_file_path(work_dir, chapter_number, chapter_title, step_name)
    step_file.parent.mkdir(parents=True, exist_ok=True)
    step_file.write_text(content, encoding="utf-8")

    return {"ok": True, "message": "内容已保存", "step": step_name}


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/force-pass")
async def api_force_pass_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    return await run_in_threadpool(
        _api_force_pass_chapter_step_blocking,
        book_id,
        chapter_number,
        step_name,
        payload,
    )


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/revise")
async def api_revise_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    return await run_in_threadpool(
        _api_revise_chapter_step_blocking,
        book_id,
        chapter_number,
        step_name,
        payload,
    )


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/revise/start")
async def api_start_revise_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持按建议修改")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    progress_step = _revise_progress_step(step_name)
    progress_path = _step_progress_path(work_dir, chapter_number, chapter_title, progress_step)
    current = _step_status_snapshot(book_id, work_dir, ch, chapter_number, progress_step)
    if current.get("status") in {"starting", "running"}:
        return {
            "ok": True,
            "accepted": True,
            "already_running": True,
            "step": step_name,
            "progress_step": progress_step,
            "status": current.get("status"),
            "detail": current.get("detail", ""),
            "updated_at": current.get("updated_at", ""),
            "run_count": current.get("run_count") or 0,
            "batch_count": current.get("batch_count") or 0,
        }

    _write_step_progress(progress_path, "starting", "按建议修改任务已启动", {"step": step_name, "progress_step": progress_step})
    _step_job_mark(book_id, chapter_number, progress_step, True)

    def _run() -> None:
        try:
            _write_step_progress(progress_path, "running", "按建议修改中，完成后会自动复审…", {"step": step_name, "progress_step": progress_step})
            result = _api_revise_chapter_step_blocking(book_id, chapter_number, step_name, payload)
            review = result.get("review") if isinstance(result.get("review"), dict) else {}
            result_summary = {
                "word_count": int(result.get("word_count") or 0),
                "revised_word_count": int(result.get("revised_word_count") or result.get("word_count") or 0),
                "review_passed": bool(review.get("passed")),
                "run_count": int(result.get("run_count") or 0),
                "batch_count": int(result.get("batch_count") or 0),
            }
            detail = str(result.get("message") or "已按建议修改，并已自动复审")
            _write_step_progress(progress_path, "done", detail, {"step": step_name, "progress_step": progress_step, "result": result_summary})
        except HTTPException as exc:
            _write_step_progress(
                progress_path,
                "error",
                str(exc.detail)[:500],
                {"step": step_name, "progress_step": progress_step, "http_status": exc.status_code},
            )
        except Exception as exc:
            logger.exception("chapter revise failed book=%s chapter=%s step=%s", book_id, chapter_number, step_name)
            _write_step_progress(progress_path, "error", str(exc)[:500], {"step": step_name, "progress_step": progress_step})
        finally:
            _step_job_mark(book_id, chapter_number, progress_step, False)

    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True,
        "accepted": True,
        "step": step_name,
        "progress_step": progress_step,
        "status": "starting",
        "detail": "按建议修改任务已启动",
    }


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/revise/status")
def api_revise_chapter_step_progress(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持按建议修改")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    work_dir = Path(book["work_dir"])
    progress_step = _revise_progress_step(step_name)
    data = _step_status_snapshot(book_id, work_dir, ch, chapter_number, progress_step)
    return {"ok": True, **data, "step": step_name, "progress_step": progress_step}


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/history")
def api_list_step_history(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    """列出某步骤的历史版本（每次运行/重做生成一份归档）。"""
    valid_steps = set(CHAPTER_STEP_FILES.keys())
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step_name)
    if not history_dir.exists():
        return {"ok": True, "step": step_name, "versions": []}
    batch_started_at = 0.0
    if step_name != "draft":
        draft_path = _step_file_read(work_dir, chapter_number, "draft")
        if draft_path and draft_path.exists():
            try:
                batch_started_at = draft_path.stat().st_mtime
            except Exception:
                batch_started_at = 0.0
    versions: list[dict[str, Any]] = []
    for p in sorted(history_dir.iterdir(), key=lambda x: x.name, reverse=True):
        if not p.is_file():
            continue
        # Direct reruns are archived as YYYYMMDD_HHMMSS.md/json. Stale outputs
        # invalidated by rerunning an upstream step are stored separately and
        # should not appear as this step's current-batch history.
        if not re.match(r"^\d{8}_\d{6}\.(?:md|json)$", p.name):
            continue
        try:
            stat = p.stat()
            if batch_started_at and stat.st_mtime < batch_started_at:
                continue
            versions.append({
                "id": p.stem,
                "filename": p.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        except Exception:
            continue
    return {"ok": True, "step": step_name, "versions": versions}


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/history/{version_id}")
def api_read_step_history(book_id: int, chapter_number: int, step_name: str, version_id: str) -> dict[str, Any]:
    """读取某个历史版本的完整内容。"""
    valid_steps = set(CHAPTER_STEP_FILES.keys())
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    if "/" in version_id or "\\" in version_id or ".." in version_id:
        raise HTTPException(status_code=400, detail="Invalid version id")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step_name)
    matches = [p for p in history_dir.glob(f"{version_id}.*")] if history_dir.exists() else []
    if not matches:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    path = matches[0]
    content = path.read_text(encoding="utf-8")
    from generator.long_novel.l2_chapter_write import count_chinese_chars
    return {
        "ok": True,
        "step": step_name,
        "version_id": version_id,
        "content": content,
        "word_count": count_chinese_chars(content),
    }


@router.delete("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/history/{version_id}")
def api_delete_step_history(book_id: int, chapter_number: int, step_name: str, version_id: str) -> dict[str, Any]:
    """Delete one archived history version file from disk."""
    valid_steps = set(CHAPTER_STEP_FILES.keys())
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    if "/" in version_id or "\\" in version_id or ".." in version_id:
        raise HTTPException(status_code=400, detail="Invalid version id")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step_name)
    matches = [p for p in history_dir.glob(f"{version_id}.*")] if history_dir.exists() else []
    if not matches:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    path = matches[0]
    if not _path_within(path, history_dir):
        raise HTTPException(status_code=400, detail="拒绝删除历史目录外文件")
    deleted_name = path.name
    path.unlink()
    return {
        "ok": True,
        "step": step_name,
        "version_id": version_id,
        "deleted": deleted_name,
        "message": "历史版本源文件已删除",
    }


@router.put("/books/{book_id}/chapters/{chapter_number}")
async def api_update_chapter(book_id: int, chapter_number: int, request: Request) -> dict[str, Any]:
    """更新章节信息，目前只支持改章节标题。"""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    payload = await _json_payload(request)
    new_title = str(payload.get("title") or "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="章节标题不能为空")
    if len(new_title) > 60:
        raise HTTPException(status_code=400, detail="章节标题过长（限 60 字符）")

    upsert_chapter(
        _db_path(),
        book_id,
        int(ch.get("volume_number") or 1),
        chapter_number,
        title=new_title,
        status=str(ch.get("status") or "outline_only"),
        target_words=int(ch.get("target_words") or 3000),
        actual_words=int(ch.get("actual_words") or 0),
        outline_path=ch.get("outline_path"),
        draft_path=ch.get("draft_path"),
        review_status=ch.get("review_status"),
        ai_review_json=ch.get("ai_review_json"),
    )
    updated = get_chapter(_db_path(), book_id, chapter_number) or {}
    return {"ok": True, "chapter": updated, "message": "章节标题已更新"}


@router.post("/books/{book_id}/chapters/{chapter_number}/generate-title")
async def api_generate_chapter_title(book_id: int, chapter_number: int) -> dict[str, Any]:
    return await run_in_threadpool(_generate_chapter_title_blocking, book_id, chapter_number)


def _generate_chapter_title_blocking(book_id: int, chapter_number: int) -> dict[str, Any]:
    """让 LLM 根据章节大纲/正文给出标题候选；返回但不直接落库。"""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    # 优先用本地正文/初稿，没有就退化成章节大纲。
    sample_text = ""
    for step in ("deslop", "polish", "expand", "draft"):
        path = _step_file_read(work_dir, chapter_number, step)
        if path and path.exists():
            sample_text = path.read_text(encoding="utf-8")[:1500]
            break
    if not sample_text and ch.get("draft_path"):
        p = Path(ch["draft_path"])
        if p.exists():
            sample_text = p.read_text(encoding="utf-8")[:1500]
    outline = _outline_for_chapter(ch)[:1200]
    if not sample_text and not outline:
        raise HTTPException(status_code=400, detail="本章还没有大纲或正文，无法生成标题")

    client = deps._deepseek_client(book)
    system = "你是中文网文资深编辑，根据章节内容拟一个 6 到 14 字、有钩子、不剧透太多的章节小标题。只输出标题文本，不要序号、不要书名号、不要解释。"
    user = f"""书名：{book.get("title", "")}
题材：{book.get("genre", "")}
第{chapter_number}章。

章节大纲：
{outline or "（无）"}

章节正文节选：
{sample_text or "（无）"}

请给出一个 6-14 字的章节小标题，只输出标题，不要任何前缀。"""
    title = _chat_text(client, system, user, thinking=False).strip()
    # 兜底清洗：去掉书名号/引号/编号前缀。
    for ch_strip in ("《", "》", "「", "」", "\"", "'", "“", "”"):
        title = title.replace(ch_strip, "")
    title = title.lstrip("0123456789.、 -·").strip()
    title = title.splitlines()[0].strip() if title else ""
    if not title:
        raise HTTPException(status_code=500, detail="LLM 没有返回有效标题，请重试")
    if len(title) > 30:
        title = title[:30]
    return {"ok": True, "title": title}


@router.post("/books/{book_id}/review/{chapter_number}")
async def api_review_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Run the 4-dimension review on an existing chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch or not ch.get("draft_path"):
        raise HTTPException(status_code=400, detail="章节尚未生成正文")

    from generator.long_novel.l4_review import run_full_review

    client = deps._deepseek_client(book)
    work_dir = Path(book["work_dir"])

    draft_path = Path(ch["draft_path"])
    chapter_content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
    outline_path = ch.get("outline_path")
    outline_text = Path(outline_path).read_text(encoding="utf-8") if outline_path and Path(outline_path).exists() else ""

    review = run_full_review(client, chapter_content, work_dir, chapter_number, outline_text)

    import json as _json
    _upsert_chapter_preserving(
        _db_path(), ch,
        review_status=review["overall"],
        ai_review_json=_json.dumps(review, ensure_ascii=False),
    )

    return {"ok": True, "review": review}


# ── Pipeline: Rewrite Chapter (L3) ────────────────────────────────────


@router.post("/books/{book_id}/reset-chapter/{chapter_number}")
async def api_reset_chapter_for_regeneration(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Delete active chapter outputs so the chapter can be generated from scratch."""
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    chapter = get_chapter(db_path, book_id, chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    if _autopilot_job_active(book_id):
        raise HTTPException(status_code=409, detail="全自动任务正在运行，请暂停后再删除正文")
    for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]:
        if _step_job_active(book_id, chapter_number, step_name):
            raise HTTPException(status_code=409, detail=f"本章 {step_name} 步骤仍在运行，请等待完成后再删除正文")

    work_dir = Path(str(book["work_dir"] or ""))
    final_path = Path(str(chapter["draft_path"])) if chapter.get("draft_path") else None
    has_final = bool(final_path and final_path.exists())
    has_steps = any(_step_file_read(work_dir, chapter_number, step) for step in CHAPTER_STEP_FILES)
    if has_final or has_steps:
        reset = _archive_and_reset_chapter_outputs(db_path, book_id, book, chapter)
    else:
        _reset_chapter_row_for_deleted_outputs(db_path, book_id, book, chapter)
        reset = {
            "archived_files": [],
            "archive_dir": "",
            "later_written_chapters": [
                int(item["chapter_number"])
                for item in list_chapters(db_path, book_id)
                if int(item.get("chapter_number") or 0) > chapter_number
                and item.get("draft_path")
                and Path(str(item["draft_path"])).exists()
            ],
        }
    later = reset["later_written_chapters"]
    message = f"第{chapter_number}章正文已归档并删除"
    if later:
        message += f"；后续已有 {len(later)} 章正文，重写后请检查连续性"
    _sync_tracking_after_chapter_reset(db_path, book_id, book, [chapter_number])
    _write_reset_idle_autopilot_snapshot(book, message)
    return {
        "ok": True,
        "chapter": chapter_number,
        "message": message,
        **reset,
    }


@router.post("/books/{book_id}/reset-chapters")
async def api_reset_chapter_range_for_regeneration(book_id: int, request: Request) -> dict[str, Any]:
    """Archive and clear an inclusive chapter range for fresh generation."""
    payload = await _json_payload(request)
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    reset = _reset_chapter_range_for_regeneration(
        db_path,
        book_id,
        book,
        payload.get("chapter_start"),
        payload.get("chapter_end"),
    )
    count = len(reset["reset_chapters"])
    message = f"已归档并清空 {count} 章正文"
    _sync_tracking_after_chapter_reset(db_path, book_id, book, reset["reset_chapters"])
    _write_reset_idle_autopilot_snapshot(book, message)
    return {
        "ok": True,
        "message": message,
        **reset,
    }


def _cascade_continuity_issues(
    client: Any,
    work_dir: Path,
    chapters: list[dict[str, Any]],
    *,
    after_chapter: int,
) -> list[dict[str, Any]]:
    from generator.long_novel.l2_chapter_write import run_continuity_check

    cascade_issues: list[dict[str, Any]] = []
    for chapter in chapters:
        chapter_number = int(chapter["chapter_number"])
        if chapter_number <= int(after_chapter) or not chapter.get("draft_path"):
            continue
        draft_path = Path(str(chapter["draft_path"]))
        if not draft_path.exists():
            continue
        result = run_continuity_check(
            client,
            work_dir,
            chapter_number,
            draft_path.read_text(encoding="utf-8"),
        )
        if result.get("issue_count", 0) > 0:
            cascade_issues.append({"chapter": chapter_number, "issues": result["issues"]})
    return cascade_issues


def _rewrite_chapter_blocking(
    book_id: int,
    chapter_number: int,
    *,
    client: Any | None = None,
    check_cascade: bool = True,
) -> dict[str, Any]:
    """Rewrite one saved chapter from its prior text and optionally check later continuity."""
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    from generator.long_novel.l2_chapter_write import (
        count_chinese_chars,
        ensure_chapter_heading,
        rewrite_chapter_from_source,
        run_deslop,
        run_polish,
        update_tracking_files,
    )

    active_client = client or deps._deepseek_client(book)
    work_dir = Path(book["work_dir"])
    chapter = get_chapter(db_path, book_id, chapter_number)
    if not chapter or not chapter.get("draft_path"):
        raise HTTPException(status_code=400, detail=f"第{chapter_number}章尚未生成正文")
    old_path = Path(str(chapter["draft_path"]))
    if not old_path.exists():
        raise HTTPException(status_code=400, detail=f"第{chapter_number}章正文文件不存在")
    source_text = old_path.read_text(encoding="utf-8")

    # Keep the prior text available even if rewriting fails midway.
    backup = old_path.with_suffix(".md.bak")
    if backup.exists():
        backup = old_path.with_name(
            f"{old_path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md.bak"
    )
    shutil.copy2(old_path, backup)
    _invalidate_outputs_after_step(db_path, book_id, book, chapter, "draft")

    # Rewrite this chapter from its own source text. Loading global tracking
    # here would leak later chapters into rewrites of an earlier chapter.
    chapter_title = str(chapter.get("title") or "")
    draft = rewrite_chapter_from_source(
        active_client,
        source_text,
        chapter_number,
        chapter_title,
        _outline_for_chapter(chapter),
    )
    _archive_step_version(work_dir, chapter_number, chapter_title, "draft")
    step_draft_path = _step_file_path(work_dir, chapter_number, chapter_title, "draft")
    step_draft_path.write_text(draft, encoding="utf-8")

    polished = run_polish(active_client, draft)
    step_polish_path = _step_file_path(work_dir, chapter_number, chapter_title, "polish")
    step_polish_path.write_text(polished, encoding="utf-8")

    final = ensure_chapter_heading(run_deslop(active_client, polished), chapter_number)
    step_deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
    step_deslop_path.write_text(final, encoding="utf-8")

    draft_path = chapter_final_path(work_dir, chapter_number, chapter_title)
    draft_path.write_text(final, encoding="utf-8")

    all_chapters = list_chapters(db_path, book_id)
    cascade_issues = (
        _cascade_continuity_issues(
            active_client,
            work_dir,
            all_chapters,
            after_chapter=chapter_number,
        )
        if check_cascade
        else []
    )
    has_later_draft = any(
        int(item.get("chapter_number") or 0) > chapter_number
        and item.get("draft_path")
        and Path(str(item["draft_path"])).exists()
        for item in all_chapters
    )
    update_tracking_files(
        work_dir,
        chapter_number,
        final,
        active_client,
        advance_current=not has_later_draft,
    )
    upsert_chapter(
        db_path,
        book_id,
        int(chapter.get("volume_number") or 1),
        chapter_number,
        title=str(chapter.get("title") or ""),
        status="draft",
        draft_path=str(draft_path),
        target_words=int(chapter.get("target_words") or book["target_words_per_chapter"]),
        actual_words=count_chinese_chars(final),
        outline_path=chapter.get("outline_path"),
        review_status=None,
        ai_review_json=None,
    )
    return {
        "ok": True,
        "chapter": chapter_number,
        "message": f"第{chapter_number}章已重写",
        "cascade_affected": len(cascade_issues),
        "cascade_issues": cascade_issues,
        "batch_count": _chapter_batch_count(work_dir, chapter_number, chapter_title),
    }


@router.post("/books/{book_id}/rewrite-chapter/{chapter_number}")
async def api_rewrite_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Rewrite a chapter and check cascade continuity."""
    return await run_in_threadpool(_rewrite_chapter_blocking, book_id, chapter_number)


@router.post("/books/{book_id}/rewrite-chapters")
async def api_rewrite_chapter_range(book_id: int, request: Request) -> dict[str, Any]:
    """Rewrite an inclusive range in the background and expose progress in the autopilot panel."""
    payload = await _json_payload(request)
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    if _autopilot_job_active(book_id):
        raise HTTPException(status_code=409, detail="全自动任务正在运行，请暂停后再批量改写")
    chapters = _chapter_range(
        db_path,
        book_id,
        payload.get("chapter_start"),
        payload.get("chapter_end"),
    )
    for chapter in chapters:
        chapter_number = int(chapter["chapter_number"])
        if not chapter.get("draft_path") or not Path(str(chapter["draft_path"])).exists():
            raise HTTPException(status_code=400, detail=f"第{chapter_number}章还没有可用旧稿")
        for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]:
            if _step_job_active(book_id, chapter_number, step_name):
                raise HTTPException(
                    status_code=409,
                    detail=f"第{chapter_number}章 {step_name} 步骤仍在运行，请等待完成后再批量改写",
                )

    work_dir = Path(str(book["work_dir"] or ""))
    work_dir.mkdir(parents=True, exist_ok=True)
    chapter_numbers = [int(chapter["chapter_number"]) for chapter in chapters]
    total = len(chapter_numbers)
    _set_cancel(book_id, False)

    def _snapshot(
        state: str,
        *,
        detail: str,
        results: list[dict[str, Any]],
        current: int = 0,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "operation": "batch_rewrite",
            "operation_label": "批量重写",
            "phase": "batch_rewrite",
            "stage": "writing",
            "detail": detail,
            "writing": {
                "total": total,
                "done": len(results),
                "current": current,
                "current_status": "rewriting" if state == "running" and current else "",
                "results": list(results),
            },
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }

    from generator.long_novel.autopilot import write_autopilot_file

    def _run() -> None:
        results: list[dict[str, Any]] = []
        try:
            active_client = deps._deepseek_client(book)
            for chapter_number in chapter_numbers:
                if _is_cancelled(book_id):
                    write_autopilot_file(
                        work_dir,
                        _snapshot("cancelled", detail="批量改写已暂停，已完成的章节会保留", results=results),
                    )
                    return
                write_autopilot_file(
                    work_dir,
                    _snapshot(
                        "running",
                        detail=f"正在重写第{chapter_number}章",
                        results=results,
                        current=chapter_number,
                    ),
                )
                _rewrite_chapter_blocking(
                    book_id,
                    chapter_number,
                    client=active_client,
                    check_cascade=False,
                )
                results.append({"chapter": chapter_number, "status": "rewritten"})

            cascade_issues = _cascade_continuity_issues(
                active_client,
                work_dir,
                list_chapters(db_path, book_id),
                after_chapter=chapter_numbers[0] - 1,
            )
            detail = f"批量改写完成：共 {total} 章"
            if cascade_issues:
                detail += f"，连续性检查发现 {len(cascade_issues)} 章需要留意"
            write_autopilot_file(
                work_dir,
                {
                    **_snapshot("done", detail=detail, results=results),
                    "cascade_affected": len(cascade_issues),
                    "cascade_issues": cascade_issues,
                },
            )
        except Exception as exc:
            write_autopilot_file(
                work_dir,
                _snapshot("error", detail=str(exc)[:300], results=results),
            )
            logger.exception("batch rewrite failed for book %s", book_id)
        finally:
            _autopilot_job_mark(book_id, False)

    write_autopilot_file(
        work_dir,
        _snapshot("running", detail=f"准备批量改写第{chapter_numbers[0]}-{chapter_numbers[-1]}章", results=[]),
    )
    _autopilot_job_mark(book_id, True)
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _autopilot_job_mark(book_id, False)
        raise
    return {
        "ok": True,
        "accepted": True,
        "message": f"已开始批量改写第{chapter_numbers[0]}-{chapter_numbers[-1]}章",
        "chapter_start": chapter_numbers[0],
        "chapter_end": chapter_numbers[-1],
        "chapter_count": total,
    }


__all__ = ["router"]
