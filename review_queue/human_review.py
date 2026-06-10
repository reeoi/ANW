"""FastAPI local management dashboard for ANW."""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from config_loader import LoadedConfig, get_env, load_from_environment
from generator.long_novel.api import router as long_novel_router
from generator.long_novel.db import initialize_long_novel_tables
from generator.long_novel.theme_api import router as theme_router
from generator.long_novel.theme_db import initialize_theme_tables
from review_queue import dashboard_assets as _assets  # hot-reload: _assets.DASHBOARD_* reads from disk each request
from review_queue.ai_review import review_story_in_database, run_review_batch
from review_queue.console_api import PHASE_PROMPT_MAP
from review_queue.console_api import router as console_router
from review_queue.control_api import router as control_router
from review_queue.db import (
    get_database_path,
    get_story,
    initialize_database,
    list_phase_transitions,
    list_reviewable_stories,
    story_from_row,
    update_story_metadata,
    update_story_status,
)
from review_queue.metrics import list_api_usage_logs, query_overview, record_pipeline_event
from review_queue.models import Story
from review_queue.phase_progress import (
    PHASE_LABELS,
    PHASES,
    PhaseAttempt,
    PhaseStep,
    PhaseTimelineEntry,
    WorkDirFile,
    compute_attempts,
    compute_overall_steps,
    compute_phase3_section_progress,
    compute_phase_progress,
    compute_phase_timeline,
    list_phase_artifacts,
    list_work_dir_files,
    normalize_resume_from,
    read_work_dir_file,
)
from review_queue.scan_plan_api import router as scan_plan_router
from review_queue.settings_api import mode_router
from review_queue.settings_api import router as settings_router
from runtime_helpers import configure_logging, recent_log_lines

logger = logging.getLogger(__name__)

SHORT_PHASE_DETAILS: dict[str, dict[str, Any]] = {
    "phase_0": {
        "description": "从当前题材库选定一个种子题材，并压缩为可直接执行的选题卡。",
        "source": "generator/c_pipeline/phase0_select.py",
        "inputs": (),
    },
    "phase_1": {
        "description": "把选题卡扩展为故事圣经，固定人物、冲突、反转、情绪曲线与结局承诺。",
        "source": "generator/c_pipeline/phase1_framework.py",
        "inputs": ("0_选题.json",),
    },
    "phase_2": {
        "description": "把故事圣经拆成逐节可执行的因果节拍，控制总字数和反转位置。",
        "source": "generator/c_pipeline/phase2_outline.py",
        "inputs": ("1_设定.md",),
    },
    "phase_3": {
        "description": "按节拍逐节写作，并使用已完成前文维持连续性，最后合并为完整初稿。",
        "source": "generator/c_pipeline/phase3_sections.py",
        "inputs": ("1_设定.md", "2_小节大纲.md", "3_正文_*.md"),
    },
    "phase_4": {
        "description": "对完整初稿做结构和语言精修，修复逻辑跳跃、重复与节奏问题。",
        "source": "generator/c_pipeline/phase4_polish.py",
        "inputs": ("3_正文_合稿.md",),
    },
    "phase_5": {
        "description": "在不改变剧情事实的前提下清理 AI 腔、套话和机械句式。",
        "source": "generator/c_pipeline/phase5_deslop.py",
        "inputs": ("4_精修稿.md", "generator/c_pipeline/prompts/ai_slop_blacklist.json"),
    },
    "phase_6": {
        "description": "根据正文节奏切分章节并生成章节标题，输出可阅读的最终稿。",
        "source": "generator/c_pipeline/phase6_chapter_title.py",
        "inputs": ("5_最终稿.md",),
    },
    "phase_7": {
        "description": "对最终稿进行 AI 审核；达到阈值则进入人工复核，否则按审核意见返修或转人工。",
        "source": "review_queue/ai_review.py",
        "inputs": ("6_最终稿_带章节.md",),
    },
}


app = FastAPI(title="ANW Auto Novel Writer")
static_dir = Path(__file__).resolve().parent / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(settings_router)
app.include_router(mode_router)
app.include_router(control_router)
app.include_router(scan_plan_router)
app.include_router(console_router)
app.include_router(long_novel_router)
app.include_router(theme_router)

# Ensure long-novel + theme tables exist on startup


@app.on_event("startup")
async def _ensure_long_novel_tables() -> None:
    db_path = initialize_database(load_from_environment())
    initialize_long_novel_tables(db_path)
    initialize_theme_tables(db_path)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, message: str | None = None) -> HTMLResponse:
    """Render the local ANW management studio."""
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
    long_novel = _long_novel_stats(db_path)
    recent = _list_stories(db_path, limit=12)
    latest = recent[0] if recent else None
    return {
        "ok": True,
        "stats": stats,
        **stats,
        "long_novel": long_novel,
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


# ============================================================================
# Phase F (decision #27 / U2): per-story progress strip, work_dir browser,
# and resume-from-phase trigger. The dashboard surfaces these endpoints as
# the phase progress bar, file browser, and "续跑" button respectively.
# ============================================================================


@app.get("/api/stories/{story_id}/phases")
def api_story_phases(story_id: int) -> dict[str, Any]:
    """Phase progress strip for one story (compute_phase_progress).

    Returns the 6-step strip plus an enriched timeline (phase_transitions
    aggregated into per-phase enter/exit timestamps + duration), Phase 3
    section sub-progress, and per-phase artifact filenames so the dashboard
    can wire the "查看产物" buttons without hard-coding paths.
    """

    story = _ensure_story_exists(story_id)
    progress = compute_phase_progress(story.current_phase)

    config = load_from_environment()
    db_path = get_database_path(config)
    transitions: list[dict[str, str]] = []
    try:
        transitions = list_phase_transitions(db_path, story.id)
    except sqlite3.OperationalError:
        # phase_transitions table is created lazily by initialize_database;
        # if the dashboard is loaded before any pipeline run we can still
        # return the static strip.
        transitions = []

    work_dir: Path | None = None
    raw_dir = (story.work_dir or "").strip()
    if raw_dir:
        candidate = Path(raw_dir)
        if candidate.exists() and candidate.is_dir():
            work_dir = candidate

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    timeline = compute_phase_timeline(transitions, now_iso=now_iso)
    attempts = compute_attempts(transitions, now_iso=now_iso)
    overall_steps = compute_overall_steps(transitions, story.current_phase)
    section_progress = compute_phase3_section_progress(transitions, work_dir=work_dir)
    artifacts = list_phase_artifacts(work_dir)

    # If story was generated with a custom preset, include its steps and output files
    preset_steps: list[dict[str, Any]] | None = None
    preset_name = (story.preset_name or "").strip()
    if preset_name and preset_name != "default":
        try:
            from generator.c_pipeline.preset_loader import load_preset
            preset = load_preset(preset_name)
            raw_steps = preset.get("steps") or []
            preset_steps = [
                {"phase": s.get("id", ""), "label": s.get("label", s.get("id", "")),
                 "status": _preset_step_status(s.get("id", ""), story.current_phase, raw_steps)}
                for s in raw_steps
            ]
            # Also add preset-declared output files to artifacts
            if work_dir is not None:
                for s in raw_steps:
                    sid = s.get("id", "")
                    out = s.get("output")
                    if not sid or not out:
                        continue
                    target = work_dir / out
                    exists = target.exists() and target.is_file()
                    size = target.stat().st_size if exists else None
                    artifacts[sid] = [{"name": out, "exists": exists, "size_bytes": size}]
        except Exception:
            preset_steps = None

    last_failed = next(
        (a for a in reversed(attempts[:-1]) if a.status == "failed"),
        None,
    )
    retry_banner: dict[str, Any] | None = None
    if len(attempts) > 1:
        retry_banner = {
            "attempt": len(attempts),
            "previous_failed_at": last_failed.failed_at if last_failed else None,
        }

    return {
        "ok": True,
        "story_id": story.id,
        "current_phase": progress.current_phase,
        "percent": progress.percent,
        "label": progress.label,
        "state": progress.state,
        "failed_at": progress.failed_at,
        "section_index": progress.section_index,
        "steps": [_phase_step_payload(step) for step in overall_steps],
        "preset_steps": preset_steps,
        "timeline": [_timeline_payload(entry) for entry in timeline],
        "attempts": [_attempt_payload(a) for a in attempts],
        "retry": retry_banner,
        "phase_3_section": _section_payload(section_progress),
        "artifacts": artifacts,
        "work_dir": str(work_dir) if work_dir else None,
    }


@app.get("/api/stories/{story_id}/phases/{phase}/detail")
def api_story_phase_detail(story_id: int, phase: str) -> dict[str, Any]:
    """Return the same inspectable chain information used by the long-novel UI."""

    story = _ensure_story_exists(story_id)
    if phase not in SHORT_PHASE_DETAILS:
        raise HTTPException(status_code=404, detail=f"未知短篇阶段：{phase}")

    detail = SHORT_PHASE_DETAILS[phase]
    work_dir = None
    if (story.work_dir or "").strip():
        candidate = Path(story.work_dir)
        if candidate.exists() and candidate.is_dir():
            work_dir = candidate

    inputs: list[dict[str, Any]] = []
    if phase == "phase_0":
        for name, value in (
            ("theme", story.genre or story.hint_title or ""),
            ("emotion", story.emotion or ""),
            ("target_length", story.target_length),
        ):
            inputs.append({"kind": "parameter", "name": name, "value": value})
    for input_path in detail["inputs"]:
        inputs.extend(_short_phase_input_payload(work_dir, str(input_path)))

    artifacts = list_phase_artifacts(work_dir).get(phase, [])
    outputs = [
        {
            "path": row["name"],
            "exists": bool(row["exists"]),
            "size_bytes": row["size_bytes"],
        }
        for row in artifacts
    ]
    if phase == "phase_7":
        outputs.append({
            "path": "stories.summary / stories.ai_review_score",
            "exists": story.ai_review_score is not None or bool(story.summary),
            "size_bytes": None,
        })
    prompt = _short_phase_prompt_payload(phase)
    config = load_from_environment()
    deepseek = config.data.get("deepseek", {}) or {}
    call_parameters: dict[str, Any] = {
        "model": deepseek.get("model"),
        "thinking_mode": deepseek.get("thinking_mode"),
        "max_output_tokens": deepseek.get("max_output_tokens"),
        "timeout_seconds": deepseek.get("timeout_seconds"),
        "max_retries": deepseek.get("max_retries"),
    }
    latest_call = _latest_short_phase_call(_database_path(config), story.id, phase)
    if latest_call:
        call_parameters.update(latest_call)

    phase_index = PHASES.index(phase)
    return {
        "ok": True,
        "story_id": story.id,
        "phase": phase,
        "label": PHASE_LABELS.get(phase, phase),
        "description": detail["description"],
        "flow": {
            "stage": "短篇生成链路" if phase_index <= 6 else "审核链路",
            "order": phase_index + 1,
            "total": len(PHASES),
            "next": PHASE_LABELS.get(PHASES[phase_index + 1]) if phase_index + 1 < len(PHASES) else None,
            "source": detail["source"],
        },
        "inputs": inputs,
        "call_parameters": call_parameters,
        "call_is_actual": bool(latest_call),
        "outputs": outputs,
        "prompt": prompt,
    }


def _short_phase_input_payload(work_dir: Path | None, raw_path: str) -> list[dict[str, Any]]:
    project_root = Path(__file__).resolve().parents[1]
    is_project_path = raw_path.startswith("generator/") or raw_path.startswith("review_queue/")
    base = project_root if is_project_path else work_dir
    if base is None:
        return [{"kind": "file", "path": raw_path, "label": raw_path, "exists": False}]

    matches = list(base.glob(raw_path)) if "*" in raw_path else [base / raw_path]
    if not matches:
        matches = [base / raw_path]
    payload: list[dict[str, Any]] = []
    for target in matches:
        exists = target.exists() and target.is_file()
        size = target.stat().st_size if exists else None
        display_path = raw_path if "*" not in raw_path else target.name
        payload.append({
            "kind": "file",
            "path": display_path,
            "label": display_path,
            "exists": exists,
            "bytes_used": size,
            "bytes_total": size,
        })
    return payload


def _short_phase_prompt_payload(phase: str) -> dict[str, Any] | None:
    filename = PHASE_PROMPT_MAP.get(phase)
    if not filename:
        return None
    path = Path(__file__).resolve().parents[1] / "generator" / "c_pipeline" / "prompts" / filename
    if not path.exists():
        return {"filename": filename, "content": "", "variables": [], "exists": False}
    content = path.read_text(encoding="utf-8")
    variables = sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", content)))
    return {
        "filename": filename,
        "content": content,
        "variables": variables,
        "exists": True,
        "size_bytes": len(content.encode("utf-8")),
    }


def _latest_short_phase_call(db_path: Path, story_id: int, phase: str) -> dict[str, Any] | None:
    patterns = {
        "phase_3": "phase_3%",
        "phase_7": "ai_review%",
    }
    pattern = patterns.get(phase, phase + "%")
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT model, input_tokens, cached_tokens, output_tokens, cost_cny, occurred_at
                FROM pipeline_cost_log
                WHERE story_id = ? AND phase LIKE ?
                ORDER BY occurred_at DESC, id DESC
                LIMIT 1
                """,
                (story_id, pattern),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return {
        "model": str(row["model"] or ""),
        "input_tokens": int(row["input_tokens"] or 0),
        "cached_tokens": int(row["cached_tokens"] or 0),
        "output_tokens": int(row["output_tokens"] or 0),
        "cost_cny": float(row["cost_cny"] or 0),
        "started_at": str(row["occurred_at"] or ""),
    }


@app.get("/api/stories/{story_id}/files")
def api_story_files(story_id: int) -> dict[str, Any]:
    """List files in the story's work_dir (top level only, sorted)."""

    story = _ensure_story_exists(story_id)
    work_dir = _story_work_dir_or_404(story)
    files = list_work_dir_files(work_dir)
    return {
        "ok": True,
        "story_id": story.id,
        "work_dir": str(work_dir),
        "files": [_file_payload(f) for f in files],
    }


@app.get("/api/stories/{story_id}/files/{filename:path}")
def api_story_file_content(story_id: int, filename: str) -> dict[str, Any]:
    """Return the text contents of one file inside the story's work_dir.

    Path traversal escapes ``work_dir`` are rejected with 400. Files
    outside the text suffix allow-list (or larger than 1 MiB) are also
    rejected with 400 so the browser doesn't try to render binaries.
    """

    story = _ensure_story_exists(story_id)
    work_dir = _story_work_dir_or_404(story)
    try:
        text = read_work_dir_file(work_dir, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "story_id": story.id,
        "work_dir": str(work_dir),
        "name": filename,
        "size_bytes": len(text.encode("utf-8")),
        "content": text,
    }


@app.post("/api/stories/{story_id}/resume")
async def api_story_resume(story_id: int, request: Request) -> dict[str, Any]:
    """Resume the c_pipeline state machine from a chosen phase.

    Body: ``{"resume_from": "phase_4"}`` (or ``phase_3_done`` which
    advances to ``phase_4``). The orchestrator call is wrapped through
    ``_invoke_resume_pipeline`` so tests can stub it.
    """

    story = _ensure_story_exists(story_id)
    payload = await _json_payload(request)
    raw = payload.get("resume_from")
    try:
        resume_from = normalize_resume_from(str(raw) if raw is not None else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = _load_config()
    db_path = _database_path(config)
    try:
        result = _invoke_resume_pipeline(
            story_id=story.id,
            resume_from=resume_from,
            config=config,
        )
    except Exception as exc:
        record_pipeline_event(
            db_path,
            kind="resume",
            status="failed",
            story_id=story.id,
            message=f"{exc.__class__.__name__}: {exc}",
            detail=resume_from,
        )
        raise HTTPException(
            status_code=500,
            detail=f"resume failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    record_pipeline_event(
        db_path,
        kind="resume",
        status=str(getattr(result, "status", "")) or "completed",
        story_id=story.id,
        message=f"resume_from={resume_from}",
        detail=str(getattr(result, "final_phase", "")),
    )
    logger.info(
        "Dashboard resume action: story_id=%s resume_from=%s final_phase=%s",
        story.id,
        resume_from,
        getattr(result, "final_phase", None),
    )
    return {
        "ok": True,
        "message": f"已从 {resume_from} 续跑作品 #{story.id}",
        "story_id": story.id,
        "resume_from": resume_from,
        "final_phase": getattr(result, "final_phase", None),
        "status": getattr(result, "status", None),
        "char_count": getattr(result, "char_count", None),
        "duration_seconds": getattr(result, "duration_seconds", None),
    }


@app.post("/api/stories/{story_id}/rerun-phase/{phase}")
def api_rerun_phase(story_id: int, phase: str, mode: str = "all") -> dict[str, Any]:
    """重新运行 phase。

    mode=all（默认）：从该 phase 开始跑完所有后续步骤。
    mode=single：只跑这一个 phase，跑完就停。
    """
    from review_queue.phase_progress import PHASES as PROGRESS_PHASES

    if phase not in PROGRESS_PHASES:
        valid = ", ".join(PROGRESS_PHASES)
        raise HTTPException(status_code=400, detail=f"无效 phase：{phase}。可选值：{valid}")

    if mode not in ("all", "single"):
        raise HTTPException(status_code=400, detail="mode 必须是 all 或 single")

    story = _ensure_story_exists(story_id)
    config = _load_config()
    db_path = _database_path(config)

    stop_after = phase if mode == "single" else None
    try:
        result = _invoke_resume_pipeline(
            story_id=story.id,
            resume_from=phase,
            config=config,
            stop_after=stop_after,
        )
    except Exception as exc:
        record_pipeline_event(
            db_path,
            kind="rerun_phase",
            status="failed",
            story_id=story.id,
            message=f"rerun {phase} mode={mode}: {exc}",
        )
        raise HTTPException(
            status_code=500,
            detail=f"rerun {phase} failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    record_pipeline_event(
        db_path,
        kind="rerun_phase",
        status=str(getattr(result, "status", "")) or "completed",
        story_id=story.id,
        message=f"rerun from {phase} mode={mode}",
        detail=str(getattr(result, "final_phase", "")),
    )
    logger.info("Dashboard rerun-phase: story_id=%s phase=%s mode=%s", story.id, phase, mode)
    return {
        "ok": True,
        "message": f"已{'仅' if mode == 'single' else '从'} {phase} {'重跑' if mode == 'single' else '开始重跑'}作品 #{story.id}",
        "story_id": story.id,
        "phase": phase,
        "mode": mode,
        "final_phase": getattr(result, "final_phase", None),
        "status": getattr(result, "status", None),
    }


def _invoke_resume_pipeline(
    *,
    story_id: int,
    resume_from: str,
    config: LoadedConfig,
    stop_after: str | None = None,
) -> Any:
    """Indirection layer so tests can monkeypatch the orchestrator call."""

    from generator.c_pipeline.orchestrator import run_pipeline

    return run_pipeline(
        story_id=story_id,
        config=config,
        resume_from=resume_from,
        stop_after=stop_after,
    )


def _story_work_dir_or_404(story: Story) -> Path:
    raw = (story.work_dir or "").strip()
    if not raw or raw == "(pending)":
        raise HTTPException(
            status_code=404,
            detail="story has no work_dir yet (still pending phase 0).",
        )
    work_dir = Path(raw)
    if not work_dir.exists() or not work_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"work_dir missing on disk: {work_dir}",
        )
    return work_dir


def _preset_step_status(step_id: str, current_phase: str, all_steps: list[dict[str, Any]]) -> str:
    """Determine status of a preset step relative to the story's current_phase."""
    if current_phase == "complete":
        return "done"
    # Find the running/completed step
    for i, s in enumerate(all_steps):
        sid = s.get("id", "")
        if current_phase.startswith(sid):
            if current_phase.endswith("_done"):
                return "done"
            if current_phase.endswith("_running"):
                return "running"
            if current_phase.endswith("_user_paused"):
                return "paused"
            if current_phase.endswith("_user_skipped"):
                return "skipped"
            if current_phase.endswith("_failed") or "failed_at_" in current_phase:
                return "failed"
            return "running"
        # Steps before the current one are done
        if current_phase == sid + "_done" or current_phase == "complete":
            return "done"
    # Fallback: mark completed steps as done
    for i, s in enumerate(all_steps):
        sid = s.get("id", "")
        if current_phase.startswith(sid):
            for j in range(i):
                pass  # steps before are done
            return "running"
    return "pending"


def _phase_step_payload(step: PhaseStep) -> dict[str, Any]:
    return {"phase": step.phase, "label": step.label, "status": step.status}


def _timeline_payload(entry: PhaseTimelineEntry) -> dict[str, Any]:
    return {
        "phase": entry.phase,
        "label": entry.label,
        "status": entry.status,
        "entered_at": entry.entered_at,
        "completed_at": entry.completed_at,
        "duration_seconds": entry.duration_seconds,
    }


# ============================================================================
# Phase 6: wait_for_human API
# ============================================================================

@app.get("/api/stories/{story_id}/pending_input")
def api_pending_input(story_id: int) -> dict[str, Any]:
    db_path = _database_path()
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT id, prompt, input_schema, created_at FROM pending_human_input "
                "WHERE story_id = ? AND resolved_at IS NULL ORDER BY id DESC LIMIT 1",
                (story_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return {"ok": True, "pending": False, "message": "pending_human_input 表尚未创建"}
    if not row:
        return {"ok": True, "pending": False}
    return {
        "ok": True, "pending": True,
        "input_id": row[0], "prompt": row[1],
        "input_schema": json.loads(row[2]) if row[2] else {"type": "text"},
        "created_at": row[3],
    }


@app.post("/api/stories/{story_id}/provide_input")
async def api_provide_input(story_id: int, request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    if not payload:
        raise HTTPException(status_code=400, detail="payload 不能为空")
    db_path = str(_database_path())
    from generator.c_pipeline.builtin.wait_for_human import provide_input
    ok = provide_input(story_id, payload, db_path)
    if not ok:
        raise HTTPException(status_code=500, detail="提交失败或该故事没有等待中的输入")
    return {"ok": True, "message": "输入已提交，流水线继续执行"}


def _section_payload(progress: Any) -> dict[str, Any] | None:
    if progress is None:
        return None
    return {
        "current": progress.current,
        "total": progress.total,
        "completed": list(progress.completed),
    }


def _attempt_payload(attempt: PhaseAttempt) -> dict[str, Any]:
    return {
        "attempt": attempt.attempt,
        "started_at": attempt.started_at,
        "ended_at": attempt.ended_at,
        "status": attempt.status,
        "failed_at": attempt.failed_at,
        "phases": [_timeline_payload(entry) for entry in attempt.phases],
    }


def _file_payload(entry: WorkDirFile) -> dict[str, Any]:
    return {
        "name": entry.name,
        "relative_path": entry.relative_path,
        "size_bytes": entry.size_bytes,
        "modified_at": entry.modified_at,
        "is_text": entry.is_text,
    }


@app.delete("/api/stories/{story_id}")
def api_delete_story(story_id: int) -> dict[str, Any]:
    """Delete one story from the local queue.

    Useful for cleaning up old failed/published records from the management UI.
    """

    _ensure_story_exists(story_id)
    db_path = _database_path()
    # 获取 work_dir 以便同步删除产物
    import shutil
    story = get_story(db_path, story_id)
    work_dir_str = (story.work_dir or "").strip() if story else ""
    with sqlite3.connect(Path(db_path)) as connection:
        cursor = connection.execute("DELETE FROM stories WHERE id = ?", (story_id,))
        deleted = cursor.rowcount
    if deleted <= 0:
        raise HTTPException(status_code=404, detail="Story not found")
    # 删除产物目录
    if work_dir_str:
        work_path = Path(work_dir_str)
        if work_path.exists() and work_path.is_dir():
            shutil.rmtree(work_path, ignore_errors=True)
            logger.info("Dashboard delete work_dir story_id=%s path=%s", story_id, work_path)
    logger.info("Dashboard delete story_id=%s", story_id)
    return {"ok": True, "message": f"已删除 ID #{story_id} 的作品。", "deleted": deleted}


@app.post("/api/stories/repair-orphans")
def api_repair_orphans() -> dict[str, Any]:
    """扫描 data/works/ 目录，为缺失 DB 记录的目录补建 stories 条目。"""
    config = _load_config()
    db_path = _database_path(config)
    project_root = Path(config.data.get("runtime", {}).get("project_root", "."))
    if not project_root.is_absolute() or str(project_root) == ".":
        project_root = Path(__file__).resolve().parents[1]
    works_dir = project_root / "data" / "works"
    if not works_dir.exists():
        return {"ok": True, "message": "data/works 目录不存在", "added": 0}

    # Get existing story IDs
    existing_ids: set[int] = set()
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT id FROM stories").fetchall()
            existing_ids = {r[0] for r in rows}
    except Exception:
        pass

    added = 0
    for child in sorted(works_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            sid = int(child.name)
        except ValueError:
            continue
        if sid in existing_ids:
            continue
        # Try to get title from 1_设定.md first, then from directory name
        title = f"作品 #{sid}"
        status = "pending"
        current_phase = "phase_0"
        summary = ""
        # Try to read title from artifacts
        try:
            framework = child / "1_设定.md"
            if framework.exists():
                text = framework.read_text(encoding="utf-8")[:2000]
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("标题") or line.startswith("title"):
                        title = line.split("：", 1)[-1].split(":", 1)[-1].strip()[:100] or title
                        break
        except Exception:
            pass
        # Count phases completed
        try:
            artifacts = sorted(child.glob("*_*.md")) + sorted(child.glob("*_*.json"))
            phases_seen = set()
            for a in artifacts:
                stem = a.name.split("_")[0] if "_" in a.name else ""
                if stem.isdigit():
                    phases_seen.add(int(stem))
                elif stem == "5":
                    phases_seen.add(5)
            if phases_seen:
                max_phase = max(phases_seen)
                if max_phase >= 6:
                    status = "pending"
                elif max_phase >= 5:
                    status = "needs_human"
                current_phase = f"phase_{min(max_phase, 6)}_done"
        except Exception:
            pass

        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO stories (id, title, status, current_phase, summary, work_dir) VALUES (?, ?, ?, ?, ?, ?)",
                    (sid, title, status, current_phase, summary, str(child)),
                )
            added += 1
            logger.info("repair-orphans added story_id=%s title=%s", sid, title)
        except Exception as exc:
            logger.warning("repair-orphans skip story_id=%s: %s", sid, exc)

    return {"ok": True, "message": f"扫描 {len(existing_ids) + added} 个目录，补建 {added} 条记录", "added": added}


@app.post("/api/review/{story_id}/approve")
def api_approve_story(story_id: int) -> dict[str, Any]:
    _ensure_story_exists(story_id)
    db_path = _database_path()
    if not update_story_status(db_path, story_id, "approved", summary="人工批准。"):
        raise HTTPException(status_code=404, detail="Story not found")
    record_pipeline_event(db_path, kind="review", status="approved", story_id=story_id, message="manual")
    logger.info("Dashboard review action: approved story_id=%s", story_id)
    return {"ok": True, "message": "已批准。"}


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





@app.get("/api/logs")
def api_logs(max_lines: int = 120) -> dict[str, Any]:
    config = _load_config()
    log_file, lines = recent_log_lines(config, max_lines=max(10, min(max_lines, 500)))
    return {"ok": True, "log_file": str(log_file), "lines": lines}


@app.get("/api/logs/costs")
def api_log_costs(limit: int = 80) -> dict[str, Any]:
    config = _load_config()
    db_path = _database_path(config)
    rows = list_api_usage_logs(db_path, limit=max(10, min(limit, 200)))
    count = len(rows)
    total_cost = round(sum(float(row.get("cost_cny") or 0.0) for row in rows), 4)
    avg_cost = round((total_cost / count), 4) if count else 0.0
    peak_cost = round(max((float(row.get("cost_cny") or 0.0) for row in rows), default=0.0), 4)
    latest_at = rows[0]["occurred_at"] if rows else None
    return {
        "ok": True,
        "items": rows,
        "summary": {
            "count": count,
            "total_cost_cny": total_cost,
            "avg_cost_cny": avg_cost,
            "peak_cost_cny": peak_cost,
            "latest_at": latest_at,
            "window_label": f"最近 {count} 次调用" if count else "暂无调用记录",
        },
    }


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
    long_novel = _long_novel_stats(db_path)
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
        "long_novel": long_novel,
        **overview,
    }


@app.get("/api/monitor/cards")
def api_monitor_cards() -> dict[str, Any]:
    """4 张状态卡片 (Phase 3 总览 Dashboard 顶部)."""
    from review_queue.monitor_aggregator import monitor_cards

    config = _load_config()
    db_path = _database_path(config)
    return {"ok": True, **monitor_cards(config, db_path)}


@app.get("/api/monitor/concurrency")
def api_monitor_concurrency() -> dict[str, Any]:
    """Phase G.2 — expose K2 pipeline semaphore stats for the dashboard.

    Reads ``c_pipeline.max_concurrent_pipelines`` (default 2 per decision
    #32) and the live ``in_use`` / ``available`` slot counts from the
    process-global semaphore. Used by the operator Web UI to confirm the
    cap is enforced and to detect stuck slots.
    """
    from generator.c_pipeline.concurrency import get_global_semaphore

    config = _load_config()
    semaphore = get_global_semaphore(config)
    stats = semaphore.stats()
    return {
        "ok": True,
        "max_concurrent": stats.max_concurrent,
        "in_use": stats.in_use,
        "available": stats.available,
    }


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    """Lightweight liveness probe for external monitoring."""
    config = _load_config()
    db_path = _database_path(config)
    if config.warnings:
        status = "degraded"
    else:
        status = "ok"
    return {
        "ok": True,
        "status": status,
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
    statuses = ["pending", "needs_human", "approved", "rejected", "failed"]
    stats = {status: 0 for status in statuses}
    with sqlite3.connect(Path(db_path)) as connection:
        total = connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        rows = connection.execute("SELECT status, COUNT(*) FROM stories GROUP BY status").fetchall()
    for status, count in rows:
        stats[str(status)] = int(count)
    stats["total"] = int(total)
    return stats


def _long_novel_stats(db_path: str | Path) -> dict[str, Any]:
    """Small long-novel summary for the main dashboard and monitor page."""

    try:
        initialize_long_novel_tables(db_path)
        with sqlite3.connect(Path(db_path)) as connection:
            connection.row_factory = sqlite3.Row
            book_rows = connection.execute(
                """
                SELECT
                    b.*,
                    COUNT(c.id) AS chapters_total,
                    COALESCE(SUM(CASE
                        WHEN c.status IN ('published', 'draft', 'final', 'finalized', 'done')
                             OR COALESCE(c.draft_path, '') != ''
                             OR COALESCE(c.actual_words, 0) > 0
                        THEN 1 ELSE 0 END), 0) AS chapters_done,
                    COALESCE(SUM(CASE WHEN c.status = 'writing' THEN 1 ELSE 0 END), 0) AS chapters_writing,
                    COALESCE(SUM(CASE WHEN c.status = 'outline_only' THEN 1 ELSE 0 END), 0) AS chapters_outline,
                    COALESCE(SUM(c.actual_words), 0) AS words_total
                FROM ln_books b
                LEFT JOIN ln_chapters c ON c.book_id = b.id
                GROUP BY b.id
                ORDER BY b.updated_at DESC, b.id DESC
                """
            ).fetchall()
            chapter_status = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT status, COUNT(*) FROM ln_chapters GROUP BY status"
                ).fetchall()
            }
    except Exception:
        logger.exception("long_novel_stats_failed")
        return {
            "books_total": 0,
            "status": {},
            "chapters_total": 0,
            "chapters_planned": 0,
            "chapters_done": 0,
            "chapters_writing": 0,
            "chapters_outline": 0,
            "chapters_remaining": 0,
            "words_total": 0,
            "progress_pct": 0.0,
            "chapter_status": {},
            "recent": [],
        }

    book_status: dict[str, int] = {}
    recent: list[dict[str, Any]] = []
    chapters_total = 0
    chapters_planned = 0
    chapters_done = 0
    chapters_writing = 0
    chapters_outline = 0
    words_total = 0
    for row in book_rows:
        status = str(row["status"] or "unknown")
        book_status[status] = book_status.get(status, 0) + 1
        total = int(row["chapters_total"] or 0)
        done = int(row["chapters_done"] or 0)
        writing = int(row["chapters_writing"] or 0)
        outline = int(row["chapters_outline"] or 0)
        words = int(row["words_total"] or 0)
        target = int(row["target_chapters"] or 0)
        planned = max(total, target)
        denom = max(planned, 1)
        chapters_total += total
        chapters_planned += planned
        chapters_done += done
        chapters_writing += writing
        chapters_outline += outline
        words_total += words
        if len(recent) < 8:
            recent.append(
                {
                    "id": int(row["id"]),
                    "title": row["title"],
                    "genre": row["genre"],
                    "status": status,
                    "current_chapter": int(row["current_chapter"] or 0),
                    "target_chapters": target,
                    "chapters_total": total,
                    "chapters_planned": planned,
                    "chapters_done": done,
                    "chapters_writing": writing,
                    "chapters_outline": outline,
                    "chapters_remaining": max(0, planned - done - writing),
                    "words_total": words,
                    "progress_pct": round(done / denom * 100, 1),
                    "updated_at": row["updated_at"],
                    "created_at": row["created_at"],
                }
            )

    progress_denom = max(chapters_planned, 1)
    return {
        "books_total": len(book_rows),
        "status": book_status,
        "chapters_total": chapters_total,
        "chapters_planned": chapters_planned,
        "chapters_done": chapters_done,
        "chapters_writing": chapters_writing,
        "chapters_outline": chapters_outline,
        "chapters_remaining": max(0, chapters_planned - chapters_done - chapters_writing),
        "words_total": words_total,
        "progress_pct": round(chapters_done / progress_denom * 100, 1),
        "chapter_status": chapter_status,
        "recent": recent,
    }


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
        "backup_cron": str(scheduler_cfg.get("backup_cron") or ""),
    }


def _system_health(config: LoadedConfig, db_path: Path) -> dict[str, Any]:
    db_size = 0
    try:
        if Path(db_path).exists():
            db_size = Path(db_path).stat().st_size
    except OSError:
        db_size = 0
    log_path = Path(str((config.data.get("logging") or {}).get("file") or "logs/anw.log"))
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
        _assets.DASHBOARD_BODY_TEMPLATE
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
        "  <title>ANW Auto Novel Writer</title>\n"
        f"  <style>{_assets.DASHBOARD_CSS}</style>\n"
        "  <script src=\"https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js\"></script>\n"
        "</head>\n"
        f"<body>{body}\n"
        f"<script>{_assets.DASHBOARD_JS}</script>\n"
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
    parser = argparse.ArgumentParser(description="Start the local ANW management FastAPI app.")
    parser.add_argument("--host", default=get_env("ANW_REVIEW_HOST", "127.0.0.1"), help="Bind host for the local server.")
    parser.add_argument("--port", type=int, default=_server_port_from_env(), help="Bind port for the local server.")
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    return args


def _server_port_from_env() -> int:
    raw_port = get_env("ANW_REVIEW_PORT", "8000")
    try:
        return int(raw_port)
    except ValueError:
        logger.warning("Invalid ANW_REVIEW_PORT value; falling back to 8000")
        return 8000


if __name__ == "__main__":
    import uvicorn

    args = _parse_server_args()
    uvicorn.run("review_queue.human_review:app", host=args.host, port=args.port, reload=False)
