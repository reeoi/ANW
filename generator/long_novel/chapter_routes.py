"""章节级路由：标题编辑/生成、整章审查（L4）、按章/按范围删除重写（L3）。

阻塞实现 ``_rewrite_chapter_blocking`` 与级联连续性检查也在本模块；
批量改写在后台线程跑，并把进度写进 autopilot 快照供监控面板展示。
"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from generator.long_novel import deps
from generator.long_novel.chapter_resets import (
    _archive_and_reset_chapter_outputs,
    _chapter_range,
    _invalidate_outputs_after_step,
    _reset_chapter_range_for_regeneration,
    _reset_chapter_row_for_deleted_outputs,
    _sync_tracking_after_chapter_reset,
    _write_reset_idle_autopilot_snapshot,
)
from generator.long_novel.db import get_book, get_chapter, list_chapters, upsert_chapter
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
)
from generator.long_novel.l2_chapter_write import (
    CHAPTER_STEP_FILES,
    chapter_final_path,
)
from generator.long_novel.step_artifacts import (
    _archive_step_version,
    _chapter_batch_count,
    _outline_for_chapter,
    _step_file_path,
    _step_file_read,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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
