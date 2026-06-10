"""``/api/settings/*`` 后端：消灭"必须改 YAML"的核心 API。

提供配置编辑 API，覆盖生成、审核、系统、通知和模式切换。
所有 secret 字段在 GET 时遮罩 (``mask_secret``)；POST 收到遮罩格式时保留服
务器现有值 —— 用户不修改不写盘。

全部写盘走 :mod:`review_queue.yaml_writer` 与 :mod:`review_queue.env_writer`,
保留注释 / 顺序 / 缩进。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import socket
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from config_loader import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DOTENV_PATH,
    get_env,
    load_from_environment,
)
from generator.api_client import _join_endpoint, _normalize_protocol, provider_defaults
from review_queue.env_writer import read_env, write_env_fields
from review_queue.yaml_writer import load_yaml, save_yaml, update_yaml_field

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
    raw = get_env("ANW_CONFIG")
    return Path(raw) if raw else DEFAULT_CONFIG_PATH


def _dotenv_path() -> Path:
    raw = get_env("ANW_DOTENV")
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


LLM_PROVIDERS = ("deepseek", "openai", "google", "anthropic", "zhipu", "qwen", "xiaomi", "custom")


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
        "mimo": "xiaomi",
        "xiaomimimo": "xiaomi",
        "xiaomi-mimo": "xiaomi",
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
            "model": provider_defaults(provider)["model"],
            "flash_model": provider_defaults(provider)["flash_model"],
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
        "flash_model": (
            env.get("LLM_FLASH_MODEL") or env.get("DEEPSEEK_FLASH_MODEL") or str(cfg_deepseek.get("flash_model") or defaults["flash_model"])
        ),
    }


def _persist_generation_probe_payload(payload: dict[str, Any]) -> None:
    """Persist settings submitted by test/discovery buttons before probing."""
    env_updates: dict[str, str] = {}
    if "provider" in payload:
        env_updates["LLM_PROVIDER"] = _normalize_provider(payload.get("provider"))
    if "protocol" in payload:
        env_updates["LLM_PROTOCOL"] = _normalize_protocol(str(payload.get("protocol") or "openai"))
    raw_key = str(payload.get("api_key") or "").strip()
    if raw_key and not is_masked(raw_key):
        env_updates["LLM_API_KEY"] = raw_key
        env_updates["DEEPSEEK_API_KEY"] = raw_key
    if "base_url" in payload:
        base_url = str(payload.get("base_url") or "").strip()
        env_updates["LLM_BASE_URL"] = base_url
        env_updates["DEEPSEEK_BASE_URL"] = base_url
    if "model" in payload:
        model = str(payload.get("model") or "").strip()
        env_updates["LLM_MODEL"] = model
        env_updates["DEEPSEEK_MODEL"] = model
    if "flash_model" in payload:
        flash_model = str(payload.get("flash_model") or "").strip()
        env_updates["LLM_FLASH_MODEL"] = flash_model
        env_updates["DEEPSEEK_FLASH_MODEL"] = flash_model
    if env_updates:
        write_env_fields(_dotenv_path(), env_updates.items())


def _versioned_base_candidate(base_url: str) -> str | None:
    """Return base_url + /v1 when the URL has no version-like path segment."""
    base = str(base_url or "").rstrip("/")
    if not base:
        return None
    parsed = urlparse(base)
    path_segments = [segment.lower() for segment in parsed.path.split("/") if segment]
    if any(segment.startswith("v") and any(ch.isdigit() for ch in segment) for segment in path_segments):
        return None
    return base + "/v1"


def _persist_corrected_base_url(base_url: str) -> None:
    write_env_fields(
        _dotenv_path(),
        [
            ("LLM_BASE_URL", base_url),
            ("DEEPSEEK_BASE_URL", base_url),
        ],
    )


def _fake_ip_notice(base_url: str) -> str:
    host = urlparse(str(base_url or "")).hostname or ""
    if not host:
        return ""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return ""
    ips = sorted({str(info[4][0]) for info in infos if info and info[4]})
    if any(ip.startswith("198.18.") or ip.startswith("198.19.") for ip in ips):
        return (
            f" 当前域名解析到 Clash/Mihomo Fake-IP（{', '.join(ips[:3])}），说明仍在 TUN/Fake-IP 规则内；"
            f"请在规则里加 DOMAIN, {host}, DIRECT，并刷新 DNS/重启 Clash。"
        )
    return ""


def _http_error_message(resp: Any, base_url: str) -> str:
    text = str(getattr(resp, "text", "") or "")
    content_type = str(getattr(resp, "headers", {}).get("Content-Type", "") or "").lower()
    server = str(getattr(resp, "headers", {}).get("Server", "") or "").lower()
    status_code = int(getattr(resp, "status_code", 0) or 0)
    html_title = ""
    if "<html" in text.lower() or "text/html" in content_type:
        match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        if match:
            html_title = re.sub(r"\s+", " ", match.group(1)).strip()
    notice = _fake_ip_notice(base_url)
    if "cloudflare" in server or "cloudflare" in text.lower() or html_title:
        title = f"（{html_title}）" if html_title else ""
        return f"HTTP {status_code}: 返回的是网页拦截页{title}，不是 API JSON。{notice}".strip()
    return f"HTTP {status_code}: {text[:200]}"


def _should_try_versioned_base(resp: Any) -> bool:
    status_code = int(getattr(resp, "status_code", 0) or 0)
    if status_code in {403, 404, 405}:
        return True
    text = str(getattr(resp, "text", "") or "").lower()
    content_type = str(getattr(resp, "headers", {}).get("Content-Type", "") or "").lower()
    return "text/html" in content_type or "<html" in text


@router.get("/generation")
def get_generation() -> dict[str, Any]:
    cfg = load_yaml(_config_path())
    env = read_env(_dotenv_path())
    d = cfg.get("deepseek") or {}
    values = _generation_env_values(env, d)
    return {
        "has_api_key": bool(values["api_key"]),
        "api_key_masked": mask_secret(values["api_key"]) if values["api_key"] else "",
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
    _persist_generation_probe_payload(payload)
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

        def _probe(candidate_base: str):
            if protocol == "anthropic":
                return httpx.post(
                    _join_endpoint(candidate_base, "messages"),
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
                    trust_env=False,
                )
            return httpx.post(
                _join_endpoint(candidate_base, "chat/completions"),
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
                trust_env=False,
            )

        corrected_base = None
        resp = _probe(base_url)
        candidate_base = _versioned_base_candidate(base_url) if protocol != "anthropic" else None
        if candidate_base and _should_try_versioned_base(resp):
            retry_resp = _probe(candidate_base)
            if 200 <= retry_resp.status_code < 300:
                resp = retry_resp
                corrected_base = candidate_base
    except Exception as exc:  # pragma: no cover - network errors are environment-specific
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    if 200 <= resp.status_code < 300:
        if corrected_base:
            _persist_corrected_base_url(corrected_base)
            return {"ok": True, "message": f"连接成功，已自动补全 Base URL：{corrected_base}"}
        return {"ok": True, "message": "连接成功"}
    return {
        "ok": False,
        "message": _http_error_message(resp, base_url),
    }


def _extract_model_names(payload: Any) -> list[str]:
    """Read model ids from common OpenAI-compatible and Anthropic list responses."""
    items: Any = payload
    if isinstance(payload, dict):
        items = payload.get("data")
        if not isinstance(items, list):
            items = payload.get("models")
    if not isinstance(items, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
        else:
            name = ""
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


@router.post("/generation/models")
async def discover_generation_models(request: Request) -> dict[str, Any]:
    """Discover models exposed by the configured official API or relay."""
    payload = await _json_payload(request)
    _persist_generation_probe_payload(payload)
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
    if not base_url:
        return {"ok": False, "models": [], "message": "Base URL 为空，请先填写接口地址"}
    if not raw_key:
        return {"ok": False, "models": [], "message": "API key 为空，请先填写"}

    headers = {"Accept": "application/json"}
    if protocol == "anthropic":
        headers.update({"x-api-key": raw_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {raw_key}"
    try:
        import httpx

        corrected_base = None
        response = httpx.get(_join_endpoint(base_url, "models"), headers=headers, timeout=15.0, trust_env=False)
        candidate_base = _versioned_base_candidate(base_url) if protocol != "anthropic" else None
        if candidate_base and _should_try_versioned_base(response):
            retry_response = httpx.get(
                _join_endpoint(candidate_base, "models"),
                headers=headers,
                timeout=15.0,
                trust_env=False,
            )
            if 200 <= retry_response.status_code < 300:
                response = retry_response
                corrected_base = candidate_base
    except Exception as exc:  # pragma: no cover - network errors are environment-specific
        return {"ok": False, "models": [], "message": f"{type(exc).__name__}: {exc}"}
    if not 200 <= response.status_code < 300:
        return {
            "ok": False,
            "models": [],
            "message": _http_error_message(response, base_url),
        }
    try:
        response_payload = response.json()
    except Exception:
        try:
            response_payload = json.loads(response.text)
        except Exception:
            response_payload = {}
    models = _extract_model_names(response_payload)
    if models and corrected_base:
        _persist_corrected_base_url(corrected_base)
        return {"ok": True, "models": models, "message": f"已识别 {len(models)} 个可用模型，并自动补全 Base URL：{corrected_base}"}
    if not models:
        return {"ok": False, "models": [], "message": "接口已连通，但没有返回可选模型；仍可手动填写"}
    return {"ok": True, "models": models, "message": f"已识别 {len(models)} 个可用模型"}


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
# 系统
# ============================================================================


@router.get("/system")
def get_system() -> dict[str, Any]:
    cfg = load_yaml(_config_path())
    db = cfg.get("database") or {}
    log = cfg.get("logging") or {}
    cost = cfg.get("cost_limits") or {}
    return {
        "database_path": str(db.get("sqlite_path") or "data/anw.sqlite3"),
        "backup_dir": str(db.get("backup_dir") or "data/backups"),
        "daily_backup": bool(db.get("daily_backup", True)),
        "log_path": str(log.get("file") or "logs/anw.log"),
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
        target = Path(str((cfg.get("database") or {}).get("sqlite_path") or "data/anw.sqlite3")).parent
    elif kind == "logs":
        target = Path(str((cfg.get("logging") or {}).get("file") or "logs/anw.log")).parent
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
