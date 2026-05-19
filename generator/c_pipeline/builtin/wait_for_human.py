"""Wait for human input — pause pipeline, wait for dashboard submission. Phase 6.2."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Process-level registry of pending inputs (survives across threads but not restarts)
_pending_events: dict[int, threading.Event] = {}  # story_id → Event
_pending_lock = threading.Lock()


def run(config: Any = None, work_dir: str | Path | None = None,
        params: dict | None = None, inputs: dict | None = None,
        ctx: Any = None) -> dict[str, Any]:
    """Block until user provides input via API. Phase 6 builtin executor."""
    params = params or {}
    prompt = params.get("prompt", "请输入内容")
    timeout = int(params.get("timeout_seconds", 3600))
    input_schema = params.get("input_schema", {"type": "text"})

    # Extract story_id from context or globals
    story_id = 0
    if ctx and hasattr(ctx, "globals"):
        sid = ctx.globals.get("story_id")
        if sid is not None:
            story_id = int(sid)

    # Write pending record to DB
    db_path = _get_db_path(config, work_dir)
    if db_path:
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO pending_human_input (story_id, prompt, input_schema, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (story_id, prompt, json.dumps(input_schema, ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
        except Exception as exc:
            logger.warning("failed to write pending_human_input: %s", exc)

    # Block on event
    event = threading.Event()
    with _pending_lock:
        _pending_events[story_id] = event

    logger.info("wait_for_human story_id=%s timeout=%s prompt=%s", story_id, timeout, prompt[:50])
    signaled = event.wait(timeout=timeout)

    with _pending_lock:
        _pending_events.pop(story_id, None)

    if not signaled:
        raise TimeoutError(f"等待人工输入超时（{timeout}s）")

    # Read the submitted payload from DB
    content = ""
    if db_path:
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT payload_json FROM pending_human_input WHERE story_id = ? ORDER BY id DESC LIMIT 1",
                    (story_id,),
                ).fetchone()
                if row and row[0]:
                    payload = json.loads(row[0])
                    content = payload.get("content", payload.get("text", str(payload)))
        except Exception:
            pass

    return {"content": content, "status": "received"}


def provide_input(story_id: int, payload: dict, db_path: str | Path) -> bool:
    """External API: submit user input to wake a waiting pipeline thread."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE pending_human_input SET payload_json = ?, resolved_at = ? WHERE story_id = ? AND resolved_at IS NULL",
                (json.dumps(payload, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(), story_id),
            )
            conn.commit()
    except Exception as exc:
        logger.exception("provide_input DB write failed")
        return False

    with _pending_lock:
        event = _pending_events.get(story_id)
    if event:
        event.set()
        return True
    return False


def _get_db_path(config: Any, work_dir: str | Path | None) -> str | None:
    """Extract db_path from config or work_dir structure."""
    try:
        if config and hasattr(config, "data"):
            db = config.data.get("database", {})
            return str(db.get("sqlite_path", "data/anp.sqlite3"))
    except Exception:
        pass
    if work_dir:
        p = Path(work_dir)
        return str(p.parents[2] / "data" / "anp.sqlite3")
    return "data/anp.sqlite3"


__all__ = ["provide_input", "run"]
