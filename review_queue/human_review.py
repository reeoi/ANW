"""FastAPI local management dashboard for ANP."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config_loader import LoadedConfig, load_from_environment
from publisher.base_publisher import PublishStatus
from review_queue.ai_review import review_story_in_database, run_review_batch
from review_queue.dashboard_assets import (
    DASHBOARD_BODY_TEMPLATE,
    DASHBOARD_CSS,
    DASHBOARD_JS,
)
from review_queue.metrics import query_overview, record_pipeline_event
from review_queue.settings_api import mode_router, router as settings_router
from review_queue.control_api import router as control_router, scheduler_manager
from review_queue.db import (
    get_database_path,
    get_story,
    initialize_database,
    list_reviewable_stories,
    story_from_row,
    update_story_metadata,
    update_story_status,
)
from review_queue.models import Story
from scheduler import configure_logging, recent_log_lines

logger = logging.getLogger(__name__)

_GENERATE_DISABLED_MESSAGE = (
    "生成入口已停用:c_pipeline 重构 Phase A 已移除旧的单步生成路径。"
    "Phase C 上线后会通过 generator.c_pipeline.orchestrator 触发多阶段流水线;"
    "Phase E 集成接线后此 API 将改为按 daily_publish_plan + theme_pool 启动。"
)

app = FastAPI(title="ANP Local Studio")
app.include_router(settings_router)
app.include_router(mode_router)
app.include_router(control_router)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str | None = None) -> HTMLResponse:
    """Render the local ANP management studio."""
    return HTMLResponse(_render_dashboard(message=message))


@app.get("/favicon.ico")
def favicon() -> "Response":
    """Return an empty favicon to silence browser 404s during local use."""
    from fastapi import Response

    return Response(status_code=204)


@app.get("/api/dashboard")
def api_dashboard() -> dict[str, Any]:
    config = _load_config()
    db_path = _database_path(config)
    stats = _queue_stats(db_path)
    recent = _list_stories(db_path, limit=12)
    latest = recent[0] if recent else None
    return {
        "ok": True,
        "stats": stats,
        **stats,
        "recent": [_story_payload(story, preview=180) for story in recent],
        "latest": _story_payload(latest, preview=120) if latest else None,
        "dry_run": bool(config.is_dry_run),
        "database": str(db_path),
        "warnings": config.warnings,
    }


@app.get("/api/stories")
def api_stories(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    db_path = _database_path()
    stories = _list_stories(db_path, status=status, limit=max(1, min(limit, 200)))
    return {"ok": True, "stories": [_story_payload(story, preview=260) for story in stories]}


@app.get("/api/stories/{story_id}")
def api_story_detail(story_id: int) -> dict[str, Any]:
    story = _ensure_story_exists(story_id)
    payload = _story_payload(story, preview=None)
    if payload is not None:
        payload["review_detail"] = _parse_summary(story.summary)
    return {"ok": True, "story": payload}


@app.delete("/api/stories/{story_id}")
def api_delete_story(story_id: int) -> dict[str, Any]:
    """Delete one story from the local queue.

    Useful for cleaning up old failed/published records from the management UI.
    """

    _ensure_story_exists(story_id)
    db_path = _database_path()
    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute("DELETE FROM stories WHERE id = ?", (story_id,))
        deleted = cursor.rowcount
    if deleted <= 0:
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Dashboard delete story_id=%s", story_id)
    return {"ok": True, "message": f"已删除 ID #{story_id} 的作品。", "deleted": deleted}


@app.post("/api/generate")
async def api_generate(request: Request) -> dict[str, Any]:
    """[Stub] Old single-shot generate endpoint, disabled during c_pipeline refactor."""

    return JSONResponse(
        {"ok": False, "message": _GENERATE_DISABLED_MESSAGE, "stub": True},
        status_code=503,
    )


@app.post("/api/batch-generate")
async def api_batch_generate(request: Request) -> dict[str, Any]:
    """[Stub] Old batch generate endpoint, disabled during c_pipeline refactor."""

    return JSONResponse(
        {"ok": False, "message": _GENERATE_DISABLED_MESSAGE, "stub": True},
        status_code=503,
    )


@app.post("/api/review/{story_id}/approve")
def api_approve_story(story_id: int) -> dict[str, Any]:
    _ensure_story_exists(story_id)
    db_path = _database_path()
    if not update_story_status(db_path, story_id, "approved", summary="人工批准。"):
        raise HTTPException(status_code=404, detail="Story not found")
    record_pipeline_event(db_path, kind="review", status="approved", story_id=story_id, message="manual")
    logger.info("Dashboard review action: approved story_id=%s", story_id)
    return {"ok": True, "message": "已批准，进入待发布队列。"}


@app.post("/api/review/{story_id}/reject")
async def api_reject_story(story_id: int, request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    notes = _clean_optional(str(payload.get("review_notes") or "")) or "人工拒绝。"
    _ensure_story_exists(story_id)
    db_path = _database_path()
    if not update_story_status(db_path, story_id, "rejected", summary=notes):
        raise HTTPException(status_code=404, detail="Story not found")
    record_pipeline_event(db_path, kind="review", status="rejected", story_id=story_id, message=notes[:200])
    logger.info("Dashboard review action: rejected story_id=%s", story_id)
    return {"ok": True, "message": "已拒绝作品。"}


@app.post("/api/review/{story_id}/save")
async def api_save_story(story_id: int, request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    title = _validate_required_text(str(payload.get("title") or ""), "标题", 200)
    summary = _clean_optional(str(payload.get("summary") or ""), 5_000)
    _ensure_story_exists(story_id)
    if not update_story_metadata(_database_path(), story_id, title=title, summary=summary):
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Dashboard review action: saved story_id=%s", story_id)
    return {"ok": True, "message": "已保存编辑。"}


@app.post("/api/review/{story_id}/ai")
def api_ai_review_one(story_id: int) -> dict[str, Any]:
    _ensure_story_exists(story_id)
    summary = review_story_in_database(_database_path(), story_id, config=_load_config())
    logger.info("Dashboard review action: ai_one story_id=%s decision=%s", story_id, summary.decision)
    return {
        "ok": summary.decision in {"approved", "needs_human"},
        "message": f"AI 审核完成：{summary.decision}，分数 {summary.final_score}，重写 {summary.attempts} 次。",
        "summary": summary.__dict__,
    }


@app.post("/api/review/batch")
def api_ai_review_batch(limit: int = 20) -> dict[str, Any]:
    result = run_review_batch(_database_path(), limit=max(1, min(limit, 100)), config=_load_config())
    logger.info("Dashboard review action: ai_batch reviewed=%s approved=%s needs_human=%s", result.reviewed, result.approved, result.needs_human)
    return {"ok": result.failed == 0, "message": result.message, "result": result.__dict__}


@app.post("/api/publish")
def api_publish(dry_run_outcome: str = "success", commit_dry_run: bool = False) -> dict[str, Any]:
    from cli.publish import apply_publish_result, find_one_approved_story
    from publisher.fansq import FansqPublisher

    config = _load_config()
    db_path = initialize_database(config)
    story = find_one_approved_story(db_path)
    if story is None:
        return {"ok": True, "message": "没有 approved 待发布作品。", "result": None}
    publisher = FansqPublisher(config)
    result = publisher.publish_story(
        story,
        dry_run=bool(config.data.get("runtime", {}).get("dry_run")),
        dry_run_outcome=dry_run_outcome,
        wait_on_pause=False,
    )
    changed = apply_publish_result(db_path, result, commit_dry_run=commit_dry_run)
    ok = result.status in {str(PublishStatus.PUBLISHED), str(PublishStatus.PAUSED), PublishStatus.PUBLISHED, PublishStatus.PAUSED}
    record_pipeline_event(
        db_path,
        kind="publish",
        status=str(result.status),
        story_id=story.id,
        message=str(result.message)[:200],
        detail=("dry_run=" + dry_run_outcome) if config.is_dry_run else "live",
    )
    logger.info("Dashboard publish story_id=%s status=%s changed=%s", story.id, result.status, changed)
    return {
        "ok": bool(ok),
        "message": result.message,
        "changed": changed,
        "result": result.__dict__,
        "safe_pause_notice": "遇到验证码/滑块/登录态缺失只会暂停并截图，不会绕过。",
    }


@app.get("/api/logs")
def api_logs(max_lines: int = 120) -> dict[str, Any]:
    config = _load_config()
    log_file, lines = recent_log_lines(config, max_lines=max(10, min(max_lines, 500)))
    return {"ok": True, "log_file": str(log_file), "lines": lines}


@app.get("/api/monitor")
def api_monitor() -> dict[str, Any]:
    """Return aggregated metrics for the monitoring dashboard."""
    config = _load_config()
    db_path = _database_path(config)
    overview = query_overview(db_path)
    cost_limits = config.data.get("cost_limits") or {}
    monthly_budget = float(cost_limits.get("monthly_budget_cny") or 0)
    daily_token_limit = int(cost_limits.get("daily_token_limit") or 0)
    spent_30d = overview["usage"]["d30"]["cost_cny"]
    tokens_24h = overview["usage"]["d1"]["total_tokens"]
    health = _system_health(config, db_path)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dry_run": bool(config.is_dry_run),
        "model": (config.data.get("deepseek") or {}).get("model"),
        "schedule": _schedule_info(config),
        "limits": {
            "monthly_budget_cny": monthly_budget,
            "daily_token_limit": daily_token_limit,
            "spent_30d_cny": spent_30d,
            "tokens_24h": tokens_24h,
            "monthly_budget_used_pct": _pct(spent_30d, monthly_budget),
            "daily_token_used_pct": _pct(tokens_24h, daily_token_limit),
        },
        "health": health,
        **overview,
    }


@app.get("/api/monitor/cards")
def api_monitor_cards() -> dict[str, Any]:
    """4 张状态卡片 (Phase 3 总览 Dashboard 顶部)."""
    from review_queue.monitor_aggregator import monitor_cards

    config = _load_config()
    db_path = _database_path(config)
    return {"ok": True, **monitor_cards(config, db_path, scheduler_manager)}


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    """Lightweight liveness probe for external monitoring."""
    config = _load_config()
    db_path = _database_path(config)
    scheduler_running = scheduler_manager.is_running()
    if scheduler_running:
        status = "ok"
    elif config.warnings:
        status = "degraded"
    else:
        status = "ok"
    return {
        "ok": True,
        "status": status,
        "scheduler_running": scheduler_running,
        "dry_run": bool(config.is_dry_run),
        "database": str(db_path),
        "warnings": list(config.warnings or []),
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# Backward-compatible form endpoints used by existing tests and old links.
@app.post("/stories/{story_id}/approve")
def approve_story(story_id: int) -> RedirectResponse:
    _ensure_story_exists(story_id)
    if not update_story_status(_database_path(), story_id, "approved", summary="人工批准。"):
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Human review action: approved story_id=%s", story_id)
    return _redirect("已批准作品。")


@app.post("/stories/{story_id}/reject")
def reject_story(story_id: int, review_notes: Annotated[str, Form()] = "人工拒绝。") -> RedirectResponse:
    notes = _clean_optional(review_notes) or "人工拒绝。"
    _ensure_story_exists(story_id)
    if not update_story_status(_database_path(), story_id, "rejected", summary=notes):
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Human review action: rejected story_id=%s", story_id)
    return _redirect("已拒绝作品。")


@app.post("/stories/{story_id}/edit")
def edit_story(
    story_id: int,
    title: Annotated[str, Form()],
    summary: Annotated[str, Form()] = "",
) -> RedirectResponse:
    clean_title = _validate_required_text(title, "标题", max_length=200)
    clean_summary = _clean_optional(summary, max_length=5_000)
    _ensure_story_exists(story_id)
    if not update_story_metadata(_database_path(), story_id, title=clean_title, summary=clean_summary):
        raise HTTPException(status_code=404, detail="Story not found")
    logger.info("Human review action: edited story_id=%s", story_id)
    return _redirect("已保存编辑。")


@app.post("/ai-review/run", response_class=HTMLResponse)
def run_ai_review() -> HTMLResponse:
    result = run_review_batch(_database_path())
    logger.info("Human review action: ai_review_batch reviewed=%s approved=%s needs_human=%s", result.reviewed, result.approved, result.needs_human)
    return HTMLResponse(_render_dashboard(message=result.message))


def _load_config() -> LoadedConfig:
    config = load_from_environment()
    configure_logging(config)
    return config


def _database_path(config: LoadedConfig | None = None) -> Path:
    config = config or _load_config()
    return initialize_database(config) or get_database_path(config)


def _ensure_story_exists(story_id: int) -> Story:
    story = get_story(_database_path(), story_id)
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


def _redirect(message: str) -> RedirectResponse:
    return RedirectResponse(url=f"/?message={html.escape(message, quote=True)}", status_code=303)


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    return payload


def _validate_required_text(value: str, label: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if len(cleaned) > max_length:
        raise HTTPException(status_code=400, detail=f"{label}长度不能超过 {max_length} 字符")
    return cleaned


def _positive_int(value: Any, label: str, max_value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{label}必须是整数") from None
    if number <= 0 or number > max_value:
        raise HTTPException(status_code=400, detail=f"{label}必须在 1 到 {max_value} 之间")
    return number


def _clean_optional(value: str | None, max_length: int = 2_000) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise HTTPException(status_code=400, detail=f"备注长度不能超过 {max_length} 字符")
    return cleaned


def _queue_stats(db_path: str | Path) -> dict[str, int]:
    statuses = ["pending", "needs_human", "approved", "published", "rejected", "publish_paused", "publish_failed", "failed"]
    stats = {status: 0 for status in statuses}
    with sqlite3.connect(Path(db_path)) as connection:
        total = connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        rows = connection.execute("SELECT status, COUNT(*) FROM stories GROUP BY status").fetchall()
    for status, count in rows:
        stats[str(status)] = int(count)
    stats["total"] = int(total)
    stats["failed"] = int(stats.get("failed", 0) + stats.get("publish_failed", 0))
    return stats


def _list_stories(db_path: str | Path, status: str | None = None, limit: int = 50) -> list[Story]:
    sql = """
        SELECT id, title, status, pipeline_version, work_dir, current_phase,
               final_content_path, pipeline_cost_cny, target_length,
               emotion, genre, hint_title, summary,
               ai_review_score, ai_review_attempts, content,
               created_at, updated_at
        FROM stories
    """
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(Path(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(sql, params).fetchall()
    return [story_from_row(row) for row in rows]


def _story_payload(story: Story | None, preview: int | None = 180) -> dict[str, Any] | None:
    if story is None:
        return None
    summary = story.summary or ""
    return {
        "id": story.id,
        "title": story.title,
        "status": story.status,
        "pipeline_version": story.pipeline_version,
        "current_phase": story.current_phase,
        "work_dir": story.work_dir,
        "final_content_path": story.final_content_path,
        "pipeline_cost_cny": float(story.pipeline_cost_cny or 0.0),
        "ai_review_score": story.ai_review_score,
        "ai_review_attempts": int(story.ai_review_attempts or 0),
        "target_length": story.target_length,
        "emotion": story.emotion,
        "genre": story.genre,
        "hint_title": story.hint_title,
        "summary": summary if preview is None else (summary[:preview] + ("…" if len(summary) > preview else "")),
        "created_at": story.created_at,
        "updated_at": story.updated_at,
    }


def _parse_summary(summary: str | None) -> dict[str, Any]:
    """Extract structured AI review info from a stored summary if present.

    The AI review pipeline stores ``ReviewResult.to_json()`` after a tag like
    "AI 审核通过：" or "AI 审核未通过，转人工复查："; older / manual summaries may
    simply contain plain text. Returns a best-effort breakdown so the UI can
    show issues / suggestions / dimension scores without losing the raw text.
    """

    if not summary:
        return {"raw": "", "issues": [], "suggestions": [], "dimension_scores": {}, "summary": ""}

    raw = str(summary)
    head = raw.strip()
    issues: list[str] = []
    suggestions: list[str] = []
    dimension_scores: dict[str, int] = {}
    decision: str | None = None
    total_score: int | None = None

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start : end + 1]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            issues = [str(item) for item in data.get("issues", []) if str(item).strip()]
            suggestions = [str(item) for item in data.get("suggestions", []) if str(item).strip()]
            scores = data.get("dimension_scores")
            if isinstance(scores, dict):
                for name, value in scores.items():
                    try:
                        dimension_scores[str(name)] = int(value)
                    except (TypeError, ValueError):
                        continue
            decision = str(data.get("decision") or "") or None
            total = data.get("total_score")
            if isinstance(total, (int, float)):
                total_score = int(total)
            head = raw[:start].strip() or head

    return {
        "raw": raw,
        "summary": head,
        "issues": issues,
        "suggestions": suggestions,
        "dimension_scores": dimension_scores,
        "decision": decision,
        "total_score": total_score,
    }


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _pct(used: float, limit: float) -> float:
    """Return ``used/limit`` as a percentage rounded to two decimals."""
    if not limit or limit <= 0:
        return 0.0
    return round((used / limit) * 100, 2)


def _schedule_info(config: LoadedConfig) -> dict[str, Any]:
    scheduler_cfg = config.data.get("scheduler") or {}
    return {
        "enabled": bool(scheduler_cfg.get("enabled")),
        "timezone": str(scheduler_cfg.get("timezone") or "Asia/Shanghai"),
        "generate_cron": str(scheduler_cfg.get("generate_cron") or ""),
        "review_cron": str(scheduler_cfg.get("review_cron") or ""),
        "publish_cron": str(scheduler_cfg.get("publish_cron") or ""),
        "backup_cron": str(scheduler_cfg.get("backup_cron") or ""),
    }


def _system_health(config: LoadedConfig, db_path: Path) -> dict[str, Any]:
    db_size = 0
    try:
        if Path(db_path).exists():
            db_size = Path(db_path).stat().st_size
    except OSError:
        db_size = 0
    log_path = Path(str((config.data.get("logging") or {}).get("file") or "logs/anp.log"))
    log_size = 0
    try:
        if log_path.exists():
            log_size = log_path.stat().st_size
    except OSError:
        log_size = 0
    backup_dir = Path(str((config.data.get("database") or {}).get("backup_dir") or "data/backups"))
    backup_count = 0
    last_backup_at: str | None = None
    if backup_dir.exists():
        try:
            backups = sorted(backup_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
            backup_count = len(backups)
            if backups:
                last_backup_at = datetime.fromtimestamp(
                    backups[0].stat().st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            pass
    return {
        "db_path": str(db_path),
        "db_size_bytes": int(db_size),
        "log_path": str(log_path),
        "log_size_bytes": int(log_size),
        "backup_dir": str(backup_dir),
        "backup_count": int(backup_count),
        "last_backup_at": last_backup_at,
    }


def _render_dashboard(message: str | None = None) -> str:
    config = _load_config()
    db_path = _database_path(config)
    stories = list_reviewable_stories(db_path)
    legacy_story_cards = "".join(_render_legacy_story_card(story) for story in stories)
    if not legacy_story_cards:
        legacy_story_cards = '<div class="empty">当前没有 pending / needs_human 待审核作品。</div>'
    safe_message = _e(message or "")
    banner_display = "block" if safe_message else "none"
    db_text = _e(db_path)
    body = (
        DASHBOARD_BODY_TEMPLATE
        .replace("__BANNER_DISPLAY__", banner_display)
        .replace("__BANNER_MESSAGE__", safe_message)
        .replace("__LEGACY_STORY_CARDS__", legacy_story_cards)
        .replace("__DB_PATH__", db_text)
    )
    return (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "  <title>ANP Local Studio</title>\n"
        f"  <style>{DASHBOARD_CSS}</style>\n"
        "</head>\n"
        f"<body>{body}\n"
        f"<script>{DASHBOARD_JS}</script>\n"
        "</body></html>\n"
    )


def _render_legacy_story_card(story: Story) -> str:
    """Hidden HTML fallback for no-JS users and backward-compatible tests."""
    story_id = story.id if story.id is not None else 0
    score_text = story.ai_review_score if story.ai_review_score is not None else "未评分"
    summary_text = story.summary or "无"
    return f"""<article class=\"legacy-story\">
  <h2>{_e(story.title)}</h2>
  <div>ID: {_e(story.id)} | 状态: {_e(story.status)} | Phase: {_e(story.current_phase)} | 分数: {_e(score_text)} | 重写: {_e(story.ai_review_attempts)}</div>
  <p>{_e(summary_text)}</p>
  <form method=\"post\" action=\"/stories/{story_id}/edit\">
    <input type=\"text\" name=\"title\" value=\"{_e(story.title)}\">
    <textarea name=\"summary\">{_e(story.summary or '')}</textarea>
    <button type=\"submit\">保存编辑</button>
  </form>
  <form method=\"post\" action=\"/stories/{story_id}/approve\"><button type=\"submit\">approve / 批准</button></form>
  <form method=\"post\" action=\"/stories/{story_id}/reject\"><input type=\"hidden\" name=\"review_notes\" value=\"人工拒绝。\"><button type=\"submit\">reject / 拒绝</button></form>
</article>"""


def _parse_server_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local ANP management FastAPI app.")
    parser.add_argument("--host", default=os.getenv("ANP_REVIEW_HOST", "127.0.0.1"), help="Bind host for the local server.")
    parser.add_argument("--port", type=int, default=_server_port_from_env(), help="Bind port for the local server.")
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
    uvicorn.run("review_queue.human_review:app", host=args.host, port=args.port, reload=False)
