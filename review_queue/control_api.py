"""Phase 2 控制平面：托盘 / UI 用的控制端点。

把"健康探针 / 通知 SSE / 开机自启 / 软重启"集中到一处。
（全自动调度器已下线，"启用全自动"开关与 /api/control/auto 端点一并移除。）
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from auto_start import (
    disable as autostart_disable,
)
from auto_start import (
    enable as autostart_enable,
)
from auto_start import (
    is_windows as autostart_is_windows,
)
from auto_start import (
    status as autostart_status,
)
from config_loader import load_from_environment
from review_queue.monitor_aggregator import is_quiet_hours
from review_queue.notification_bus import Severity, bus

logger = logging.getLogger(__name__)


def _quiet_predicate() -> bool:
    try:
        return is_quiet_hours(load_from_environment())
    except Exception:
        return False


bus.set_quiet_filter(_quiet_predicate)


router = APIRouter(prefix="/api", tags=["control"])


# ============================================================================
# /api/control/restart (托盘菜单触发)
# ============================================================================


@router.post("/control/restart")
def restart_service() -> dict[str, Any]:
    """提示外部托盘进程：用户希望重启 uvicorn。

    本进程内并不真重启 uvicorn —— 由托盘程序订阅这条 ``critical`` 通知后,
    自己 ``terminate()`` + 重新 ``Popen``。
    """
    bus.publish(
        Severity.WARNING,
        "重启请求",
        "用户从托盘菜单触发了软重启",
        source="control",
        action="restart_uvicorn",
    )
    logger.info("Restart requested via /api/control/restart")
    return {"ok": True, "message": "重启请求已广播。托盘会处理。"}


# ============================================================================
# /api/autostart
# ============================================================================


@router.get("/autostart")
def get_autostart() -> dict[str, Any]:
    return autostart_status()


@router.post("/autostart")
async def set_autostart(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    enabled = bool(payload.get("enabled"))
    project_root = Path(__file__).resolve().parents[1]
    pythonw = project_root / ".venv" / "Scripts" / "pythonw.exe"
    tray = project_root / "tray_app.py"
    if enabled:
        if not autostart_is_windows():
            raise HTTPException(status_code=400, detail="开机自启只支持 Windows")
        path = autostart_enable(project_root=project_root, pythonw=pythonw, tray_script=tray)
        bus.publish(Severity.INFO, "开机自启已启用", f"快捷方式：{path}", source="control")
        return {"ok": True, "enabled": True, "shortcut_path": str(path)}
    removed = autostart_disable()
    bus.publish(
        Severity.INFO,
        "开机自启已关闭",
        "已移除 Startup 文件夹中的快捷方式" if removed else "本来就没有",
        source="control",
    )
    return {"ok": True, "enabled": False, "removed": removed}


# ============================================================================
# /api/notifications
# ============================================================================


@router.get("/notifications")
def list_notifications(limit: int = 50) -> dict[str, Any]:
    items = bus.list_recent(limit=max(1, min(limit, 200)))
    return {"ok": True, "items": [n.to_dict() for n in items]}


@router.post("/notifications/dismiss/{notification_id}")
def dismiss_notification(notification_id: str) -> dict[str, Any]:
    ok = bus.dismiss(notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="通知不存在或已 dismiss")
    return {"ok": True}


@router.post("/notifications/dismiss-all")
def dismiss_all() -> dict[str, Any]:
    n = bus.dismiss_all()
    return {"ok": True, "dismissed": n}


@router.get("/notifications/stream")
async def notifications_stream() -> StreamingResponse:
    """Server-Sent Events 推送通知。

    托盘程序和前端都用这个端点。
    """

    async def event_generator():
        # 先发 retry 提示让客户端在断线后等待 3 秒
        yield "retry: 3000\n\n"
        # 立即发送最近 10 条历史
        for item in reversed(bus.list_recent(limit=10)):
            yield item.to_sse()
        async for notification in bus.subscribe():
            try:
                yield notification.to_sse()
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================================
# 工具
# ============================================================================


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON 请求体必须是对象")
    return payload


__all__ = ["router"]
