"""书库 / 卷 / 章节 / 主题趋势 / 产物文件浏览的 REST 路由。

由 ``generator.long_novel.api`` 聚合进主 router（prefix=/api/long-novel）。
LLM 客户端一律经 ``deps._deepseek_client(...)`` 模块属性调用，保持单一
monkeypatch 缝。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from generator.long_novel import deps
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
    upsert_volume,
)
from generator.long_novel.deps import _db_path, _json_payload, _project_root
from generator.long_novel.step_artifacts import _draft_context_manifest
from generator.long_novel.theme_manager import (
    get_fanqie_dates,
    get_fanqie_trending_keywords,
    get_hot_themes,
    get_trending_emotions,
    get_trending_genres,
    import_fanqie_trends,
    suggest_books,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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
    db = _db_path()
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    volumes = list_volumes(db, book_id)
    chapters = list_chapters(db, book_id)
    book["volumes"] = volumes
    book["chapters"] = chapters
    book["total_words"] = sum(c.get("actual_words", 0) for c in chapters)
    done_statuses = {"published", "draft", "final", "finalized", "done"}
    book["completed_chapters"] = sum(
        1
        for c in chapters
        if c.get("status") in done_statuses
        or bool(c.get("draft_path"))
        or int(c.get("actual_words") or 0) > 0
    )
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
    import shutil
    work_dir = Path(book.get("work_dir") or "")
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
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

    from generator.long_novel.l2_chapter_write import assemble_context, ensure_tracking_files

    ensure_tracking_files(work_dir, int(book.get("target_chapters") or 0))
    context.update(
        assemble_context(
            work_dir,
            chapter_number,
            str(ch.get("title") or ""),
            int(ch.get("target_words") or book.get("target_words_per_chapter") or 3000),
        )
    )
    context["llm_context"] = _draft_context_manifest(context)
    return {"ok": True, "context": context}


@router.post("/books/{book_id}/tracking/ensure")
def api_ensure_tracking_files(book_id: int) -> dict[str, Any]:
    """Create missing long-memory tracking files for an existing book."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    from generator.long_novel.l2_chapter_write import ensure_tracking_files

    work_dir = Path(book["work_dir"])
    ensure_tracking_files(work_dir, int(book.get("target_chapters") or 0))
    files = sorted(p.name for p in (work_dir / "追踪").glob("*.md"))
    return {"ok": True, "files": files}


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

    client = deps._deepseek_client()

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


@router.post("/books/{book_id}/artifact")
async def api_write_artifact(book_id: int, request: Request) -> dict[str, Any]:
    """Save edits to a generated markdown artifact inside the book work dir."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    rel_path = str(payload.get("path") or "").strip()
    content = str(payload.get("content") or "")
    if not rel_path or not rel_path.endswith(".md"):
        raise HTTPException(status_code=400, detail="只能保存 markdown 文件")
    work_dir = Path(book["work_dir"]).resolve()
    safe_path = (work_dir / rel_path).resolve()
    if not str(safe_path).startswith(str(work_dir)):
        raise HTTPException(status_code=403, detail="路径不允许")
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": rel_path, "size": len(content)}


@router.post("/books/{book_id}/artifact/regenerate")
async def api_regenerate_artifact(book_id: int, request: Request) -> dict[str, Any]:
    """Regenerate one artifact file with optional user instructions."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    rel_path = str(payload.get("path") or "").strip()
    additional_prompt = str(payload.get("additional_prompt") or "").strip()
    if not rel_path or not rel_path.endswith(".md"):
        raise HTTPException(status_code=400, detail="只能重新生成 markdown 文件")
    work_dir = Path(book["work_dir"]).resolve()
    safe_path = (work_dir / rel_path).resolve()
    if not str(safe_path).startswith(str(work_dir)):
        raise HTTPException(status_code=403, detail="路径不允许")

    client = deps._deepseek_client(book)
    existing = safe_path.read_text(encoding="utf-8")[:4000] if safe_path.exists() else ""

    context_parts = []
    for ctx_rel in [
        "设定/题材定位.md",
        "设定/世界观/背景设定.md",
        "设定/世界观/力量体系.md",
        "设定/角色/_角色索引.md",
        "设定/关系.md",
        "大纲/大纲.md",
        "大纲/卷纲_第一卷.md",
    ]:
        p = work_dir / ctx_rel
        if p.exists() and ctx_rel != rel_path:
            context_parts.append(f"--- {ctx_rel} ---\n{p.read_text(encoding='utf-8')[:1800]}")

    system = "你是一位小说设定与大纲编辑。请只输出目标 markdown 文件正文，不要解释。"
    user = f"""请重新生成《{book['title']}》（{book['genre']}）的文件：{rel_path}

当前文件内容参考：
{existing or '（当前文件不存在或为空）'}

上游上下文：
{chr(10).join(context_parts)}

生成要求：
- 保持与已有题材定位、世界观、角色、关系、大纲一致。
- 如果用户补充要求与上游设定冲突，优先保持设定一致，并用不冲突的方式满足。
- 只输出 markdown 正文，不要说明保存路径。
"""
    if additional_prompt:
        user += f"\n用户本次补充要求：\n{additional_prompt}\n"

    completion = client.chat_completion(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        thinking_mode=True,
    )
    text = completion.text if hasattr(completion, "text") else str(completion)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(text.strip() + "\n", encoding="utf-8")
    return {"ok": True, "path": rel_path, "content": text, "size": len(text)}


@router.get("/books/{book_id}/tree")
def api_book_tree(book_id: int) -> dict[str, Any]:
    """Return the complete file tree of a book's work directory.

    Returns a nested structure so the frontend can render a file browser
    without making N recursive API calls.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])

    def _walk(dir_path: Path, rel_root: Path | None = None) -> dict[str, Any]:
        """Walk a directory, returning a nested dict."""
        if rel_root is None:
            rel_root = dir_path
        rel = str(dir_path.relative_to(rel_root)).replace("\\", "/")
        if rel == ".":
            rel = dir_path.name

        result: dict[str, Any] = {"name": dir_path.name, "path": rel, "is_dir": True, "children": []}
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return result

        for entry in entries:
            if entry.name.startswith("_step_"):
                continue  # skip internal step temp files
            child_rel = str(entry.relative_to(rel_root)).replace("\\", "/")
            if entry.is_dir():
                child = _walk(entry, rel_root)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                child = {
                    "name": entry.name,
                    "path": child_rel,
                    "is_dir": False,
                    "size": size,
                }
            result["children"].append(child)
        return result

    if not work_dir.exists():
        return {"ok": True, "tree": {"name": book.get("title", "无标题"), "path": ".", "is_dir": True, "children": []}}

    tree = _walk(work_dir)
    return {"ok": True, "tree": tree}
