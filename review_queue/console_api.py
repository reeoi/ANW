"""Execution console API — backs the dashboard hero panel on the 生成作品 tab.

Endpoints:

- ``GET  /api/console/status``      → status snapshot for 5s polling
                                       (current task / login / theme pool)
- ``POST /api/console/run-now``     → fire-and-forget atomic generate→AI review
- ``POST /api/console/cancel``      → set ``cancel_requested=1`` on the active story

The frontend polls every 5 seconds. The scheduler-related fields
(``scheduler_running``, ``today.next_slot_iso``, today's planned/published
counts, the ``/auto`` toggle) were removed when ANP switched to
manual-only execution.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config_loader import load_from_environment
from review_queue.atomic_runner import kick_off_async
from review_queue.atomic_runner import state as atomic_state
from review_queue.db import (
    get_story,
    initialize_database,
    request_story_cancel,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/console", tags=["console"])


# ============================================================================
# helpers
# ============================================================================


def _theme_pool_path() -> Path:
    """Match seed_evolver / scan_plan_api convention."""
    config = load_from_environment()
    runtime = config.data.get("runtime") or {}
    rt = runtime.get("project_root")
    if rt and rt != ".":
        return Path(rt).resolve() / "data" / "theme_pool.json"
    return Path(__file__).resolve().parents[1] / "data" / "theme_pool.json"


def _theme_pool_count() -> int:
    pool_path = _theme_pool_path()
    if not pool_path.exists():
        return 0
    try:
        data = json.loads(pool_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    items = data.get("items") if isinstance(data, dict) else None
    if items is None and isinstance(data, list):
        items = data
    return len(items) if isinstance(items, list) else 0


def _login_state_payload() -> dict[str, Any]:
    config = load_from_environment()
    fansq = ((config.data.get("publisher") or {}).get("fansq") or {})
    raw_path = str(fansq.get("login_state_path") or "").strip()
    if not raw_path:
        return {"status": "missing", "path": None, "exists": False}
    path = Path(raw_path)
    exists = path.exists()
    return {
        "status": "valid" if exists else "missing",
        "path": str(path),
        "exists": exists,
    }


def _current_task_payload() -> dict[str, Any] | None:
    """Inspect AtomicRunnerState; if running, enrich with story phase from DB."""
    snapshot = atomic_state.get_current()
    if snapshot is None:
        return None
    enriched = dict(snapshot)
    sid = snapshot.get("story_id")
    if sid is not None:
        try:
            config = load_from_environment()
            db_path = initialize_database(config)
            story = get_story(db_path, int(sid))
            if story is not None:
                enriched["current_phase"] = story.current_phase
                enriched["status"] = story.status
                enriched["title"] = story.title
        except Exception:  # pragma: no cover
            logger.exception("current task enrichment failed")
    return enriched


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON 请求体必须是对象")
    return payload


# ============================================================================
# endpoints
# ============================================================================


@router.get("/status")
def console_status() -> dict[str, Any]:
    """Aggregate status snapshot for the execution console hero."""

    return {
        "ok": True,
        "current_task": _current_task_payload(),
        "busy": atomic_state.is_busy(),
        "login_state": _login_state_payload(),
        "publish_fail_streak": atomic_state.get_publish_fail_streak(),
        "theme_pool_count": _theme_pool_count(),
    }


@router.post("/run-now")
async def console_run_now(request: Request) -> dict[str, Any]:
    """Kick a fire-and-forget atomic generate→AI review task.

    Returns 409 Conflict if another atomic task is already running.
    """
    payload = await _json_payload(request)
    raw_theme_id = payload.get("theme_id")
    if raw_theme_id is None:
        raise HTTPException(status_code=400, detail="请先从题材库选择一个题材")

    from generator.long_novel.theme_db import get_theme, mark_consumed

    config = load_from_environment()
    db_path = initialize_database(config)
    try:
        theme_id = int(raw_theme_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="theme_id 必须是整数") from exc
    theme = get_theme(db_path, theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="未找到可用题材")

    is_long_theme = theme.get("target_type") == "long"

    try:
        raw_item = json.loads(str(theme.get("raw_json") or "{}"))
    except json.JSONDecodeError:
        raw_item = {}
    if not isinstance(raw_item, dict):
        raw_item = {}
    selected_item = {
        **raw_item,
        "id": f"theme_db_{theme_id}",
        "theme": theme.get("theme") or "",
        "genre": theme.get("genre") or "",
        "emotion": theme.get("emotion") or "",
        "target_platform": "番茄短篇" if is_long_theme else (theme.get("platform") or "番茄短篇"),
        "target_length": [
            8000 if is_long_theme else int(theme.get("target_words_min") or 8000),
            15000 if is_long_theme else int(theme.get("target_words_max") or 15000),
        ],
        "hint_title": theme.get("hint_title") or "",
        "expected_audience": theme.get("audience") or "",
    }
    try:
        story_id = kick_off_async(config, overrides={"theme_pool_item": selected_item})
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    mark_consumed(db_path, theme_id)
    logger.info("Console run-now started: story_id=%s", story_id)
    return {
        "ok": True,
        "story_id": story_id,
        "theme_id": theme_id,
        "adapted_from_long": is_long_theme,
        "message": "已启动短篇创作任务",
    }


@router.post("/cancel")
async def console_cancel(request: Request) -> dict[str, Any]:
    """Set ``cancel_requested = 1`` on the active story (or one given by id)."""

    payload = await _json_payload(request)
    raw_sid = payload.get("story_id")
    if raw_sid is None:
        snapshot = atomic_state.get_current()
        if snapshot is None or snapshot.get("story_id") is None:
            raise HTTPException(status_code=404, detail="当前没有正在运行的任务")
        try:
            sid = int(snapshot["story_id"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="invalid current story id") from exc
    else:
        try:
            sid = int(raw_sid)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="story_id 必须是整数") from exc

    config = load_from_environment()
    db_path = initialize_database(config)
    story = get_story(db_path, sid)
    if story is None:
        raise HTTPException(status_code=404, detail="story 不存在")
    if story.status == "published":
        raise HTTPException(status_code=400, detail="已发布作品无法取消")
    ok = request_story_cancel(db_path, sid)
    if not ok:
        raise HTTPException(status_code=404, detail="story 不存在")
    logger.info("Console cancel requested: story_id=%s", sid)
    return {"ok": True, "story_id": sid, "message": "已请求取消，将在当前 phase 结束后停止"}


@router.get("/phase-controls")
def get_phase_controls() -> dict[str, Any]:
    """返回每个 phase 的控制策略（auto / skip / pause_after）。"""
    config = load_from_environment()
    c_pipe = config.data.get("c_pipeline") or {}
    controls = dict(c_pipe.get("phase_controls") or {})
    from review_queue.phase_progress import PHASE_LABELS
    from review_queue.phase_progress import PHASES as PROGRESS_PHASES
    result: dict[str, dict[str, str]] = {}
    for phase in PROGRESS_PHASES:
        result[phase] = {
            "phase": phase,
            "label": PHASE_LABELS.get(phase, phase),
            "control": controls.get(phase, "auto") or "auto",
        }
    return {"ok": True, "controls": result}


@router.post("/phase-controls")
async def set_phase_controls(request: Request) -> dict[str, Any]:
    """设置单个或多个 phase 的控制策略。body: {"phase_0": "skip", "phase_3": "pause_after"}"""
    payload = await _json_payload(request)
    allowed = {"auto", "skip", "pause_after"}
    for phase, value in payload.items():
        if not isinstance(phase, str) or not phase.startswith("phase_"):
            raise HTTPException(status_code=400, detail=f"无效 phase：{phase}")
        if str(value) not in allowed:
            raise HTTPException(status_code=400, detail=f"{phase} 控制值必须是 auto / skip / pause_after")
    from review_queue.yaml_writer import load_yaml, save_yaml
    cfg = load_yaml(_config_path())
    cfg.setdefault("c_pipeline", {})
    cfg["c_pipeline"].setdefault("phase_controls", {})
    for phase, value in payload.items():
        cfg["c_pipeline"]["phase_controls"][phase] = str(value)
    save_yaml(_config_path(), cfg)
    logger.info("phase_controls updated: %s", {k: v for k, v in payload.items()})
    return {"ok": True, "message": "阶段控制已更新", "controls": dict(cfg["c_pipeline"]["phase_controls"])}


# ============================================================================
# Prompt 提示词 API — 查看/编辑每个 phase 的 LLM prompt 模板
# ============================================================================

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "generator" / "c_pipeline" / "prompts"

PHASE_PROMPT_MAP: dict[str, str] = {
    "phase_0": "phase0_select.txt",
    "phase_1": "phase1_framework.txt",
    "phase_2": "phase2_outline.txt",
    "phase_3": "phase3_section.txt",
    "phase_4": "phase4_polish.txt",
    "phase_5": "phase5_deslop.txt",
    "phase_6": "phase6_chapter.txt",
}


@router.get("/prompts")
def list_prompts() -> dict[str, Any]:
    """列出所有 phase 的 prompt 文件名、大小、是否存在。"""
    from review_queue.phase_progress import PHASE_LABELS
    from review_queue.phase_progress import PHASES as PROGRESS_PHASES
    items: list[dict[str, Any]] = []
    for phase in PROGRESS_PHASES:
        fname = PHASE_PROMPT_MAP.get(phase)
        if not fname:
            continue
        fpath = _PROMPTS_DIR / fname
        exists = fpath.exists() and fpath.is_file()
        size = fpath.stat().st_size if exists else None
        items.append({
            "phase": phase,
            "label": PHASE_LABELS.get(phase, phase),
            "filename": fname,
            "exists": exists,
            "size_bytes": size,
        })
    return {"ok": True, "prompts": items}


@router.get("/prompts/{phase}")
def get_prompt(phase: str) -> dict[str, Any]:
    """读取某个 phase 的 prompt 模板全文。"""
    fname = PHASE_PROMPT_MAP.get(phase)
    if not fname:
        raise HTTPException(status_code=404, detail=f"未知 phase：{phase}")
    fpath = _PROMPTS_DIR / fname
    if not fpath.exists():
        raise HTTPException(status_code=404, detail=f"prompt 文件不存在：{fname}")
    content = fpath.read_text(encoding="utf-8")
    from review_queue.phase_progress import PHASE_LABELS
    return {
        "ok": True,
        "phase": phase,
        "label": PHASE_LABELS.get(phase, phase),
        "filename": fname,
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
    }


@router.post("/prompts/{phase}")
async def save_prompt(phase: str, request: Request) -> dict[str, Any]:
    """保存（覆写）某个 phase 的 prompt 模板。body: {"content": "..."}"""
    fname = PHASE_PROMPT_MAP.get(phase)
    if not fname:
        raise HTTPException(status_code=404, detail=f"未知 phase：{phase}")
    fpath = _PROMPTS_DIR / fname
    payload = await _json_payload(request)
    content = str(payload.get("content") or "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="prompt 内容不能为空")
    # 安全：禁止写入非 .txt 文件
    if fpath.suffix.lower() != ".txt":
        raise HTTPException(status_code=400, detail="只支持编辑 .txt 格式的 prompt 文件")
    # 备份旧文件
    backup = fpath.with_suffix(".txt.bak")
    if fpath.exists():
        backup.write_text(fpath.read_text(encoding="utf-8"), encoding="utf-8")
    fpath.write_text(content, encoding="utf-8")
    logger.info("prompt saved phase=%s file=%s bytes=%s", phase, fname, len(content))
    return {"ok": True, "message": f"已保存 {fname}（旧版备份至 {backup.name}）"}


@router.post("/prompts/{phase}/revert")
def revert_prompt(phase: str) -> dict[str, Any]:
    """恢复 prompt 为备份版本（.txt.bak → .txt）。"""
    fname = PHASE_PROMPT_MAP.get(phase)
    if not fname:
        raise HTTPException(status_code=404, detail=f"未知 phase：{phase}")
    fpath = _PROMPTS_DIR / fname
    backup = fpath.with_suffix(".txt.bak")
    if not backup.exists():
        raise HTTPException(status_code=404, detail="备份文件不存在，无法恢复")
    content = backup.read_text(encoding="utf-8")
    fpath.write_text(content, encoding="utf-8")
    logger.info("prompt reverted phase=%s file=%s", phase, fname)
    return {
        "ok": True,
        "message": f"已从备份恢复 {fname}",
        "filename": fname,
        "content": content,
        "size_bytes": len(content.encode("utf-8")),
    }


# ============================================================================
# Preset 预设 API
# ============================================================================


@router.get("/presets")
def list_presets_api() -> dict[str, Any]:
    from generator.c_pipeline.preset_loader import list_presets
    items = list_presets()
    config = load_from_environment()
    active = str((config.data.get("c_pipeline") or {}).get("active_preset") or "default")
    return {"ok": True, "presets": items, "active": active}


@router.get("/presets/{name}")
def get_preset(name: str) -> dict[str, Any]:
    from generator.c_pipeline.preset_loader import load_preset
    preset = load_preset(name)
    return {"ok": True, "preset": preset}


@router.post("/presets/{name}")
async def save_preset(name: str, request: Request) -> dict[str, Any]:
    """保存（覆写）一个预设 YAML 文件。body: 完整 preset dict。"""
    import yaml

    from generator.c_pipeline.preset_loader import DEFAULT_PRESETS_DIR
    payload = await _json_payload(request)
    if not payload:
        raise HTTPException(status_code=400, detail="preset 内容不能为空")
    # basic validation
    if not isinstance(payload.get("steps"), list):
        raise HTTPException(status_code=400, detail="steps 必须是 list")
    path = DEFAULT_PRESETS_DIR / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(payload, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info("preset saved name=%s", name)
    return {"ok": True, "message": f"已保存预设 {name}"}


@router.delete("/presets/{name}")
def delete_preset(name: str) -> dict[str, Any]:
    from generator.c_pipeline.preset_loader import DEFAULT_PRESETS_DIR
    if name == "default":
        raise HTTPException(status_code=400, detail="不能删除默认预设")
    path = DEFAULT_PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"预设不存在：{name}")
    path.unlink()
    logger.info("preset deleted name=%s", name)
    return {"ok": True, "message": f"已删除预设 {name}"}


@router.post("/presets/{name}/activate")
def activate_preset(name: str) -> dict[str, Any]:
    """切换当前活跃预设。"""
    from review_queue.yaml_writer import load_yaml, save_yaml
    cfg = load_yaml(_config_path())
    cfg.setdefault("c_pipeline", {})
    cfg["c_pipeline"]["active_preset"] = name
    save_yaml(_config_path(), cfg)
    logger.info("active_preset switched to %s", name)
    return {"ok": True, "message": f"已切换到预设 {name}", "active": name}


# ============================================================================
# Step generation — LLM 辅助生成自定义步骤
# ============================================================================

_STEP_GEN_PROMPT_FILE = _PROMPTS_DIR / "step_generator.txt"


@router.post("/steps/generate")
async def generate_step(request: Request) -> dict[str, Any]:
    """LLM 根据用户描述生成一个自定义步骤的 action chain。
    body: {"description": "检查对话质量", "existing_steps": "phase_0, phase_1, ..."}
    """
    payload = await _json_payload(request)
    description = str(payload.get("description") or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="description 不能为空")
    existing = str(payload.get("existing_steps") or "")

    import string as _string
    template_str = _STEP_GEN_PROMPT_FILE.read_text(encoding="utf-8")
    template = _string.Template(template_str)
    original_step = payload.get("original_step")
    user_prompt = template.safe_substitute(user_request=description, existing_steps=existing)
    if original_step:
        import json as _json2
        user_prompt += (
            "\n\n# 改写模式 — 请基于以下现有步骤进行修改\n"
            "保持 id 不变（除非用户明确要求改名），保留与用户描述无关的字段：\n"
            "```json\n" + _json2.dumps(original_step, ensure_ascii=False, indent=2) + "\n```\n"
            f"用户的修改要求：{description}"
        )

    try:
        from generator.api_client import DeepSeekClient
        config = load_from_environment()
        client = DeepSeekClient(config)
        messages = [
            {"role": "system", "content": "你是 ANP 流水线步骤设计专家。只输出 JSON。"},
            {"role": "user", "content": user_prompt},
        ]
        completion = client.chat_completion(messages, thinking_mode=False, purpose="step_generate")
        raw = (completion.text or "").strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:]) if len(lines) > 1 else raw
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        import json as _json
        step_spec = _json.loads(raw)
        if not isinstance(step_spec, dict):
            raise ValueError("LLM returned non-dict")
        if "actions" not in step_spec:
            step_spec["actions"] = []
        if "id" not in step_spec:
            step_spec["id"] = "custom_" + _json.dumps(description[:20])
        step_spec.setdefault("type", "custom")
        step_spec.setdefault("label", description[:30])
        step_spec.setdefault("enabled", True)
        step_spec.setdefault("pause_after", False)
        if original_step and original_step.get("id"):
            step_spec["id"] = original_step["id"]
        logger.info("step_generated id=%s actions=%s", step_spec.get("id"), len(step_spec.get("actions", [])))
        return {"ok": True, "step": step_spec, "message": f"生成了 {len(step_spec.get('actions',[]))} 个 action"}
    except _json.JSONDecodeError as exc:
        logger.warning("step_generate JSON parse failed: %s", str(exc)[:200])
        return {"ok": False, "message": f"LLM 返回了非 JSON 内容，请重试或简化描述。原始返回：{raw[:500]}", "raw": raw[:1000]}
    except Exception as exc:
        logger.exception("step_generate failed")
        raise HTTPException(status_code=500, detail=f"生成失败：{exc}")


def _config_path() -> Path:
    import os
    from pathlib import Path
    raw = os.getenv("ANP_CONFIG")
    return Path(raw) if raw else Path("config.yaml")


__all__ = ["router"]
