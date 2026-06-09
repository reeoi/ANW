"""Phase 3 Dashboard 4 张状态卡片聚合。

入口 :func:`monitor_cards` 必须在 50ms 内返回，所以全部用 SQLite 索引读 +
缓存 cookie 文件解析（每 60 秒一次,避免读文件过频）。

返回结构（JSON）::

    {
      "next_run":  { job_id, label, next_run_at, countdown_seconds, level },
      "last_run":  { kind, status, story_id, occurred_at, level, message },
      "login":     { status, label, days_left, level },
      "budget":    { used_cny, limit_cny, percent, level }
    }

``level`` 字段是前端用于卡片配色的：``ok`` / ``warn`` / ``danger``。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config_loader import LoadedConfig

def monitor_cards(config: LoadedConfig, db_path: str | Path) -> dict[str, Any]:
    """聚合 4 张状态卡片所需数据。

    Args:
        config: 已加载的配置（含 cost_limits 等）。
        db_path: SQLite 路径。

    第一张卡（"待处理收件箱"）由阶段 4 接入；本阶段仅删除调度器依赖。
    """
    return {
        "next_run": _next_run_card(),
        "last_run": _last_run_card(db_path),
        "budget": _budget_card(config, db_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# Card: 下次运行（调度器已下线，占位为待处理收件箱由阶段 4 替换）
# ============================================================================


def _next_run_card() -> dict[str, Any]:
    return {
        "job_id": None,
        "label": "—",
        "next_run_at": None,
        "countdown_seconds": None,
        "level": "warn",
    }


# ============================================================================
# Card: 最近结果
# ============================================================================


def _last_run_card(db_path: str | Path) -> dict[str, Any]:
    """从 ``pipeline_events`` 拿最近一条非 ``info`` 事件,以及最近 N 次失败计数。"""
    p = Path(db_path)
    if not p.exists():
        return {"kind": None, "status": None, "occurred_at": None, "level": "warn", "message": "无运行记录"}
    try:
        with sqlite3.connect(p) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT occurred_at, kind, status, story_id, message
                FROM pipeline_events
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            recent_failures = connection.execute(
                """
                SELECT COUNT(*) FROM pipeline_events
                WHERE status IN ('failed', 'error', 'paused')
                  AND id > COALESCE(
                    (SELECT MAX(id) FROM pipeline_events WHERE status IN ('success', 'approved', 'published')),
                    0
                  )
                """
            ).fetchone()
    except sqlite3.Error:
        return {"kind": None, "status": None, "occurred_at": None, "level": "warn", "message": "无法查询事件表"}

    if row is None:
        return {"kind": None, "status": None, "occurred_at": None, "level": "warn", "message": "无运行记录"}

    consecutive_failures = int(recent_failures[0] or 0) if recent_failures else 0
    status = str(row["status"])
    if status in {"success", "approved", "published"}:
        level = "ok"
    elif consecutive_failures >= 2:
        level = "danger"
    else:
        level = "warn"
    return {
        "kind": str(row["kind"]),
        "status": status,
        "story_id": row["story_id"],
        "occurred_at": str(row["occurred_at"]),
        "level": level,
        "message": str(row["message"] or ""),
        "consecutive_failures": consecutive_failures,
    }


# ============================================================================
# Card: 月度预算
# ============================================================================


def _budget_card(config: LoadedConfig, db_path: str | Path) -> dict[str, Any]:
    cost_limits = config.data.get("cost_limits") or {}
    try:
        limit = float(cost_limits.get("monthly_budget_cny") or 0)
    except (TypeError, ValueError):
        limit = 0.0
    p = Path(db_path)
    used = 0.0
    if p.exists():
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with sqlite3.connect(p) as connection:
                row = connection.execute(
                    "SELECT COALESCE(SUM(cost_cny), 0) FROM api_usage WHERE occurred_at >= ?",
                    (cutoff,),
                ).fetchone()
            used = float(row[0] or 0.0) if row else 0.0
        except sqlite3.Error:
            used = 0.0
    pct = round((used / limit) * 100, 2) if limit > 0 else 0.0
    if limit <= 0:
        level = "ok"
    elif pct >= 90:
        level = "danger"
    elif pct >= 60:
        level = "warn"
    else:
        level = "ok"
    return {
        "used_cny": round(used, 2),
        "limit_cny": round(limit, 2),
        "percent": pct,
        "level": level,
    }


# ============================================================================
# 不打扰时段判断 (服务端,Phase 3 通知精细化)
# ============================================================================


def is_quiet_hours(config: LoadedConfig, now: datetime | None = None) -> bool:
    """根据配置判断当前是否处于"不打扰时段"。"""
    n = config.data.get("notifications") or {}
    start = str(n.get("quiet_hours_start") or "").strip()
    end = str(n.get("quiet_hours_end") or "").strip()
    if not start or not end:
        return False
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except ValueError:
        return False
    current = now or datetime.now()
    cur_minutes = current.hour * 60 + current.minute
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= cur_minutes < end_minutes
    # 跨夜: 22:00 - 08:00
    return cur_minutes >= start_minutes or cur_minutes < end_minutes


__all__ = [
    "is_quiet_hours",
    "monitor_cards",
]
