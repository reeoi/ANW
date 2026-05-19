"""HTTP API endpoints for manual scan triggers.

Surfaces the scan operation that previously only existed as a CLI command
(``cli.scan_now``) so the user can refresh ``theme_pool.json`` from the Web
UI without ever opening a terminal.

Endpoints:
- ``GET  /api/scan/status``    - theme_pool size + iso_week + last-scan + fallback flag
- ``POST /api/scan/run``       - synchronously refresh theme_pool.json (live or dry-run)

The ``/api/plan/...`` endpoints have been removed along with the scheduler
(daily_publish_plan is no longer maintained — manual single-shot only).

Scan can take 30-90 seconds with a live DeepSeek call; the UI should show a
spinner and not retry on its own. A dry-run flag is honoured for offline
rehearsal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config_loader import load_from_environment
from generator.api_client import DeepSeekClient
from scan import WeeklyScanBlockedError, run_weekly_scan

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["scan-plan"])


# ============================================================ scan


@router.get("/scan/status")
def get_scan_status() -> dict[str, Any]:
    """Inspect data/theme_pool.json without touching the LLM.

    Returns:
        - ``exists`` (bool)
        - ``iso_week`` (str|None)
        - ``generated_at`` (str|None) ISO8601
        - ``item_count`` (int)
        - ``used_fallback`` (bool)
        - ``weekly_topics`` (list[str])
        - ``pool_path`` (str)
    """
    config = load_from_environment()
    pool_path = _theme_pool_path(config)
    payload: dict[str, Any] = {
        "exists": False,
        "iso_week": None,
        "generated_at": None,
        "item_count": 0,
        "used_fallback": False,
        "weekly_topics": [],
        "pool_path": str(pool_path),
    }
    if not pool_path.exists():
        return payload
    try:
        data = json.loads(pool_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("theme_pool.json unreadable: %s", exc)
        payload["exists"] = True
        payload["error"] = f"theme_pool.json 解析失败: {exc}"
        return payload

    items = data.get("items") if isinstance(data, dict) else None
    if items is None and isinstance(data, list):
        items = data
    payload["exists"] = True
    payload["iso_week"] = data.get("iso_week") if isinstance(data, dict) else None
    payload["generated_at"] = (
        data.get("generated_at") if isinstance(data, dict) else None
    )
    payload["used_fallback"] = bool(
        data.get("used_fallback") if isinstance(data, dict) else False
    )
    payload["weekly_topics"] = list(
        data.get("weekly_topics") or [] if isinstance(data, dict) else []
    )
    payload["item_count"] = len(items) if isinstance(items, list) else 0
    return payload


@router.post("/scan/run")
async def post_scan_run(request: Request) -> dict[str, Any]:
    """Trigger ``run_weekly_scan`` synchronously.

    Body (all optional):
        - ``force`` (bool, default False) — rerun even if this week's pool exists
        - ``dry_run`` (bool, default False) — synthesize pool offline
    """
    payload = await _safe_json(request)
    force = bool(payload.get("force", False))
    dry_run = bool(payload.get("dry_run", False))

    config = load_from_environment()
    client: Any
    if dry_run:
        from cli.scan_now import _DryRunScanClient
        from scan import load_seeds

        client = _DryRunScanClient(seeds=load_seeds())
    else:
        client = DeepSeekClient(config)

    try:
        result = run_weekly_scan(config, force=force, client=client)
    except WeeklyScanBlockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("scan/run failed")
        raise HTTPException(status_code=500, detail=f"scan failed: {exc}") from exc

    return {
        "ok": True,
        "iso_week": result.iso_week,
        "item_count": result.item_count,
        "used_fallback": result.used_fallback,
        "weekly_topics": list(result.weekly_topics),
        "pool_path": str(result.pool_path),
        "backed_up_to": str(result.backed_up_to) if result.backed_up_to else None,
        "warnings": list(result.warnings),
    }


# ============================================================ helpers


async def _safe_json(request: Request) -> dict[str, Any]:
    """Read JSON body; return {} for empty / non-JSON bodies."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _theme_pool_path(config: Any) -> Path:
    """Resolve theme_pool.json path the same way seed_evolver does.

    Honours ``runtime.project_root`` so tests can redirect the pool to a
    tmp directory; falls back to the package's repo root otherwise.
    """
    runtime = (config.data.get("runtime") or {}) if hasattr(config, "data") else {}
    raw_root = runtime.get("project_root") if isinstance(runtime, dict) else None
    if raw_root and raw_root != ".":
        root = Path(raw_root).resolve()
    else:
        root = _project_root()
    return root / "data" / "theme_pool.json"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


__all__ = ["router"]
