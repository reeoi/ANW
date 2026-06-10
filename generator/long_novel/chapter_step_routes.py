"""逐步写章（L2 step-by-step）路由：步骤运行/轮询/产物读取/跳过/保存/
强行通过/按建议修改、历史版本管理。

阻塞引擎在 ``chapter_steps``；本模块只做 HTTP 编排（后台线程 + 进度文件）。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from generator.long_novel.chapter_resets import _path_within
from generator.long_novel.chapter_steps import (
    _api_force_pass_chapter_step_blocking,
    _api_revise_chapter_step_blocking,
    _api_write_chapter_blocking,
    _api_write_chapter_step_blocking,
    _revise_progress_step,
)
from generator.long_novel.db import get_book, get_chapter
from generator.long_novel.deps import _db_path, _json_payload, _upsert_chapter_preserving
from generator.long_novel.jobs import _step_job_mark
from generator.long_novel.l2_chapter_write import (
    CHAPTER_STEP_FILES,
    chapter_final_path,
)
from generator.long_novel.review_gate import (
    _EXPAND_AUTO_SKIP_WORDS,
    _normalize_review_gate,
    _score_deai_result,
)
from generator.long_novel.step_artifacts import (
    _chapter_batch_count,
    _finalize_run_count,
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

router = APIRouter()


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
