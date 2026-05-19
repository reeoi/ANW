"""Long novel REST API — book library, writing workbench, review."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config_loader import load_from_environment
from generator.long_novel.theme_manager import (
    get_fanqie_dates,
    get_fanqie_trending_keywords,
    get_hot_themes,
    get_trending_emotions,
    get_trending_genres,
    import_fanqie_trends,
    suggest_books,
)
from generator.long_novel.db import (
    create_book,
    delete_book,
    get_book,
    get_chapter,
    get_next_chapter,
    list_books,
    list_chapters,
    list_volumes,
    update_book,
    upsert_chapter,
    upsert_volume,
)
from review_queue.db import initialize_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/long-novel", tags=["long-novel"])


def _db_path() -> Path:
    config = load_from_environment()
    return initialize_database(config) or Path("data/anp.sqlite3")


def _project_root() -> Path:
    config = load_from_environment()
    return Path(str(config.data.get("runtime", {}).get("project_root") or ".")).resolve()


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        return await request.json() or {}
    except Exception:
        return {}


# ── Books ────────────────────────────────────────────────────────────


@router.get("/books")
def api_list_books() -> dict[str, Any]:
    books = list_books(_db_path())
    return {"ok": True, "books": books, "count": len(books)}


@router.post("/books")
async def api_create_book(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="书名不能为空")
    genre = str(payload.get("genre") or "").strip()
    premise = str(payload.get("premise") or "").strip()
    target_chapters = int(payload.get("target_chapters") or 30)
    target_words = int(payload.get("target_words_per_chapter") or 3000)
    root = _project_root()
    work_dir = root / "data" / "books" / title
    book_id = create_book(
        _db_path(),
        title=title,
        genre=genre,
        premise=premise,
        target_chapters=target_chapters,
        target_words_per_chapter=target_words,
        work_dir=str(work_dir),
    )
    logger.info("Created book id=%s title=%s", book_id, title)
    return {"ok": True, "book_id": book_id, "message": f"已创建书籍「{title}」"}


@router.get("/books/{book_id}")
def api_get_book(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    volumes = list_volumes(_db_path(), book_id)
    chapters = list_chapters(_db_path(), book_id)
    book["volumes"] = volumes
    book["chapters"] = chapters
    book["total_words"] = sum(c.get("actual_words", 0) for c in chapters)
    book["completed_chapters"] = sum(1 for c in chapters if c["status"] == "published")
    return {"ok": True, "book": book}


@router.put("/books/{book_id}")
async def api_update_book(book_id: int, request: Request) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    fields = {}
    for k in ("title", "genre", "premise", "target_chapters", "target_words_per_chapter"):
        if k in payload and payload[k] is not None:
            fields[k] = payload[k]
    if "status" in payload:
        fields["status"] = payload["status"]
    if fields:
        update_book(_db_path(), book_id, **fields)
    return {"ok": True, "message": "已更新"}


@router.delete("/books/{book_id}")
def api_delete_book(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    delete_book(_db_path(), book_id)
    return {"ok": True, "message": f"已删除「{book['title']}」"}


# ── Volumes ──────────────────────────────────────────────────────────


@router.get("/books/{book_id}/volumes")
def api_list_volumes(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    volumes = list_volumes(_db_path(), book_id)
    return {"ok": True, "volumes": volumes}


@router.post("/books/{book_id}/volumes")
async def api_create_volume(book_id: int, request: Request) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    vol_num = int(payload.get("volume_number") or 1)
    title = str(payload.get("title") or f"第{vol_num}卷").strip()
    chapter_count = int(payload.get("chapter_count") or 30)
    upsert_volume(_db_path(), book_id, vol_num, title=title, chapter_count=chapter_count)
    update_book(_db_path(), book_id, total_volumes=max(book["total_volumes"] or 1, vol_num))
    return {"ok": True, "message": f"已创建第{vol_num}卷「{title}」"}


# ── Chapters ─────────────────────────────────────────────────────────


@router.get("/books/{book_id}/chapters")
def api_list_chapters(book_id: int, volume: int | None = None) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    chapters = list_chapters(_db_path(), book_id, volume_number=volume)
    return {"ok": True, "chapters": chapters, "count": len(chapters)}


@router.get("/books/{book_id}/chapters/{chapter_number}")
def api_get_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    # Load draft content if available
    content = ""
    if ch.get("draft_path"):
        p = Path(ch["draft_path"])
        if p.exists():
            content = p.read_text(encoding="utf-8")
    ch["content"] = content
    return {"ok": True, "chapter": ch}


@router.get("/books/{book_id}/next-chapter")
def api_next_chapter(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_next_chapter(_db_path(), book_id)
    if not ch:
        return {"ok": True, "chapter": None, "message": "所有章节已完成"}
    return {"ok": True, "chapter": ch, "message": f"下一章：第{ch['chapter_number']}章"}


# ── Context ──────────────────────────────────────────────────────────


@router.get("/books/{book_id}/context/{chapter_number}")
def api_chapter_context(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Assemble writing context for a chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"] or "")
    context: dict[str, Any] = {
        "book_title": book["title"],
        "chapter_number": chapter_number,
        "chapter_title": ch.get("title", ""),
        "target_words": ch.get("target_words", 3000),
    }

    # Load chapter outline
    outline_path = ch.get("outline_path")
    if outline_path:
        op = Path(outline_path)
        if op.exists():
            context["outline"] = op.read_text(encoding="utf-8")

    # Load previous chapter summary
    if chapter_number > 1:
        prev_ch = get_chapter(_db_path(), book_id, chapter_number - 1)
        if prev_ch and prev_ch.get("draft_path"):
            dp = Path(prev_ch["draft_path"])
            if dp.exists():
                prev_text = dp.read_text(encoding="utf-8")
                context["prev_chapter_summary"] = prev_text[:500]
                context["prev_chapter_last_paragraph"] = prev_text[-300:]

    # Load relevant foreshadowing
    foreshadow_path = work_dir / "追踪" / "伏笔.md"
    if foreshadow_path.exists():
        foreshadow_text = foreshadow_path.read_text(encoding="utf-8")
        context["foreshadowing"] = foreshadow_text

    # Load character states
    char_state_path = work_dir / "追踪" / "角色状态.md"
    if char_state_path.exists():
        context["character_states"] = char_state_path.read_text(encoding="utf-8")

    return {"ok": True, "context": context}


# ── Theme & Suggestions ──────────────────────────────────────────────


@router.get("/themes/trending")
def api_trending_themes() -> dict[str, Any]:
    genres = get_trending_genres(8)
    emotions = get_trending_emotions(6)
    hot = get_hot_themes(6)
    fanqie_keywords = get_fanqie_trending_keywords()
    return {
        "ok": True,
        "genres": genres,
        "emotions": emotions,
        "hot_themes": [{"theme": t.get("theme", ""), "genre": t.get("genre", ""),
                         "emotion": t.get("emotion", ""), "hint_title": t.get("hint_title", "")}
                        for t in hot],
        "fanqie_keywords": fanqie_keywords,
    }


@router.post("/themes/suggest-books")
async def api_suggest_books(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    target_type = str(payload.get("type") or "long")
    count = int(payload.get("count") or 5)

    from generator.api_client import DeepSeekClient
    config = load_from_environment()
    client = DeepSeekClient(config)

    suggestions = suggest_books(client, target_type=target_type, count=count)
    return {"ok": True, "suggestions": suggestions, "count": len(suggestions)}


@router.post("/themes/refresh-fanqie")
async def api_refresh_fanqie(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    date_str = str(payload.get("date") or "").strip() or None
    result = import_fanqie_trends(date_str=date_str)
    return {"ok": result["ok"], "source": result.get("source", "?"),
            "date": result.get("date", "?"),
            "books": result.get("books", 0),
            "categories": result.get("categories", 0),
            "message": f"Fanqie trends: {result.get('books', 0)} books across {result.get('categories', 0)} categories"}


@router.get("/themes/fanqie-dates")
def api_fanqie_dates() -> dict[str, Any]:
    dates = get_fanqie_dates()
    return {"ok": True, "dates": dates, "count": len(dates)}


@router.get("/themes/fanqie-keywords")
def api_fanqie_keywords() -> dict[str, Any]:
    keywords = get_fanqie_trending_keywords(20)
    return {"ok": True, "keywords": keywords, "count": len(keywords)}


# ── Artifact Viewer ───────────────────────────────────────────────────


@router.get("/books/{book_id}/artifact")
def api_read_artifact(book_id: int, path: str = "") -> dict[str, Any]:
    """Read a generated artifact file or list directory contents."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    safe_path = (work_dir / path).resolve()
    if not str(safe_path).startswith(str(work_dir.resolve())):
        raise HTTPException(status_code=403, detail="路径不允许")
    if not safe_path.exists():
        return {"ok": True, "content": "", "message": "文件尚未生成"}
    if safe_path.is_dir():
        files = []
        for f in sorted(safe_path.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size})
        return {"ok": True, "is_dir": True, "files": files, "path": path}
    content = safe_path.read_text(encoding="utf-8")
    return {"ok": True, "content": content, "path": path, "size": len(content)}


# ── Pipeline: Book Setup (L0) - async with polling ──────────────────


@router.post("/books/{book_id}/setup-phase/{phase}")
async def api_start_setup_phase(book_id: int, phase: str, request: Request) -> dict[str, Any]:
    """Start a single L0 phase in background. Poll /setup-phase/{phase}/status for progress."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    work_dir = Path(book["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    progress_file = work_dir / f"_setup_{phase}.json"

    import json as _json_lib

    def _write(s, d=""):
        progress_file.write_text(_json_lib.dumps({
            "status": s, "detail": d,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }, ensure_ascii=False), encoding="utf-8")

    valid_phases = ["premise", "world", "characters", "outline", "finalize"]
    if phase not in valid_phases:
        raise HTTPException(status_code=400, detail=f"未知阶段：{phase}")

    payload = await _json_payload(request)
    benchmark_dir = Path(payload["benchmark_dir"]) if payload.get("benchmark_dir") else None

    def _run():
        from generator.api_client import DeepSeekClient
        from generator.long_novel.l0_book_setup import (
            run_l0_characters, run_l0_outline, run_l0_premise, run_l0_world,
        )
        config = load_from_environment()
        client = DeepSeekClient(config)
        try:
            if phase == "premise":
                _write("running", "AI正在分析题材趋势，生成题材定位文档...")
                run_l0_premise(client, work_dir, book["title"], book["genre"], book["premise"], benchmark_dir)
                fp = work_dir / "设定" / "题材定位.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "world":
                _write("running", "AI正在构建世界观背景和力量体系...")
                run_l0_world(client, work_dir, book["title"], book["genre"])
                fp = work_dir / "设定" / "世界观" / "背景设定.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "characters":
                _write("running", "AI正在设计主要角色和关系网络...")
                run_l0_characters(client, work_dir, book["title"], book["genre"])
                fp = work_dir / "设定" / "角色" / "角色设定.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "outline":
                _write("running", "AI正在生成全书大纲和30章细纲（最慢的一步，约需1-2分钟）...")
                run_l0_outline(client, work_dir, book["title"], book["genre"],
                               book["target_chapters"], book["target_words_per_chapter"])
                fp = work_dir / "大纲" / "大纲.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "finalize":
                _write("running", "正在写入数据库...")
                for ch_num in range(1, book["target_chapters"] + 1):
                    outline_path = work_dir / "大纲" / f"细纲_第{ch_num:03d}章.md"
                    upsert_chapter(
                        _db_path(), book_id, volume_number=1, chapter_number=ch_num,
                        title=f"第{ch_num}章", status="outline_only",
                        target_words=book["target_words_per_chapter"],
                        outline_path=str(outline_path) if outline_path.exists() else None,
                    )
                upsert_volume(_db_path(), book_id, 1, title="第一卷", chapter_count=book["target_chapters"], status="outlined")
                update_book(_db_path(), book_id, status="writing", total_volumes=1, current_volume=1)
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
    progress_file = Path(book["work_dir"]) / f"_setup_{phase}.json"
    if not progress_file.exists():
        return {"ok": True, "status": "pending", "detail": "尚未开始"}
    import json as _json_lib
    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    return {"ok": True, "status": data.get("status", "?"), "detail": data.get("detail", ""),
            "updated_at": data.get("updated_at", "")}


@router.get("/books/{book_id}/setup-progress")
def api_setup_progress(book_id: int) -> dict[str, Any]:
    """Poll the current L0 setup progress."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    progress_file = Path(book["work_dir"]) / "_setup_progress.json"
    if not progress_file.exists():
        return {"ok": True, "progress": {"phase": "pending", "status": "not_started", "detail": "尚未开始"}}
    import json as _json_lib
    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    return {"ok": True, "progress": data}


# ── Pipeline: Write Chapter (L2) ──────────────────────────────────────


@router.post("/books/{book_id}/write-chapter/{chapter_number}")
async def api_write_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Run the full L2 chapter writing pipeline for a single chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.api_client import DeepSeekClient
    from generator.long_novel.l2_chapter_write import run_full_chapter

    config = load_from_environment()
    client = DeepSeekClient(config)
    work_dir = Path(book["work_dir"])

    upsert_chapter(_db_path(), book_id, 1, chapter_number, status="writing")
    update_book(_db_path(), book_id, current_chapter=chapter_number)

    result = run_full_chapter(
        client, work_dir, chapter_number,
        chapter_title=ch.get("title", ""),
        target_words=ch.get("target_words", book["target_words_per_chapter"]),
    )

    upsert_chapter(
        _db_path(), book_id, 1, chapter_number,
        status="draft",
        draft_path=result["draft_path"],
        actual_words=result["final_words"],
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
    upsert_chapter(
        _db_path(), book_id, 1, chapter_number,
        review_status=review["overall"],
        ai_review_json=_json.dumps(review, ensure_ascii=False),
    )

    result["review"] = review
    return {"ok": True, "message": f"第{chapter_number}章写作完成", "result": result}


# ── Pipeline: Review Only (L4) ────────────────────────────────────────


@router.post("/books/{book_id}/review/{chapter_number}")
async def api_review_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Run the 4-dimension review on an existing chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch or not ch.get("draft_path"):
        raise HTTPException(status_code=400, detail="章节尚未生成正文")

    from generator.api_client import DeepSeekClient
    from generator.long_novel.l4_review import run_full_review

    config = load_from_environment()
    client = DeepSeekClient(config)
    work_dir = Path(book["work_dir"])

    draft_path = Path(ch["draft_path"])
    chapter_content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
    outline_path = ch.get("outline_path")
    outline_text = Path(outline_path).read_text(encoding="utf-8") if outline_path and Path(outline_path).exists() else ""

    review = run_full_review(client, chapter_content, work_dir, chapter_number, outline_text)

    import json as _json
    upsert_chapter(
        _db_path(), book_id, 1, chapter_number,
        review_status=review["overall"],
        ai_review_json=_json.dumps(review, ensure_ascii=False),
    )

    return {"ok": True, "review": review}


# ── Pipeline: Rewrite Chapter (L3) ────────────────────────────────────


@router.post("/books/{book_id}/rewrite-chapter/{chapter_number}")
async def api_rewrite_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Rewrite a chapter and check cascade continuity."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    from generator.api_client import DeepSeekClient
    from generator.long_novel.l2_chapter_write import (
        assemble_context,
        count_chinese_chars,
        run_continuity_check,
        run_deslop,
        run_polish,
        update_tracking_files,
    )

    config = load_from_environment()
    client = DeepSeekClient(config)
    work_dir = Path(book["work_dir"])

    # Backup old draft
    ch = get_chapter(_db_path(), book_id, chapter_number)
    old_draft = ""
    if ch and ch.get("draft_path"):
        old_path = Path(ch["draft_path"])
        if old_path.exists():
            old_draft = old_path.read_text(encoding="utf-8")
            backup = old_path.with_suffix(".md.bak")
            old_path.rename(backup)

    # Rewrite
    from generator.long_novel.l2_chapter_write import run_draft, run_expand
    draft = run_draft(client, work_dir, chapter_number, ch.get("title", "") if ch else "", book["target_words_per_chapter"])
    if count_chinese_chars(draft) < book["target_words_per_chapter"] * 0.9:
        draft = run_expand(client, draft, book["target_words_per_chapter"])
    polished = run_polish(client, draft)
    final = run_deslop(client, polished)

    # Save new draft
    text_dir = work_dir / "正文"
    text_dir.mkdir(parents=True, exist_ok=True)
    draft_path = text_dir / f"第{chapter_number:03d}章_{ch.get('title', '')}.md" if ch else text_dir / f"第{chapter_number:03d}章.md"
    draft_path.write_text(final, encoding="utf-8")

    # Cascade continuity check
    cascade_issues = []
    all_chapters = list_chapters(_db_path(), book_id)
    for c in all_chapters:
        cn = c["chapter_number"]
        if cn <= chapter_number:
            continue
        if not c.get("draft_path"):
            continue
        dp = Path(c["draft_path"])
        if not dp.exists():
            continue
        content = dp.read_text(encoding="utf-8")
        ck = run_continuity_check(client, work_dir, cn, content)
        if ck.get("issue_count", 0) > 0:
            cascade_issues.append({"chapter": cn, "issues": ck["issues"]})

    update_tracking_files(work_dir, chapter_number, final)

    upsert_chapter(
        _db_path(), book_id, 1, chapter_number,
        status="draft", draft_path=str(draft_path),
        actual_words=count_chinese_chars(final),
    )

    return {
        "ok": True,
        "message": f"第{chapter_number}章已重写",
        "cascade_affected": len(cascade_issues),
        "cascade_issues": cascade_issues,
    }


__all__ = ["router"]
