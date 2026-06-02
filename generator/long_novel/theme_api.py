"""Standalone theme pool API — major feature under sidebar."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from config_loader import load_from_environment
from generator.long_novel.theme_db import (
    count_themes,
    get_theme,
    get_theme_stats,
    list_themes,
    mark_consumed,
    run_import_all,
)
from generator.long_novel.theme_manager import (
    get_category_trend_analysis,
    get_fanqie_trending_keywords,
    get_trending_emotions,
    get_trending_genres,
    import_fanqie_trends,
    suggest_books,
    suggest_hot_opening,
)
from review_queue.db import initialize_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/themes", tags=["themes"])


def _db_path() -> Path:
    config = load_from_environment()
    return initialize_database(config) or Path("data/anp.sqlite3")


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        return await request.json() or {}
    except Exception:
        return {}


# ── List & Filter ─────────────────────────────────────────────────────


@router.get("")
def api_list_themes(
    type: str | None = Query(None, alias="type"),
    genre: str | None = None,
    source: str | None = None,
    consumed: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    items = list_themes(_db_path(), target_type=type, genre=genre,
                        source=source, consumed=consumed, limit=limit, offset=offset)
    total = count_themes(_db_path(), is_consumed=(1 if consumed else 0) if consumed is not None else None)
    return {"ok": True, "themes": items, "count": len(items), "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
def api_theme_stats() -> dict[str, Any]:
    stats = get_theme_stats(_db_path())
    return {"ok": True, "stats": stats}


@router.get("/{theme_id}")
def api_get_theme(theme_id: int) -> dict[str, Any]:
    t = get_theme(_db_path(), theme_id)
    if not t:
        raise HTTPException(status_code=404, detail="题材不存在")
    return {"ok": True, "theme": t}


@router.post("/{theme_id}/consume")
def api_consume_theme(theme_id: int) -> dict[str, Any]:
    t = get_theme(_db_path(), theme_id)
    if not t:
        raise HTTPException(status_code=404, detail="题材不存在")
    mark_consumed(_db_path(), theme_id)
    return {"ok": True, "message": "已标记为已消费"}


# ── Import ────────────────────────────────────────────────────────────


@router.post("/import-all")
def api_import_all() -> dict[str, Any]:
    result = run_import_all(_db_path())
    return {"ok": True, "result": result, "message": f"导入完成：共{result['total']}条题材"}


@router.post("/import-fanqie")
async def api_import_fanqie(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    date_str = str(payload.get("date") or "").strip() or None
    fetch_result = import_fanqie_trends(date_str=date_str)
    if not fetch_result["ok"]:
        return {"ok": False, "error": fetch_result.get("error", "拉取失败")}
    from generator.long_novel.theme_db import import_from_fanqie_cache
    count = import_from_fanqie_cache(_db_path())
    return {"ok": True, "imported": count, "source": fetch_result["source"],
            "date": fetch_result["date"], "books": fetch_result.get("books", 0)}


# ── Stats & Trending ──────────────────────────────────────────────────


@router.get("/trending/genres")
def api_trending_genres() -> dict[str, Any]:
    genres = get_trending_genres(12)
    return {"ok": True, "genres": genres}


@router.get("/trending/emotions")
def api_trending_emotions() -> dict[str, Any]:
    emotions = get_trending_emotions(8)
    return {"ok": True, "emotions": emotions}


@router.get("/trending/fanqie-keywords")
def api_fanqie_keywords() -> dict[str, Any]:
    keywords = get_fanqie_trending_keywords(20)
    return {"ok": True, "keywords": keywords}


@router.get("/trending/sources")
def api_source_info() -> dict[str, Any]:
    stats = get_theme_stats(_db_path())
    source_counts = {
        str(item.get("source")): int(item.get("count") or 0)
        for item in stats.get("sources", [])
    }
    sources = [
            {
                "id": "fanqie",
                "name": "FanqieRankTracker",
                "icon": "",
                "desc": "每日拉取番茄小说女频新书榜（360+本/天，18分类）→ AI提取题材关键信息",
                "frequency": "每日更新",
                "format": "title + author + reads + intro → AI标准化为统一格式",
                "url": "https://raw.githubusercontent.com/reeoi/FanqieRankTracker/main/data/fanqie_female_new_ranks_YYYYMMDD.json",
            },
    ]
    active_sources = []
    for src in sources:
        count = source_counts.get(src["id"], 0)
        if count <= 0:
            continue
        item = dict(src)
        item["count"] = count
        active_sources.append(item)
    return {"ok": True, "sources": active_sources}


# ── AI Suggest ────────────────────────────────────────────────────────


@router.post("/suggest")
async def api_suggest_books(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    target_type = str(payload.get("type") or "long")
    count = int(payload.get("count") or 5)

    from generator.api_client import DeepSeekClient
    config = load_from_environment()
    client = DeepSeekClient(config)

    suggestions = suggest_books(client, target_type=target_type, count=count)
    return {"ok": True, "suggestions": suggestions, "count": len(suggestions)}


# ── Category Trend Analysis ──────────────────────────────────────────


@router.get("/trending/analysis")
def api_category_trend_analysis() -> dict[str, Any]:
    """Per-category trend analysis: hotness ranking, keywords, top titles."""
    categories = get_category_trend_analysis()
    return {
        "ok": True,
        "categories": categories,
        "hottest": categories[0] if categories else None,
        "total_categories": len(categories),
    }


@router.post("/trending/hot-opening")
async def api_hot_opening(request: Request) -> dict[str, Any]:
    """Generate a single book opening suggestion based on the hottest category."""
    payload = await _json_payload(request)
    target_type = str(payload.get("type") or "long")

    from generator.api_client import DeepSeekClient
    config = load_from_environment()
    client = DeepSeekClient(config)

    result = suggest_hot_opening(client, target_type=target_type)
    return {"ok": True, "suggestion": result}


__all__ = ["router"]
