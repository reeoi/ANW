"""``/api/settings/*`` 后端：消灭"必须改 YAML"的核心 API。

按 ``docs/VISUAL_REBUILD_PLAN.md`` §1.2 拆成 6 大节 + 番茄登录 + 模式切换。
所有 secret 字段在 GET 时遮罩 (``mask_secret``)；POST 收到遮罩格式时保留服
务器现有值 —— 用户不修改不写盘。

全部写盘走 :mod:`review_queue.yaml_writer` 与 :mod:`review_queue.env_writer`,
保留注释 / 顺序 / 缩进。
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config_loader import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DOTENV_PATH,
    load_from_environment,
)
from review_queue.env_writer import read_env, write_env_fields
from review_queue import login_capture
from review_queue.yaml_writer import load_yaml, save_yaml, update_yaml_field
from generator.api_client import _join_endpoint, _normalize_protocol, provider_defaults

logger = logging.getLogger(__name__)


# ============================================================================
# 工具
# ============================================================================

# 7 维度审核权重默认值（与 config.yaml 模板一致）。
WEIGHT_KEYS = (
    "plot",
    "character",
    "pacing",
    "language",
    "originality",
    "safety",
    "platform_fit",
)


def mask_secret(value: str) -> str:
    """头尾露 6+4 字符。短串全用 ``•``。空串原样返回。"""
    if not value:
        return ""
    if len(value) < 12:
        return "•" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def is_masked(value: str) -> bool:
    """是否是 :func:`mask_secret` 产生的遮罩字符串。"""
    if not value or "..." not in value:
        return False
    head, _, tail = value.partition("...")
    return len(head) == 6 and len(tail) == 4 and "•" not in value


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON 请求体必须是对象")
    return payload


def _config_path() -> Path:
    raw = os.getenv("ANP_CONFIG")
    return Path(raw) if raw else DEFAULT_CONFIG_PATH


def _dotenv_path() -> Path:
    raw = os.getenv("ANP_DOTENV")
    return Path(raw) if raw else DEFAULT_DOTENV_PATH


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _validate_weights(weights: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in WEIGHT_KEYS:
        if key not in weights:
            raise HTTPException(status_code=400, detail=f"缺少维度权重：{key}")
        try:
            v = float(weights[key])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"维度 {key} 权重不是数字") from None
        if v < 0 or v > 1:
            raise HTTPException(status_code=400, detail=f"维度 {key} 权重必须在 0~1 之间")
        out[key] = round(v, 4)
    total = sum(out.values())
    if abs(total - 1.0) > 0.005:
        raise HTTPException(
            status_code=400,
            detail=f"7 维度权重总和应为 1.0（当前 {round(total, 3)}）",
        )
    return out


def _intersect_dict(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """把 updates 中存在的键写入 base，返回 base（in-place）。"""
    for k, v in updates.items():
        base[k] = v
    return base


# ============================================================================
# 调度
# ============================================================================

router = APIRouter(prefix="/api/settings", tags=["settings"])


LLM_PROVIDERS = ("deepseek", "openai", "google", "anthropic", "zhipu", "qwen", "custom")


def _normalize_provider(value: Any) -> str:
    provider = str(value or "deepseek").strip().lower()
    aliases = {
        "gemini": "google",
        "google-gemini": "google",
        "claude": "anthropic",
        "zhipuai": "zhipu",
        "bigmodel": "zhipu",
        "dashscope": "qwen",
        "aliyun": "qwen",
        "openai-compatible": "custom",
    }
    provider = aliases.get(provider, provider)
    return provider if provider in LLM_PROVIDERS else "custom"


def _provider_presets() -> list[dict[str, str]]:
    return [
        {
            "id": provider,
            "label": provider_defaults(provider)["label"],
            "protocol": provider_defaults(provider)["protocol"],
            "base_url": provider_defaults(provider)["base_url"],
        }
        for provider in LLM_PROVIDERS
    ]


def _generation_env_values(env: dict[str, str], cfg_deepseek: dict[str, Any]) -> dict[str, str]:
    provider = _normalize_provider(env.get("LLM_PROVIDER") or cfg_deepseek.get("provider") or "deepseek")
    defaults = provider_defaults(provider)
    return {
        "provider": provider,
        "protocol": _normalize_protocol(env.get("LLM_PROTOCOL") or cfg_deepseek.get("protocol") or defaults["protocol"]),
        "api_key": env.get("LLM_API_KEY") or env.get("DEEPSEEK_API_KEY") or str(cfg_deepseek.get("api_key") or ""),
        "base_url": env.get("LLM_BASE_URL") or env.get("DEEPSEEK_BASE_URL") or str(cfg_deepseek.get("base_url") or defaults["base_url"]),
        "model": env.get("LLM_MODEL") or env.get("DEEPSEEK_MODEL") or str(cfg_deepseek.get("model") or defaults["model"]),
        "flash_model": env.get("LLM_FLASH_MODEL") or env.get("DEEPSEEK_FLASH_MODEL") or str(cfg_deepseek.get("flash_model") or defaults["flash_model"]),
    }


@router.get("/generation")
def get_generation() -> dict[str, Any]:
    cfg = load_yaml(_config_path())
    env = read_env(_dotenv_path())
    d = cfg.get("deepseek") or {}
    values = _generation_env_values(env, d)
    return {
        "has_api_key": bool(values["api_key"]),
        "provider": values["provider"],
        "protocol": values["protocol"],
        "providers": _provider_presets(),
        "base_url": values["base_url"],
        "model": values["model"],
        "flash_model": values["flash_model"],
    }


@router.post("/generation")
async def set_generation(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    env_updates: dict[str, str] = {}
    if "provider" in payload:
        env_updates["LLM_PROVIDER"] = _normalize_provider(payload.get("provider"))
    if "protocol" in payload:
        env_updates["LLM_PROTOCOL"] = _normalize_protocol(str(payload.get("protocol") or "openai"))
    if "api_key" in payload and payload["api_key"] is not None:
        new_key = str(payload["api_key"]).strip()
        if new_key and not is_masked(new_key):
            env_updates["LLM_API_KEY"] = new_key
            env_updates["DEEPSEEK_API_KEY"] = new_key
    if "base_url" in payload:
        base_url = str(payload["base_url"]).strip()
        env_updates["LLM_BASE_URL"] = base_url
        env_updates["DEEPSEEK_BASE_URL"] = base_url
    if "model" in payload:
        model = str(payload["model"]).strip()
        env_updates["LLM_MODEL"] = model
        env_updates["DEEPSEEK_MODEL"] = model
    if "flash_model" in payload:
        flash_model = str(payload["flash_model"]).strip()
        env_updates["LLM_FLASH_MODEL"] = flash_model
        env_updates["DEEPSEEK_FLASH_MODEL"] = flash_model
    if env_updates:
        write_env_fields(_dotenv_path(), env_updates.items())

    return {"ok": True, "message": "生成配置已保存"}


@router.post("/generation/test")
async def test_generation(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    env = read_env(_dotenv_path())
    cfg = load_yaml(_config_path())
    deepseek = cfg.get("deepseek") or {}

    provider = _normalize_provider(payload.get("provider") or env.get("LLM_PROVIDER") or deepseek.get("provider") or "deepseek")
    defaults = provider_defaults(provider)
    protocol = _normalize_protocol(str(payload.get("protocol") or env.get("LLM_PROTOCOL") or deepseek.get("protocol") or defaults["protocol"]))

    raw_key = str(payload.get("api_key") or "").strip()
    if not raw_key or is_masked(raw_key):
        raw_key = env.get("LLM_API_KEY") or env.get("DEEPSEEK_API_KEY") or str(deepseek.get("api_key") or "")
    base_url = str(
        payload.get("base_url")
        or env.get("LLM_BASE_URL")
        or env.get("DEEPSEEK_BASE_URL")
        or deepseek.get("base_url")
        or defaults["base_url"]
    ).rstrip("/")
    model = str(
        payload.get("model")
        or env.get("LLM_MODEL")
        or env.get("DEEPSEEK_MODEL")
        or deepseek.get("model")
        or defaults["model"]
    )
    if not base_url:
        return {"ok": False, "message": "Base URL 为空，请填写接口地址"}
    if not model:
        return {"ok": False, "message": "模型名称为空，请填写模型名"}
    if not raw_key:
        return {"ok": False, "message": "API key 为空，请先填写"}

    try:
        import httpx

        if protocol == "anthropic":
            endpoint = _join_endpoint(base_url, "messages")
            resp = httpx.post(
                endpoint,
                headers={
                    "x-api-key": raw_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                timeout=15.0,
            )
        else:
            endpoint = _join_endpoint(base_url, "chat/completions")
            resp = httpx.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {raw_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                timeout=15.0,
            )
    except Exception as exc:  # pragma: no cover - network errors are environment-specific
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    if 200 <= resp.status_code < 300:
        return {"ok": True, "message": "连接成功"}
    return {
        "ok": False,
        "message": f"HTTP {resp.status_code}: {resp.text[:200]}",
    }


# ============================================================================
# 审核
# ============================================================================


@router.get("/audit")
def get_audit() -> dict[str, Any]:
    cfg = load_yaml(_config_path())
    a = cfg.get("audit") or {}
    dims = a.get("dimensions") or {}
    weights: dict[str, float] = {}
    for k in WEIGHT_KEYS:
        try:
            weights[k] = float(dims.get(k) or 0)
        except (TypeError, ValueError):
            weights[k] = 0.0
    return {
        "approval_threshold": int(a.get("approval_threshold") or 85),
        "rewrite_threshold": int(a.get("rewrite_threshold") or 70),
        "max_rewrite_attempts": int(a.get("max_rewrite_attempts") or 3),
        "weights": weights,
    }


@router.post("/audit")
async def set_audit(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    cfg = load_yaml(_config_path())
    cfg.setdefault("audit", {})
    a = cfg["audit"]
    if "approval_threshold" in payload:
        try:
            v = int(payload["approval_threshold"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="通过分数必须是整数") from None
        if v < 0 or v > 100:
            raise HTTPException(status_code=400, detail="通过分数必须在 0~100")
        a["approval_threshold"] = v
    if "rewrite_threshold" in payload:
        try:
            v = int(payload["rewrite_threshold"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="重写阈值必须是整数") from None
        if v < 0 or v > 100:
            raise HTTPException(status_code=400, detail="重写阈值必须在 0~100")
        a["rewrite_threshold"] = v
    if (
        "approval_threshold" in payload
        and "rewrite_threshold" in payload
        and a["rewrite_threshold"] > a["approval_threshold"]
    ):
        raise HTTPException(status_code=400, detail="重写阈值不能高于通过分数")
    if "max_rewrite_attempts" in payload:
        try:
            v = int(payload["max_rewrite_attempts"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="最大重写次数必须是整数") from None
        if v < 0 or v > 10:
            raise HTTPException(status_code=400, detail="最大重写次数必须在 0~10")
        a["max_rewrite_attempts"] = v
    if "weights" in payload:
        if not isinstance(payload["weights"], dict):
            raise HTTPException(status_code=400, detail="weights 必须是对象")
        normalized = _validate_weights(payload["weights"])
        a.setdefault("dimensions", {})
        for k in WEIGHT_KEYS:
            a["dimensions"][k] = normalized[k]
    save_yaml(_config_path(), cfg)
    return {"ok": True, "message": "审核配置已保存"}


# ============================================================================
# 发布 (含番茄登录)
# ============================================================================


def _get_login_state() -> dict[str, Any]:
    """Return CDP status + login state file validity."""
    fansq_cfg = load_yaml(_config_path()).get("publisher", {}).get("fansq", {})
    state_path_str = str(fansq_cfg.get("login_state_path") or "").strip()

    # CDP readiness check
    cdp_ok = login_capture.is_cdp_ready(timeout=0.5)

    # Login state file validity
    current_state_path = login_capture.state_file()
    if not current_state_path.exists():
        if cdp_ok:
            return {"status": "cdp_active", "state_path": str(current_state_path)}
        return {"status": "chrome_offline", "state_path": str(current_state_path)}

    validity = login_capture.login_state_validity(current_state_path)
    return {
        "status": validity["status"],
        "days_left": validity["days_left"],
        "state_path": str(current_state_path),
    }


def _get_login_session() -> dict[str, Any]:
    """Return the current login session status."""
    session = login_capture.get_session_status()
    return {
        "status": session.get("status", "none"),
        "task_id": session.get("task_id"),
        "started_at": session.get("started_at"),
    }


@router.get("/publish")
def get_publish() -> dict[str, Any]:
    cfg = load_yaml(_config_path())
    f = cfg.get("publisher", {}).get("fansq", {})
    return {
        "login_state": _get_login_state(),
        "login_session": _get_login_session(),
        "draft_url": str(f.get("draft_url") or "https://fanqienovel.com/"),
        "min_publish_interval_minutes": int(f.get("min_publish_interval_minutes") or 5),
        "max_publish_interval_minutes": int(f.get("max_publish_interval_minutes") or 15),
        "pause_on_risk_control": bool(f.get("pause_on_risk_control", True)),
        "login_state_path": str(f.get("login_state_path") or ""),
    }


@router.post("/publish")
async def post_publish(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    cfg = load_yaml(_config_path())
    fansq = cfg.setdefault("publisher", {}).setdefault("fansq", {})

    if "draft_url" in payload:
        raw = str(payload["draft_url"]).strip()
        if raw and not raw.startswith("http"):
            raise HTTPException(status_code=400, detail="draft_url 必须是 http(s):// 开头")
        fansq["draft_url"] = raw or "https://fanqienovel.com/"

    for key in ("min_publish_interval_minutes", "max_publish_interval_minutes"):
        if key in payload:
            try:
                v = int(payload[key])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"{key} 必须是整数")
            if v < 0:
                raise HTTPException(status_code=400, detail=f"{key} 不能为负")
            fansq[key] = v

    mi = int(fansq.get("min_publish_interval_minutes") or 5)
    ma = int(fansq.get("max_publish_interval_minutes") or 15)
    if mi > ma:
        raise HTTPException(status_code=400, detail="最小间隔不能大于最大间隔")

    if "pause_on_risk_control" in payload:
        fansq["pause_on_risk_control"] = _to_bool(payload["pause_on_risk_control"])

    save_yaml(_config_path(), cfg)
    return {"ok": True, "message": "发布配置已保存"}


# --- 番茄登录子端点 ---


@router.post("/publish/login")
async def post_login() -> dict[str, Any]:
    """Start a login session (opens a browser login page)."""
    result = login_capture.start_login_session()
    return result


@router.get("/publish/login/status")
async def get_login_status() -> dict[str, Any]:
    """Return login session status + login state info."""
    return {
        "session": _get_login_session(),
        "login_state": _get_login_state(),
    }


@router.post("/publish/login/cancel")
async def post_login_cancel() -> dict[str, Any]:
    """Cancel the current login session."""
    return login_capture.cancel_login_session()


@router.post("/publish/login/finish")
async def post_login_finish() -> dict[str, Any]:
    """Wait for the login worker to finish and validate the state."""
    return login_capture.finish_login_session(timeout_seconds=30)


# ============================================================================
# 系统
# ============================================================================


@router.get("/system")
def get_system() -> dict[str, Any]:
    cfg = load_yaml(_config_path())
    db = cfg.get("database") or {}
    log = cfg.get("logging") or {}
    cost = cfg.get("cost_limits") or {}
    return {
        "database_path": str(db.get("sqlite_path") or "data/anp.sqlite3"),
        "backup_dir": str(db.get("backup_dir") or "data/backups"),
        "daily_backup": bool(db.get("daily_backup", True)),
        "log_path": str(log.get("file") or "logs/anp.log"),
        "log_level": str(log.get("level") or "INFO"),
        "log_json": bool(log.get("json", False)),
        "monthly_budget_cny": float(cost.get("monthly_budget_cny") or 0),
        "daily_token_limit": int(cost.get("daily_token_limit") or 0),
        "stop_when_exceeded": bool(cost.get("stop_when_exceeded", True)),
    }


@router.post("/system")
async def set_system(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    cfg = load_yaml(_config_path())
    cfg.setdefault("logging", {})
    cfg.setdefault("database", {})
    cfg.setdefault("cost_limits", {})
    if "log_level" in payload:
        level = str(payload["log_level"]).strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise HTTPException(status_code=400, detail="日志级别必须是 DEBUG/INFO/WARNING/ERROR")
        cfg["logging"]["level"] = level
    if "log_json" in payload:
        cfg["logging"]["json"] = _to_bool(payload["log_json"])
    if "daily_backup" in payload:
        cfg["database"]["daily_backup"] = _to_bool(payload["daily_backup"])
    if "monthly_budget_cny" in payload:
        try:
            v = float(payload["monthly_budget_cny"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="月预算必须是数字") from None
        if v < 0:
            raise HTTPException(status_code=400, detail="月预算必须非负")
        cfg["cost_limits"]["monthly_budget_cny"] = v
    if "daily_token_limit" in payload:
        try:
            v = int(payload["daily_token_limit"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="每日 token 上限必须是整数") from None
        if v < 0:
            raise HTTPException(status_code=400, detail="每日 token 上限必须非负")
        cfg["cost_limits"]["daily_token_limit"] = v
    if "stop_when_exceeded" in payload:
        cfg["cost_limits"]["stop_when_exceeded"] = _to_bool(payload["stop_when_exceeded"])
    save_yaml(_config_path(), cfg)
    return {"ok": True, "message": "系统配置已保存"}


@router.post("/system/open-folder")
async def open_folder(request: Request) -> dict[str, Any]:
    """在资源管理器中打开数据 / 日志文件夹。"""
    payload = await _json_payload(request)
    kind = str(payload.get("kind") or "").strip()
    cfg = load_yaml(_config_path())
    if kind == "data":
        target = Path(str((cfg.get("database") or {}).get("sqlite_path") or "data/anp.sqlite3")).parent
    elif kind == "logs":
        target = Path(str((cfg.get("logging") or {}).get("file") or "logs/anp.log")).parent
    elif kind == "backup":
        target = Path(str((cfg.get("database") or {}).get("backup_dir") or "data/backups"))
    else:
        raise HTTPException(status_code=400, detail="kind 必须是 data / logs / backup")
    target.mkdir(parents=True, exist_ok=True)
    abs_path = target.resolve()
    try:
        if platform.system() == "Windows":
            os.startfile(str(abs_path))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(abs_path)])
        else:
            subprocess.Popen(["xdg-open", str(abs_path)])
    except Exception as exc:  # pragma: no cover - depends on OS
        return {"ok": False, "message": f"打开失败：{exc}", "path": str(abs_path)}
    return {"ok": True, "path": str(abs_path)}


# ============================================================================
# 通知 (第 1 期占位，第 2 期实现真实通知行为)
# ============================================================================


@router.get("/notifications")
def get_notifications() -> dict[str, Any]:
    cfg = load_yaml(_config_path())
    n = cfg.get("notifications") or {}
    return {
        "critical_enabled": bool(n.get("critical_enabled", True)),
        "warning_enabled": bool(n.get("warning_enabled", True)),
        "info_enabled": bool(n.get("info_enabled", False)),
        "quiet_hours_start": str(n.get("quiet_hours_start") or ""),
        "quiet_hours_end": str(n.get("quiet_hours_end") or ""),
    }


@router.post("/notifications")
async def set_notifications(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    cfg = load_yaml(_config_path())
    cfg.setdefault("notifications", {})
    n = cfg["notifications"]
    for key in ("critical_enabled", "warning_enabled", "info_enabled"):
        if key in payload:
            n[key] = _to_bool(payload[key])
    for key in ("quiet_hours_start", "quiet_hours_end"):
        if key in payload:
            v = str(payload[key]).strip()
            if v and not _is_hhmm(v):
                raise HTTPException(status_code=400, detail=f"{key} 必须是 HH:MM 格式")
            n[key] = v
    save_yaml(_config_path(), cfg)
    return {"ok": True, "message": "通知配置已保存"}


def _is_hhmm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59


# ============================================================================
# 聚合
# ============================================================================


@router.get("")
def get_all_settings() -> dict[str, Any]:
    """一次性返回所有配置节，给前端首屏用。"""
    return {
        "generation": get_generation(),
        "audit": get_audit(),
        "publish": get_publish(),
        "system": get_system(),
        "notifications": get_notifications(),
    }


# ============================================================================
# 模式切换 (挂在 /api/mode 而非 /api/settings/mode)
# ============================================================================

mode_router = APIRouter(prefix="/api", tags=["mode"])


@mode_router.get("/mode")
def get_mode() -> dict[str, Any]:
    config = load_from_environment()
    runtime = config.data.get("runtime") or {}
    return {
        "dry_run": bool(config.is_dry_run),
        "mode": str(runtime.get("mode") or "semi-auto"),
        "warnings": list(config.warnings or []),
    }


@mode_router.post("/mode")
async def set_mode(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    if "dry_run" not in payload:
        raise HTTPException(status_code=400, detail="缺少 dry_run 字段")
    new_dry_run = _to_bool(payload["dry_run"])
    if not new_dry_run:
        confirmations = set(payload.get("confirmations") or [])
        required = {"login_state_ready", "accept_consequences"}
        missing = required - confirmations
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"切换到真实模式需要确认: {sorted(missing)}",
            )
    update_yaml_field(_config_path(), "runtime.dry_run", new_dry_run)
    if "mode" in payload:
        new_mode = str(payload["mode"]).strip()
        if new_mode not in {"auto", "semi-auto"}:
            raise HTTPException(status_code=400, detail="mode 必须是 auto 或 semi-auto")
        update_yaml_field(_config_path(), "runtime.mode", new_mode)
    logger.info("Mode changed via UI: dry_run=%s", new_dry_run)
    return {"ok": True, "dry_run": new_dry_run}


__all__ = [
    "WEIGHT_KEYS",
    "is_masked",
    "mask_secret",
    "mode_router",
    "router",
]
