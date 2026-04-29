"""FastAPI human-review application for local queued stories."""

from __future__ import annotations

import argparse
import html
import logging
import os
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config_loader import load_from_environment
from queue.ai_review import run_review_batch
from queue.db import (
    get_database_path,
    get_story,
    initialize_database,
    list_reviewable_stories,
    update_story_content,
    update_story_status,
)
from queue.models import Story

logger = logging.getLogger(__name__)

app = FastAPI(title="ANP Human Review")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str | None = None) -> HTMLResponse:
    """Render the local human-review queue landing page."""
    db_path = _database_path()
    stories = list_reviewable_stories(db_path)
    body = _render_index(stories, message=message)
    return HTMLResponse(body)


@app.post("/stories/{story_id}/approve")
def approve_story(story_id: int) -> RedirectResponse:
    """Mark a story as approved for the publishing stage."""
    _ensure_story_exists(story_id)
    if not update_story_status(_database_path(), story_id, "approved", "人工批准。"):
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Human review action: approved story_id=%s", story_id)
    return _redirect("已批准作品。")


@app.post("/stories/{story_id}/reject")
def reject_story(
    story_id: int,
    review_notes: Annotated[str, Form()] = "人工拒绝。",
) -> RedirectResponse:
    """Mark a story as rejected."""
    notes = _clean_optional(review_notes) or "人工拒绝。"
    _ensure_story_exists(story_id)
    if not update_story_status(_database_path(), story_id, "rejected", notes):
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Human review action: rejected story_id=%s", story_id)
    return _redirect("已拒绝作品。")


@app.post("/stories/{story_id}/edit")
def edit_story(
    story_id: int,
    title: Annotated[str, Form()],
    content: Annotated[str, Form()],
    review_notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Save human edits to a queued story."""
    clean_title = _validate_required_text(title, "标题", max_length=200)
    clean_content = _validate_required_text(content, "内容", max_length=100_000)
    clean_notes = _clean_optional(review_notes, max_length=2_000)
    _ensure_story_exists(story_id)
    if not update_story_content(_database_path(), story_id, clean_title, clean_content, clean_notes):
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Human review action: edited story_id=%s", story_id)
    return _redirect("已保存编辑。")


@app.post("/ai-review/run", response_class=HTMLResponse)
def run_ai_review() -> HTMLResponse:
    """Run the AI review batch seam from the review page."""
    result = run_review_batch(_database_path())
    logger.info(
        "Human review action: ai_review_batch reviewed=%s approved=%s needs_human=%s",
        result.reviewed,
        result.approved,
        result.needs_human,
    )
    return HTMLResponse(_render_index(list_reviewable_stories(_database_path()), message=result.message))


def _database_path():
    config = load_from_environment()
    return initialize_database(config) or get_database_path(config)


def _ensure_story_exists(story_id: int) -> Story:
    story = get_story(_database_path(), story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


def _redirect(message: str) -> RedirectResponse:
    return RedirectResponse(url=f"/?message={html.escape(message, quote=True)}", status_code=303)


def _validate_required_text(value: str, label: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if len(cleaned) > max_length:
        raise HTTPException(status_code=400, detail=f"{label}长度不能超过 {max_length} 字符")
    return cleaned


def _clean_optional(value: str | None, max_length: int = 2_000) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise HTTPException(status_code=400, detail=f"备注长度不能超过 {max_length} 字符")
    return cleaned


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _render_index(stories: list[Story], message: str | None = None) -> str:
    story_cards = "".join(_render_story_card(story) for story in stories)
    if not story_cards:
        story_cards = '<section class="empty">当前没有 pending / needs_human 待审核作品。</section>'
    safe_message = f'<div class="message">{_e(message)}</div>' if message else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ANP 人工审核队列</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 2rem; background: #f7f7fb; color: #222; }}
    header, .story, .empty, .message {{ background: white; border: 1px solid #ddd; border-radius: 12px; padding: 1rem; margin-bottom: 1rem; }}
    .meta {{ color: #555; display: flex; flex-wrap: wrap; gap: .75rem; margin: .5rem 0; }}
    textarea, input[type=text] {{ box-sizing: border-box; width: 100%; margin: .25rem 0 .75rem; padding: .5rem; }}
    textarea {{ min-height: 220px; }}
    button {{ margin-right: .5rem; padding: .45rem .8rem; border: 0; border-radius: 8px; cursor: pointer; }}
    .approve {{ background: #16803c; color: white; }}
    .reject {{ background: #b42318; color: white; }}
    .save, .ai {{ background: #2454d6; color: white; }}
    .message {{ border-color: #8fb5ff; background: #eef5ff; }}
    .empty {{ color: #666; }}
  </style>
</head>
<body>
  <header>
    <h1>ANP 人工审核队列</h1>
    <p>仅显示 <code>pending</code> 与 <code>needs_human</code> 作品。页面不展示 API key、账号、密码或登录态路径。</p>
    <form method="post" action="/ai-review/run">
      <button class="ai" type="submit">运行 AI 审核批次</button>
    </form>
  </header>
  {safe_message}
  <main>{story_cards}</main>
</body>
</html>"""


def _render_story_card(story: Story) -> str:
    story_id = story.id if story.id is not None else 0
    return f"""<article class="story">
  <h2>{_e(story.title)}</h2>
  <div class="meta">
    <span>ID: {_e(story.id)}</span>
    <span>状态: {_e(story.status)}</span>
    <span>分数: {_e(story.score if story.score is not None else "未评分")}</span>
    <span>重试次数: {_e(story.retry_count)}</span>
  </div>
  <p><strong>审核备注：</strong>{_e(story.review_notes or "无")}</p>
  <form method="post" action="/stories/{story_id}/edit">
    <label>标题
      <input type="text" name="title" maxlength="200" required value="{_e(story.title)}">
    </label>
    <label>内容
      <textarea name="content" maxlength="100000" required>{_e(story.content)}</textarea>
    </label>
    <label>审核备注
      <input type="text" name="review_notes" maxlength="2000" value="{_e(story.review_notes or "")}">
    </label>
    <button class="save" type="submit">保存编辑</button>
  </form>
  <form method="post" action="/stories/{story_id}/approve" style="display:inline">
    <button class="approve" type="submit">approve / 批准</button>
  </form>
  <form method="post" action="/stories/{story_id}/reject" style="display:inline">
    <input type="hidden" name="review_notes" value="人工拒绝。">
    <button class="reject" type="submit">reject / 拒绝</button>
  </form>
</article>"""


def _parse_server_args() -> argparse.Namespace:
    """Parse local server host/port without exposing application config values."""
    parser = argparse.ArgumentParser(description="Start the local ANP human-review FastAPI app.")
    parser.add_argument(
        "--host",
        default=os.getenv("ANP_REVIEW_HOST", "127.0.0.1"),
        help="Bind host for the local review server (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_server_port_from_env(),
        help="Bind port for the local review server (default: 8000; override with ANP_REVIEW_PORT).",
    )
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    return args


def _server_port_from_env() -> int:
    raw_port = os.getenv("ANP_REVIEW_PORT", "8000")
    try:
        return int(raw_port)
    except ValueError:
        logger.warning("Invalid ANP_REVIEW_PORT value; falling back to 8000")
        return 8000


if __name__ == "__main__":
    import uvicorn

    args = _parse_server_args()
    uvicorn.run("queue.human_review:app", host=args.host, port=args.port, reload=False)
