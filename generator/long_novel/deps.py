"""长篇 API 各模块共享的运行时依赖。

`_deepseek_client` 是测试与运行时共用的注入缝（monkeypatch 此处即可替换
LLM 客户端）——其余模块一律通过 ``deps._deepseek_client(...)`` 的模块属性
方式调用，保证补丁在所有调用方生效。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from config_loader import load_from_environment
from generator.long_novel import runtime
from generator.long_novel.db import upsert_chapter


def _db_path() -> Path:
    return runtime.db_path()


def _project_root() -> Path:
    return runtime.project_root()


def _deepseek_client(book: dict[str, Any] | None = None) -> Any:
    """Create a client and bind long-novel usage records to the current book."""
    from generator.api_client import DeepSeekClient

    client = DeepSeekClient(load_from_environment())
    if book:
        client.set_usage_context(
            work_type="long_novel",
            work_id=int(book["id"]),
            work_title=str(book.get("title") or ""),
        )
    return client


def _upsert_chapter_preserving(
    db_path: Path,
    chapter: dict[str, Any],
    **changes: Any,
) -> None:
    """Update one chapter without clearing metadata omitted by the caller."""
    values = {
        "title": str(chapter.get("title") or ""),
        "status": str(chapter.get("status") or "outline_only"),
        "target_words": int(chapter.get("target_words") or 3000),
        "actual_words": int(chapter.get("actual_words") or 0),
        "outline_path": chapter.get("outline_path"),
        "draft_path": chapter.get("draft_path"),
        "review_status": chapter.get("review_status"),
        "ai_review_json": chapter.get("ai_review_json"),
    }
    values.update(changes)
    upsert_chapter(
        db_path,
        int(chapter["book_id"]),
        int(chapter.get("volume_number") or 1),
        int(chapter["chapter_number"]),
        **values,
    )


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        return await request.json() or {}
    except Exception:
        return {}


def _chat_text(client: Any, system: str, user: str, thinking: bool = False) -> str:
    completion = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        thinking_mode=thinking,
    )
    return completion.text if hasattr(completion, "text") else str(completion)
