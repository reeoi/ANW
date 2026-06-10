"""L2 章节写作步骤引擎（无路由）。

原 ``api._api_write_chapter_step_blocking`` 是 342 行巨函数；这里按 step 拆为
7 个处理函数，调度器负责校验与装配共享上下文。每个处理函数体与原分支逐字
一致；``run_draft`` 等 l2 函数保持调用时懒导入，使测试对 l2 模块属性的
monkeypatch 继续生效。LLM 客户端经 ``deps._deepseek_client`` 模块属性获取。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from generator.long_novel import deps, prompt_kit
from generator.long_novel.chapter_resets import (
    _archive_and_remove_step_artifact,
    _has_later_saved_chapter,
    _invalidate_outputs_after_step,
)
from generator.long_novel.db import get_book, get_chapter, update_book, upsert_chapter
from generator.long_novel.deps import _chat_text, _db_path, _upsert_chapter_preserving
from generator.long_novel.l2_chapter_write import chapter_final_path
from generator.long_novel.review_gate import (
    _expand_skip_threshold,
    _normalize_review_gate,
    _review_issue_count,
    _review_recommendation_text,
    _score_deai_result,
)
from generator.long_novel.step_artifacts import (
    _LEGACY_STEP_FILES,
    _archive_step_version,
    _chapter_batch_count,
    _draft_context_manifest,
    _finalize_run_count,
    _outline_for_chapter,
    _read_json_file,
    _read_step_source,
    _step_file_path,
    _step_file_read,
    _step_force_path,
    _step_force_read,
    _step_gate_path,
    _step_gate_read,
    _step_history_count,
    _step_run_count,
    _step_skip_path,
    _step_skip_read,
)

logger = logging.getLogger(__name__)

# 共享实现见 prompt_kit；保留旧私有名，调用点零改动。
_load_prompt_template = prompt_kit.load_prompt_template
_render_prompt_template = prompt_kit.render_prompt_template


def _api_write_chapter_blocking(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Run the full L2 chapter writing pipeline for a single chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import run_full_chapter

    client = deps._deepseek_client(book)
    work_dir = Path(book["work_dir"])

    _upsert_chapter_preserving(_db_path(), ch, status="writing")
    update_book(_db_path(), book_id, current_chapter=chapter_number)

    result = run_full_chapter(
        client, work_dir, chapter_number,
        chapter_title=ch.get("title", ""),
        target_words=ch.get("target_words", book["target_words_per_chapter"]),
    )

    # Auto-run 4-dimension review
    from generator.long_novel.l4_review import run_full_review
    chapter_content = Path(result["draft_path"]).read_text(encoding="utf-8") if result.get("draft_path") else ""
    outline_path = ch.get("outline_path")
    outline_text = Path(outline_path).read_text(encoding="utf-8") if outline_path and Path(outline_path).exists() else ""

    review = run_full_review(
        client, chapter_content, work_dir, chapter_number, outline_text,
    )

    import json as _json
    _upsert_chapter_preserving(
        _db_path(), ch,
        status="draft",
        draft_path=result["draft_path"],
        actual_words=result["final_words"],
        review_status=review["overall"],
        ai_review_json=_json.dumps(review, ensure_ascii=False),
    )

    result["review"] = review
    return {"ok": True, "message": f"第{chapter_number}章写作完成", "result": result}


def _api_write_chapter_step_blocking(
    book_id: int,
    chapter_number: int,
    step_name: str,
    force: bool = False,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run a single step of the L2 chapter writing pipeline.

    Steps: draft | expand | polish | deslop | continuity | finalize

    Each step (except finalize) saves intermediate output to the work_dir
    so the next step can pick it up. The frontend can show each output before
    the user decides to continue.
    """
    valid_steps = {"draft", "expand", "polish", "review", "deslop", "continuity", "finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    client = client or deps._deepseek_client(book)
    work_dir = Path(book["work_dir"])
    target_words = ch.get("target_words", book["target_words_per_chapter"])
    chapter_title = ch.get("title", "")
    expand_threshold = _expand_skip_threshold(target_words)
    batch_count = _chapter_batch_count(work_dir, chapter_number, str(chapter_title or ""))

    handler = _STEP_HANDLERS[step_name]
    return handler(
        book_id, chapter_number, book, ch, client, work_dir,
        chapter_title, target_words, expand_threshold, batch_count, force,
    )


def _run_draft_step(
    book_id: int, chapter_number: int, book: dict[str, Any], ch: dict[str, Any],
    client: Any, work_dir: Path, chapter_title: Any, target_words: Any,
    expand_threshold: int, batch_count: int, force: bool,
) -> dict[str, Any]:
    from generator.long_novel.l2_chapter_write import assemble_context, count_chinese_chars, run_draft

    if ch.get("status") not in ("outline_only", "writing", "draft", "published"):
        raise HTTPException(status_code=400, detail=f"章节状态 {ch.get('status')} 无法开始写作")
    _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "draft")
    _upsert_chapter_preserving(
        _db_path(),
        ch,
        status="writing",
        actual_words=0,
        draft_path=None,
        review_status=None,
        ai_review_json=None,
    )
    if not _has_later_saved_chapter(_db_path(), book_id, chapter_number):
        update_book(_db_path(), book_id, current_chapter=chapter_number)
    _archive_step_version(work_dir, chapter_number, chapter_title, "draft")
    draft = run_draft(client, work_dir, chapter_number, chapter_title, target_words)
    draft_words = count_chinese_chars(draft)
    draft_path = _step_file_path(work_dir, chapter_number, chapter_title, "draft")
    draft_path.write_text(draft, encoding="utf-8")
    ctx = assemble_context(work_dir, chapter_number, chapter_title, target_words)
    run_count = _step_run_count(work_dir, chapter_number, chapter_title, "draft")
    batch_count = _chapter_batch_count(work_dir, chapter_number, str(chapter_title or ""))
    return {
        "ok": True, "step": "draft", "word_count": draft_words,
        "content": draft, "target_words": target_words,
        "llm_context": _draft_context_manifest(ctx),
        "needs_expand": draft_words < expand_threshold,
        "next_step": "expand" if draft_words < expand_threshold else "polish",
        "run_count": run_count,
        "batch_count": batch_count,
    }


def _run_expand_step(
    book_id: int, chapter_number: int, book: dict[str, Any], ch: dict[str, Any],
    client: Any, work_dir: Path, chapter_title: Any, target_words: Any,
    expand_threshold: int, batch_count: int, force: bool,
) -> dict[str, Any]:
    from generator.long_novel.l2_chapter_write import count_chinese_chars, run_expand

    draft_path = _step_file_read(work_dir, chapter_number, "draft")
    if not draft_path or not draft_path.exists():
        raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
    draft = draft_path.read_text(encoding="utf-8")
    draft_words = count_chinese_chars(draft)
    _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "expand")
    if draft_words >= expand_threshold and not force:
        _archive_and_remove_step_artifact(work_dir, chapter_number, chapter_title, "expand")
        marker = _step_skip_path(work_dir, chapter_number, chapter_title, "expand")
        marker.write_text(
            json.dumps(
                {
                    "step": "expand",
                    "skipped": True,
                    "reason": "draft_reached_target_words",
                    "word_count": draft_words,
                    "threshold": expand_threshold,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "step": "expand",
            "skipped": True,
            "word_count": draft_words,
            "content": draft,
            "target_words": target_words,
            "source_before": draft,
            "source_word_count": draft_words,
            "next_step": "polish",
            "message": f"初稿已达到 {draft_words} 字（目标 {expand_threshold} 字），自动跳过扩写。",
            "run_count": _step_history_count(work_dir, chapter_number, chapter_title, "expand"),
            "batch_count": batch_count,
        }
    if force:
        try:
            old_marker = _step_skip_read(work_dir, chapter_number, "expand")
            if old_marker:
                old_marker.unlink(missing_ok=True)
        except Exception:
            logger.exception("remove_expand_skip_marker_failed book=%s chapter=%s", book_id, chapter_number)
    _archive_step_version(work_dir, chapter_number, chapter_title, "expand")
    expanded = run_expand(client, draft, target_words)
    expanded_words = count_chinese_chars(expanded)
    expand_path = _step_file_path(work_dir, chapter_number, chapter_title, "expand")
    expand_path.write_text(expanded, encoding="utf-8")
    run_count = _step_run_count(work_dir, chapter_number, chapter_title, "expand")
    return {
        "ok": True, "step": "expand", "word_count": expanded_words,
        "content": expanded, "target_words": target_words,
        "source_before": draft,
        "source_word_count": draft_words,
        "next_step": "polish",
        "run_count": run_count,
        "batch_count": batch_count,
    }


def _run_polish_step(
    book_id: int, chapter_number: int, book: dict[str, Any], ch: dict[str, Any],
    client: Any, work_dir: Path, chapter_title: Any, target_words: Any,
    expand_threshold: int, batch_count: int, force: bool,
) -> dict[str, Any]:
    from generator.long_novel.l2_chapter_write import count_chinese_chars, run_polish

    expand_path = _step_file_read(work_dir, chapter_number, "expand")
    draft_path = _step_file_read(work_dir, chapter_number, "draft")
    if expand_path and expand_path.exists():
        source = expand_path.read_text(encoding="utf-8")
    elif draft_path and draft_path.exists():
        source = draft_path.read_text(encoding="utf-8")
    else:
        raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
    _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "polish")
    polished = run_polish(client, source)
    polished_words = count_chinese_chars(polished)
    polish_path = _step_file_path(work_dir, chapter_number, chapter_title, "polish")
    _archive_step_version(work_dir, chapter_number, chapter_title, "polish")
    polish_path.write_text(polished, encoding="utf-8")
    run_count = _step_run_count(work_dir, chapter_number, chapter_title, "polish")
    return {
        "ok": True, "step": "polish", "word_count": polished_words,
        "content": polished, "next_step": "deslop",
        "source_before": source,
        "source_word_count": count_chinese_chars(source),
        "run_count": run_count,
        "batch_count": batch_count,
    }


def _run_review_step(
    book_id: int, chapter_number: int, book: dict[str, Any], ch: dict[str, Any],
    client: Any, work_dir: Path, chapter_title: Any, target_words: Any,
    expand_threshold: int, batch_count: int, force: bool,
) -> dict[str, Any]:
    source = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
    if not source:
        raise HTTPException(status_code=400, detail="请先运行去 AI 步骤")
    _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "review")
    from generator.long_novel.l4_review import run_story_review
    review = run_story_review(client, source, work_dir, chapter_number, _outline_for_chapter(ch))
    review_path = _step_file_path(work_dir, chapter_number, chapter_title, "review")
    _archive_step_version(work_dir, chapter_number, chapter_title, "review")
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    run_count = _step_run_count(work_dir, chapter_number, chapter_title, "review")
    return {
        "ok": True,
        "step": "review",
        "review": review,
        "next_step": "finalize",
        "run_count": run_count,
        "batch_count": batch_count,
    }


def _run_deslop_step(
    book_id: int, chapter_number: int, book: dict[str, Any], ch: dict[str, Any],
    client: Any, work_dir: Path, chapter_title: Any, target_words: Any,
    expand_threshold: int, batch_count: int, force: bool,
) -> dict[str, Any]:
    from generator.long_novel.l2_chapter_write import count_chinese_chars, run_deslop, strip_chapter_heading

    polish_path = _step_file_read(work_dir, chapter_number, "polish")
    expand_path = _step_file_read(work_dir, chapter_number, "expand")
    draft_path = _step_file_read(work_dir, chapter_number, "draft")
    if polish_path and polish_path.exists():
        source = polish_path.read_text(encoding="utf-8")
    elif expand_path and expand_path.exists():
        source = expand_path.read_text(encoding="utf-8")
    elif draft_path and draft_path.exists():
        source = draft_path.read_text(encoding="utf-8")
    else:
        raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
    _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "deslop")
    final = strip_chapter_heading(run_deslop(client, source))
    final_words = count_chinese_chars(final)
    deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
    _archive_step_version(work_dir, chapter_number, chapter_title, "deslop")
    deslop_path.write_text(final, encoding="utf-8")
    deai = _score_deai_result(final)
    gate_path = _step_gate_path(work_dir, chapter_number, chapter_title, "deslop")
    gate_path.write_text(
        json.dumps({"deai": deai}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    run_count = _step_run_count(work_dir, chapter_number, chapter_title, "deslop")
    return {
        "ok": True, "step": "deslop", "word_count": final_words,
        "content": final, "deai": deai, "next_step": "review",
        "source_before": strip_chapter_heading(source),
        "source_word_count": count_chinese_chars(strip_chapter_heading(source)),
        "run_count": run_count,
        "batch_count": batch_count,
    }


def _run_continuity_step(
    book_id: int, chapter_number: int, book: dict[str, Any], ch: dict[str, Any],
    client: Any, work_dir: Path, chapter_title: Any, target_words: Any,
    expand_threshold: int, batch_count: int, force: bool,
) -> dict[str, Any]:
    from generator.long_novel.l2_chapter_write import run_continuity_check

    deslop_path = _step_file_read(work_dir, chapter_number, "deslop")
    polish_path = _step_file_read(work_dir, chapter_number, "polish")
    if deslop_path and deslop_path.exists():
        source = deslop_path.read_text(encoding="utf-8")
    elif polish_path and polish_path.exists():
        source = polish_path.read_text(encoding="utf-8")
    else:
        raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
    if chapter_number <= 1:
        return {
            "ok": True,
            "step": "continuity",
            "skipped": True,
            "reason": "第一章无需连续性检查",
            "next_step": "finalize",
            "run_count": 0,
            "batch_count": batch_count,
        }
    continuity = run_continuity_check(client, work_dir, chapter_number, source)
    return {
        "ok": True, "step": "continuity",
        "issues": continuity.get("issues", []),
        "issue_count": continuity.get("issue_count", 0),
        "passed": continuity.get("ok", False),
        "next_step": "finalize",
        "run_count": 1,
        "batch_count": batch_count,
    }


def _run_finalize_step(
    book_id: int, chapter_number: int, book: dict[str, Any], ch: dict[str, Any],
    client: Any, work_dir: Path, chapter_title: Any, target_words: Any,
    expand_threshold: int, batch_count: int, force: bool,
) -> dict[str, Any]:
    # Save the post-deAI text, update tracking, and persist review.
    # All intermediate step files (初稿/扩写/润色/去AI/审查) are kept inside
    # the chapter folder per user request.
    from generator.long_novel.l2_chapter_write import (
        count_chinese_chars,
        ensure_chapter_heading,
        update_tracking_files,
    )

    final_text = ""
    for step in ("deslop", "polish", "expand", "draft"):
        sp = _step_file_read(work_dir, chapter_number, step)
        if sp and sp.exists():
            final_text = sp.read_text(encoding="utf-8")
            break
    if not final_text:
        raise HTTPException(status_code=400, detail="请先运行至少一个写作步骤")

    final_text = ensure_chapter_heading(final_text, chapter_number)
    final_words = count_chinese_chars(final_text)

    final_draft_path = chapter_final_path(work_dir, chapter_number, chapter_title)
    if final_draft_path.exists():
        backup = final_draft_path.with_suffix(".md.bak")
        if backup.exists():
            backup = final_draft_path.with_name(
                f"{final_draft_path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md.bak"
            )
        shutil.copy2(final_draft_path, backup)
    final_draft_path.write_text(final_text, encoding="utf-8")

    has_later_draft = _has_later_saved_chapter(_db_path(), book_id, chapter_number)
    update_tracking_files(
        work_dir,
        chapter_number,
        final_text,
        client,
        advance_current=not has_later_draft,
    )

    review_existing = _step_file_read(work_dir, chapter_number, "review")
    if review_existing and review_existing.exists():
        review = json.loads(review_existing.read_text(encoding="utf-8"))
    else:
        from generator.long_novel.l4_review import run_story_review
        review = run_story_review(client, final_text, work_dir, chapter_number, _outline_for_chapter(ch))
        # Persist the review next to the chapter so it survives finalize.
        review_path = _step_file_path(work_dir, chapter_number, chapter_title, "review")
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    upsert_chapter(
        _db_path(), book_id, int(ch.get("volume_number") or 1), chapter_number,
        title=str(ch.get("title") or ""),
        status="draft", draft_path=str(final_draft_path),
        target_words=int(ch.get("target_words") or book["target_words_per_chapter"]),
        actual_words=final_words,
        outline_path=ch.get("outline_path"),
        review_status=review.get("overall", "CONCERNS"),
        ai_review_json=json.dumps(review, ensure_ascii=False),
    )

    # Migrate any legacy `_step_*` files at work_dir root into the chapter
    # folder, then remove the legacy copies (one-time cleanup per chapter).
    for step, legacy_name in _LEGACY_STEP_FILES.items():
        legacy = work_dir / legacy_name
        if legacy.exists():
            target = _step_file_path(work_dir, chapter_number, chapter_title, step)
            if not target.exists():
                try:
                    target.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass
            try:
                legacy.unlink()
            except Exception:
                pass

    return {
        "ok": True, "step": "finalize",
        "final_words": final_words,
        "draft_path": str(final_draft_path),
        "content": final_text,
        "review": review,
        "message": f"第{chapter_number}章已保存，共{final_words}字",
        "run_count": _finalize_run_count(final_draft_path),
        "batch_count": batch_count,
    }


_STEP_HANDLERS = {
    "draft": _run_draft_step,
    "expand": _run_expand_step,
    "polish": _run_polish_step,
    "review": _run_review_step,
    "deslop": _run_deslop_step,
    "continuity": _run_continuity_step,
    "finalize": _run_finalize_step,
}


def _api_force_pass_chapter_step_blocking(
    book_id: int,
    chapter_number: int,
    step_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持强行通过")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    marker = _step_force_path(work_dir, chapter_number, chapter_title, step_name)
    data = {
        "step": step_name,
        "force_passed": True,
        "reason": str(payload.get("reason") or "人工强行通过"),
        "chapter_number": chapter_number,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    marker.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "step": step_name, "force_pass": data, "message": "已记录强行通过"}


def _revise_progress_step(step_name: str) -> str:
    return f"{step_name}_revise"


def _api_revise_chapter_step_blocking(
    book_id: int,
    chapter_number: int,
    step_name: str,
    payload: dict[str, Any],
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持按建议修改")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import count_chinese_chars, run_deslop, strip_chapter_heading

    client = client or deps._deepseek_client(book)
    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    extra_prompt = str(payload.get("prompt") or "").strip()

    if step_name == "review":
        source = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
        if not source:
            raise HTTPException(status_code=400, detail="没有可修改的正文来源，请先运行去 AI")
        review = _read_json_file(_step_file_read(work_dir, chapter_number, "review"))
        review = _normalize_review_gate(review, chapter_number) if review else {}
        suggestions = _review_recommendation_text(review)
        previous_issue_count = _review_issue_count(review)
        system = _load_prompt_template(
            "l2_review_fix_system.txt",
            "你是长篇网文改稿编辑。你的任务是逐条落实审查问题，不是笼统润色。只输出修改后的完整正文，不要解释。",
        )
        user_template = _load_prompt_template("l2_review_fix_user.txt", "请根据审查建议修改第{chapter_number}章。\n{source}")
        user = _render_prompt_template(user_template, {
            "chapter_number": chapter_number,
            "outline": _outline_for_chapter(ch)[:2000],
            "suggestions": suggestions or "没有结构化建议，请整体提升连续性、逻辑、剧情推进、人设、环境与共情。",
            "extra_prompt": extra_prompt or "无",
            "source": source,
        })
        revised = _chat_text(client, system, user, thinking=True).strip()
        revised = strip_chapter_heading(run_deslop(client, revised))
        deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
        _archive_step_version(work_dir, chapter_number, chapter_title, "deslop")
        deslop_path.write_text(revised, encoding="utf-8")
        deai = _score_deai_result(revised)
        gate_path = _step_gate_path(work_dir, chapter_number, chapter_title, "deslop")
        gate_path.write_text(
            json.dumps({"deai": deai}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        from generator.long_novel.l4_review import run_story_review
        new_review = run_story_review(client, revised, work_dir, chapter_number, _outline_for_chapter(ch))
        new_review = _normalize_review_gate(new_review, chapter_number)
        new_review["revision_audit"] = {
            "mode": "review_fix_then_auto_recheck",
            "source_step": "deslop",
            "previous_overall": review.get("overall"),
            "previous_score": review.get("score"),
            "previous_issue_count": previous_issue_count,
            "new_overall": new_review.get("overall"),
            "new_score": new_review.get("score"),
            "remaining_issue_count": _review_issue_count(new_review),
            "remaining_summary": _review_recommendation_text(new_review)[:1800],
        }
        review_path = _step_file_path(work_dir, chapter_number, chapter_title, "review")
        _archive_step_version(work_dir, chapter_number, chapter_title, "review")
        review_path.write_text(json.dumps(new_review, ensure_ascii=False, indent=2), encoding="utf-8")
        force = _step_force_read(work_dir, chapter_number, "review")
        if force and force.exists():
            force.unlink()
        passed = bool(new_review.get("passed"))
        msg = "已按审查建议修改，并已自动复审"
        msg += "：审查已通过" if passed else "：仍有未解决项，请查看新的审查结果"
        return {
            "ok": True,
            "step": "review",
            "revised_step": "deslop",
            "content": revised,
            "word_count": count_chinese_chars(revised),
            "deai": deai,
            "review": new_review,
            "revised_content": revised,
            "revised_word_count": count_chinese_chars(revised),
            "source_before": source,
            "source_word_count": count_chinese_chars(source),
            "run_count": _step_run_count(work_dir, chapter_number, chapter_title, "review"),
            "batch_count": _chapter_batch_count(work_dir, chapter_number, chapter_title),
            "message": msg,
        }

    source = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
    if not source:
        raise HTTPException(status_code=400, detail="没有可去 AI 的正文来源")
    gate = _read_json_file(_step_gate_read(work_dir, chapter_number, "deslop"))
    deai = gate.get("deai") or {}
    suggestions = "\n".join([*(deai.get("findings") or []), *(deai.get("recommendations") or [])])
    system = _load_prompt_template(
        "l2_deslop_fix_system.txt",
        "你是中文网文资深去 AI 味编辑。只改文风，不改剧情、人设、关系、伏笔和章节推进。只输出修改后的完整正文，不要解释。",
    )
    user_template = _load_prompt_template("l2_deslop_fix_user.txt", "请继续降低第{chapter_number}章的 AI 味。\n{source}")
    user = _render_prompt_template(user_template, {
        "chapter_number": chapter_number,
        "suggestions": suggestions or "重点减少工整模板句、抽象情绪、泛泛转折和排比说明。",
        "extra_prompt": extra_prompt or "无",
        "source": source,
    })
    revised = _chat_text(client, system, user, thinking=True).strip()
    revised = strip_chapter_heading(run_deslop(client, revised))
    deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
    if deslop_path.exists():
        deslop_path.with_suffix(".md.bak").write_text(deslop_path.read_text(encoding="utf-8"), encoding="utf-8")
    deslop_path.write_text(revised, encoding="utf-8")
    deai = _score_deai_result(revised)
    gate_path = _step_gate_path(work_dir, chapter_number, chapter_title, "deslop")
    gate_path.write_text(json.dumps({"deai": deai}, ensure_ascii=False, indent=2), encoding="utf-8")
    force = _step_force_read(work_dir, chapter_number, "deslop")
    if force and force.exists():
        force.unlink()
    return {
        "ok": True,
        "step": "deslop",
        "content": revised,
        "word_count": count_chinese_chars(revised),
        "deai": deai,
        "message": "已按去 AI 建议修改完成，并重新完成本地质量门评估",
        "source_before": source,
    }
