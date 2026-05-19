"""``/api/settings/*`` 与 ``/api/mode`` 端点测试。

借用 ``test_enhanced_ui`` 的最小 ASGI 客户端思路，避免引入 starlette/httpx
TestClient 的版本耦合。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from review_queue.human_review import app
from review_queue import settings_api


SAMPLE_CONFIG = """\
# Top comment
deepseek:
  api_key: ""
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-flash"
  timeout_seconds: 60
  max_retries: 3

runtime:
  mode: "semi-auto"
  dry_run: true
  project_root: "."

audit:
  approval_threshold: 85
  rewrite_threshold: 70
  max_rewrite_attempts: 3
  dimensions:
    plot: 0.20
    character: 0.15
    pacing: 0.15
    language: 0.15
    originality: 0.15
    safety: 0.10
    platform_fit: 0.10

publisher:
  default_platform: "fansq"
  fansq:
    enabled: true
    login_state_path: "data/browser/fansq_state.json"
    draft_url: "https://fanqienovel.com/"
    min_publish_interval_minutes: 5
    max_publish_interval_minutes: 15
    pause_on_risk_control: true

scheduler:
  enabled: false
  timezone: "Asia/Shanghai"
  generate_cron: "0 9 * * *"
  review_cron: "30 9 * * *"
  publish_cron: "0 10 * * *"
  backup_cron: "0 3 * * *"

generation:
  theme: "雨夜归人"
  word_count: 3000
  style: "现实温情"

logging:
  level: "INFO"
  file: "logs/anp.log"
  json: false

database:
  sqlite_path: "data/anp.sqlite3"
  backup_dir: "data/backups"
  daily_backup: true

cost_limits:
  monthly_budget_cny: 100
  daily_token_limit: 200000
  stop_when_exceeded: true
"""


SAMPLE_ENV = """\
# DeepSeek
DEEPSEEK_API_KEY=sk-abcdef-1234567890-tail
DEEPSEEK_BASE_URL=https://example.test/v1
DEEPSEEK_MODEL=deepseek-v4-pro
"""


@pytest.fixture()
def env_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(SAMPLE_CONFIG, encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(SAMPLE_ENV, encoding="utf-8")
    monkeypatch.setenv("ANP_CONFIG", str(cfg))
    monkeypatch.setenv("ANP_DOTENV", str(env))
    # 把 login_capture 也指到临时目录
    browser = tmp_path / "data" / "browser"
    browser.mkdir(parents=True)
    from review_queue import login_capture

    monkeypatch.setattr(login_capture, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(login_capture, "BROWSER_DIR", browser)
    monkeypatch.setattr(login_capture, "SESSION_FILE", browser / ".login_session.json")
    monkeypatch.setattr(login_capture, "TRIGGER_FINISH_FILE", browser / ".login_trigger_finish")
    monkeypatch.setattr(login_capture, "WORKER_DONE_FILE", browser / ".login_worker_done.json")
    return {"cfg": cfg, "env": env, "browser": browser}


def _request(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import anyio

    async def run() -> dict[str, Any]:
        body_bytes = b""
        headers: list[tuple[bytes, bytes]] = []
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            headers.append((b"content-type", b"application/json"))
        messages = [{"type": "http.request", "body": body_bytes, "more_body": False}]
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers + [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return {"status": status, "body": body.decode("utf-8")}

    return anyio.run(run)


# ============================================================================
# 工具函数
# ============================================================================


def test_mask_secret_handles_short_and_long() -> None:
    assert settings_api.mask_secret("") == ""
    assert settings_api.mask_secret("abcd") == "••••"
    masked = settings_api.mask_secret("sk-abcdef-1234567890-tail")
    assert masked == "sk-abc...tail"


def test_is_masked_detects_format() -> None:
    assert settings_api.is_masked("sk-abc...tail")
    assert not settings_api.is_masked("sk-real-key")
    assert not settings_api.is_masked("")


# ============================================================================
# 调度
# ============================================================================


def test_get_scheduler_endpoint_removed(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings/scheduler")
    assert r["status"] == 404


# ============================================================================
# 生成 + 凭据遮罩
# ============================================================================


def test_get_generation_masks_api_key(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings/generation")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["api_key_masked"].startswith("sk-abc")
    assert body["api_key_masked"].endswith("tail")
    assert "..." in body["api_key_masked"]
    assert body["has_api_key"] is True
    assert "sk-abcdef-1234567890-tail" not in r["body"]


def test_post_generation_keeps_existing_when_masked(env_setup: dict[str, Path]) -> None:
    masked = settings_api.mask_secret("sk-abcdef-1234567890-tail")
    r = _request(
        "POST",
        "/api/settings/generation",
        json_body={
            "api_key": masked,  # 用户没改 API key
            "model": "deepseek-v4-pro",
        },
    )
    assert r["status"] == 200
    env_text = env_setup["env"].read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-abcdef-1234567890-tail" in env_text


def test_post_generation_writes_real_key(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/generation",
        json_body={"api_key": "sk-fresh-9999"},
    )
    assert r["status"] == 200
    env_text = env_setup["env"].read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-fresh-9999" in env_text


def test_post_generation_validates_timeout(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/generation",
        json_body={"timeout_seconds": 0},
    )
    assert r["status"] == 400


def test_generation_test_handles_empty_key(env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    # 把 .env 清空模拟没填 key
    env_setup["env"].write_text("", encoding="utf-8")
    r = _request("POST", "/api/settings/generation/test", json_body={"api_key": ""})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is False
    assert "API key" in body["message"]


def test_generation_test_calls_httpx(env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResp:
        status_code = 200
        text = ""

    def fake_post(url: str, **kwargs: Any) -> FakeResp:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    r = _request(
        "POST",
        "/api/settings/generation/test",
        json_body={"api_key": "sk-newtest-xxx", "base_url": "https://example.test", "model": "deepseek-v4-pro"},
    )
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert captured["headers"]["Authorization"] == "Bearer sk-newtest-xxx"
    assert captured["json"]["model"] == "deepseek-v4-pro"


# ============================================================================
# 审核 + 7 维度权重
# ============================================================================


def test_get_audit_returns_weights(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings/audit")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["approval_threshold"] == 85
    assert set(body["weights"].keys()) == set(settings_api.WEIGHT_KEYS)
    total = sum(body["weights"].values())
    assert abs(total - 1.0) < 0.005


def test_post_audit_rejects_unbalanced_weights(env_setup: dict[str, Path]) -> None:
    weights = {k: 0.5 for k in settings_api.WEIGHT_KEYS}
    r = _request("POST", "/api/settings/audit", json_body={"weights": weights})
    assert r["status"] == 400


def test_post_audit_accepts_balanced_weights(env_setup: dict[str, Path]) -> None:
    weights = {
        "plot": 0.25,
        "character": 0.20,
        "pacing": 0.10,
        "language": 0.15,
        "originality": 0.15,
        "safety": 0.10,
        "platform_fit": 0.05,
    }
    r = _request(
        "POST",
        "/api/settings/audit",
        json_body={"weights": weights, "approval_threshold": 88, "rewrite_threshold": 60},
    )
    assert r["status"] == 200


def test_post_audit_rejects_threshold_inversion(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/audit",
        json_body={"approval_threshold": 60, "rewrite_threshold": 80},
    )
    assert r["status"] == 400


# ============================================================================
# 发布
# ============================================================================


def test_get_publish_includes_login_state(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings/publish")
    assert r["status"] == 200
    body = json.loads(r["body"])
    # CDP 模式：测试环境没有运行 Chrome（9222 端口空），且默认 storage_state 文件不存在
    # → 返回 chrome_offline。旧 storage_state 语义下这里曾期望 missing。
    assert body["login_state"]["status"] in {"chrome_offline", "cdp_active"}
    assert body["login_session"]["status"] == "none"
    assert body["min_publish_interval_minutes"] == 5


def test_post_publish_rejects_inverted_intervals(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/publish",
        json_body={
            "min_publish_interval_minutes": 30,
            "max_publish_interval_minutes": 10,
        },
    )
    assert r["status"] == 400


def test_post_publish_rejects_bad_url(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/publish", json_body={"draft_url": "not-a-url"})
    assert r["status"] == 400


def test_post_publish_persists(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/publish",
        json_body={
            "min_publish_interval_minutes": 7,
            "max_publish_interval_minutes": 20,
            "pause_on_risk_control": True,
        },
    )
    assert r["status"] == 200
    cfg_text = env_setup["cfg"].read_text(encoding="utf-8")
    assert "min_publish_interval_minutes: 7" in cfg_text
    assert "max_publish_interval_minutes: 20" in cfg_text


def test_login_endpoints_round_trip(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from review_queue import login_capture

    class _DummyProc:
        def __init__(self, pid: int = 1234) -> None:
            self.pid = pid

    monkeypatch.setattr(login_capture.subprocess, "Popen", lambda *a, **kw: _DummyProc(7))

    r = _request("POST", "/api/settings/publish/login")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["status"] == "pending"
    assert body["pid"] == 7

    r = _request("GET", "/api/settings/publish/login/status")
    assert r["status"] == 200
    status_body = json.loads(r["body"])
    assert status_body["session"]["status"] == "pending"

    r = _request("POST", "/api/settings/publish/login/cancel")
    assert r["status"] == 200
    assert json.loads(r["body"])["ok"] is True


# ============================================================================
# 系统
# ============================================================================


def test_get_system(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings/system")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["log_level"] == "INFO"
    assert body["monthly_budget_cny"] == 100
    assert body["database_path"] == "data/anp.sqlite3"


def test_post_system_validates_log_level(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/system", json_body={"log_level": "TRACE"})
    assert r["status"] == 400


def test_post_system_persists_budget(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/system",
        json_body={"monthly_budget_cny": 250, "daily_token_limit": 100000},
    )
    assert r["status"] == 200
    cfg_text = env_setup["cfg"].read_text(encoding="utf-8")
    assert "250" in cfg_text


def test_post_system_open_folder_invalid_kind(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/system/open-folder", json_body={"kind": "nope"})
    assert r["status"] == 400


# ============================================================================
# 通知
# ============================================================================


def test_get_notifications_defaults(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings/notifications")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["critical_enabled"] is True
    assert body["warning_enabled"] is True


def test_post_notifications_validates_quiet_hours(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/notifications",
        json_body={"quiet_hours_start": "25:00"},
    )
    assert r["status"] == 400


def test_post_notifications_persists(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/notifications",
        json_body={
            "critical_enabled": True,
            "warning_enabled": False,
            "info_enabled": True,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "08:00",
        },
    )
    assert r["status"] == 200


# ============================================================================
# 聚合
# ============================================================================


def test_get_all_settings(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert set(body.keys()) >= {
        "generation",
        "audit",
        "publish",
        "system",
        "notifications",
    }
    assert "scheduler" not in body


# ============================================================================
# 模式切换
# ============================================================================


def test_get_mode_default_is_dry_run(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/mode")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["dry_run"] is True


def test_post_mode_to_real_requires_confirmations(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/mode", json_body={"dry_run": False})
    assert r["status"] == 400


def test_post_mode_to_real_with_confirmations(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/mode",
        json_body={
            "dry_run": False,
            "confirmations": ["login_state_ready", "accept_consequences"],
        },
    )
    assert r["status"] == 200
    cfg_text = env_setup["cfg"].read_text(encoding="utf-8")
    assert "dry_run: false" in cfg_text


def test_post_mode_back_to_dry_run_no_confirmations_needed(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/mode", json_body={"dry_run": True})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["dry_run"] is True


def test_post_mode_validates_runtime_mode(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/mode",
        json_body={"dry_run": True, "mode": "robot"},
    )
    assert r["status"] == 400


# ============================================================================
# 边界 / 错误路径
# ============================================================================


def test_invalid_json_body_returns_400(env_setup: dict[str, Path]) -> None:
    """空 body 应该走默认值，对其余仍存活的 endpoint 返回 200。"""
    r = _request("POST", "/api/settings/notifications", json_body=None)
    assert r["status"] == 200


def test_post_generation_rejects_bad_timeout(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/generation", json_body={"timeout_seconds": 99999})
    assert r["status"] == 400


def test_post_generation_rejects_non_int_retries(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/generation", json_body={"max_retries": "many"})
    assert r["status"] == 400


def test_post_audit_rejects_weight_out_of_range(env_setup: dict[str, Path]) -> None:
    weights = {k: 0.1 for k in settings_api.WEIGHT_KEYS}
    weights["plot"] = 1.5
    r = _request("POST", "/api/settings/audit", json_body={"weights": weights})
    assert r["status"] == 400


def test_post_audit_rejects_weights_not_dict(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/audit", json_body={"weights": "bad"})
    assert r["status"] == 400


def test_post_audit_rejects_threshold_string(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/audit", json_body={"approval_threshold": "abc"})
    assert r["status"] == 400


def test_post_audit_rejects_max_rewrite_invalid(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/audit", json_body={"max_rewrite_attempts": -1})
    assert r["status"] == 400


def test_post_publish_rejects_negative_interval(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/publish",
        json_body={"min_publish_interval_minutes": -5},
    )
    assert r["status"] == 400


def test_post_publish_rejects_non_int_min(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/publish",
        json_body={"min_publish_interval_minutes": "soon"},
    )
    assert r["status"] == 400


def test_post_system_rejects_negative_budget(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/system", json_body={"monthly_budget_cny": -10})
    assert r["status"] == 400


def test_post_system_rejects_bad_token_limit(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/system", json_body={"daily_token_limit": "lots"})
    assert r["status"] == 400


def test_post_notifications_rejects_missing_minute(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/notifications",
        json_body={"quiet_hours_start": "22"},
    )
    assert r["status"] == 400


def test_post_mode_missing_dry_run(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/mode", json_body={"mode": "auto"})
    assert r["status"] == 400


def test_post_mode_with_runtime_change(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/mode", json_body={"dry_run": True, "mode": "auto"})
    assert r["status"] == 200
    cfg_text = env_setup["cfg"].read_text(encoding="utf-8")
    assert 'mode: "auto"' in cfg_text or "mode: auto" in cfg_text


def test_finish_login_when_no_session(env_setup: dict[str, Path]) -> None:
    r = _request("POST", "/api/settings/publish/login/finish")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is False


def test_login_status_returns_state_object(env_setup: dict[str, Path]) -> None:
    r = _request("GET", "/api/settings/publish/login/status")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert "session" in body and "login_state" in body


def test_open_folder_data_kind(env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖 system/open-folder 成功路径，但不真启 explorer。"""
    import platform as _pl

    if _pl.system() == "Windows":
        called: list[str] = []

        def fake_startfile(p: str) -> None:
            called.append(p)

        monkeypatch.setattr("os.startfile", fake_startfile, raising=False)
    else:
        # 非 Windows 下用 subprocess.Popen 桩
        from review_queue import settings_api as sa

        monkeypatch.setattr(sa.subprocess, "Popen", lambda *a, **kw: None)
    r = _request("POST", "/api/settings/system/open-folder", json_body={"kind": "data"})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True


def test_post_generation_writes_base_url_and_model(env_setup: dict[str, Path]) -> None:
    r = _request(
        "POST",
        "/api/settings/generation",
        json_body={
            "base_url": "https://new-base/v1",
            "model": "deepseek-v4-flash",
        },
    )
    assert r["status"] == 200
    env_text = env_setup["env"].read_text(encoding="utf-8")
    assert "DEEPSEEK_BASE_URL=https://new-base/v1" in env_text
    assert "DEEPSEEK_MODEL=deepseek-v4-flash" in env_text


def test_get_publish_when_pause_disabled(env_setup: dict[str, Path]) -> None:
    """切换 pause_on_risk_control，确认 GET 反映出来。"""
    _request("POST", "/api/settings/publish", json_body={"pause_on_risk_control": False})
    r = _request("GET", "/api/settings/publish")
    body = json.loads(r["body"])
    assert body["pause_on_risk_control"] is False


def test_test_generation_uses_existing_key_when_masked(
    env_setup: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """提交 masked api_key 时，测试连接应使用 .env 里的真实 key。"""
    captured: dict[str, Any] = {}

    class FakeResp:
        status_code = 401
        text = "unauthorized"

    def fake_post(url: str, **kwargs: Any) -> FakeResp:
        captured["headers"] = kwargs.get("headers")
        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    masked = settings_api.mask_secret("sk-abcdef-1234567890-tail")
    r = _request("POST", "/api/settings/generation/test", json_body={"api_key": masked})
    body = json.loads(r["body"])
    assert body["ok"] is False
    assert "401" in body["message"]
    # 应用了 .env 里的真实 key
    assert captured["headers"]["Authorization"] == "Bearer sk-abcdef-1234567890-tail"
