"""章节步骤产物的文件布局与读写：step 文件、历史版本、运行计数、
skip/gate/force/progress 标记、状态快照。

New layout writes step files into the per-chapter folder
(`正文/第NNN章_标题/<step>.md|json`). The legacy work_dir-root paths
(`_step_*.md|json`) are kept as a read fallback so a half-written chapter from
before the migration still resolves.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from generator.long_novel.jobs import _CHAPTER_STEP_STALE_SECONDS, _step_job_active
from generator.long_novel.l2_chapter_write import (
    CHAPTER_STEP_FILES,
    chapter_dir,
)

logger = logging.getLogger(__name__)

_LEGACY_STEP_FILES = {
    "draft": "_step_draft.md",
    "expand": "_step_expand.md",
    "polish": "_step_polish.md",
    "review": "_step_review.json",
    "deslop": "_step_deslop.md",
}


def _max_outline_chapter(work_dir: Path) -> int:
    outline_dir = work_dir / "大纲"
    if not outline_dir.exists():
        return 0
    max_ch = 0
    for p in outline_dir.glob("细纲_第*章.md"):
        m = re.search(r"第(\d+)章", p.name)
        if m:
            max_ch = max(max_ch, int(m.group(1)))
    return max_ch


def _outline_title(path: Path, chapter_number: int) -> str:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                text = re.sub(r"^第\s*0*\d+\s*章[：:\s-]*", "", text).strip()
                if text:
                    return text[:80]
                break
    return f"第{chapter_number}章"


def _step_file_path(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> Path:
    """Return write path for a step file (always in chapter folder)."""
    return chapter_dir(work_dir, chapter_number, chapter_title) / CHAPTER_STEP_FILES[step]


def _step_history_dir(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> Path:
    """每个步骤的历史版本目录，位于章节文件夹下的 _history/{step}/。"""
    return chapter_dir(work_dir, chapter_number, chapter_title) / "_history" / step


def _archive_step_version(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> Path | None:
    """运行步骤前，把上一版产物归档到 _history/{step}/{timestamp}{ext}。
    无旧产物返回 None；归档失败也返回 None（不阻塞主流程）。"""
    try:
        current = _step_file_path(work_dir, chapter_number, chapter_title, step)
        if not current.exists():
            return None
        history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step)
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archived = history_dir / f"{ts}{current.suffix}"
        archived.write_bytes(current.read_bytes())
        return archived
    except Exception:
        logger.exception("archive_step_version_failed step=%s chapter=%s", step, chapter_number)
        return None


def _step_history_count(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> int:
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step)
    if not history_dir.exists():
        return 0
    try:
        return sum(1 for p in history_dir.iterdir() if p.is_file())
    except Exception:
        logger.exception("count_step_history_failed step=%s chapter=%s", step, chapter_number)
        return 0


def _step_run_count(work_dir: Path, chapter_number: int, chapter_title: str, step: str, *, has_current: bool = True) -> int:
    count = _step_history_count(work_dir, chapter_number, chapter_title, step)
    if has_current:
        count += 1
    return max(0, count)


def _chapter_batch_count(work_dir: Path, chapter_number: int, chapter_title: str) -> int:
    """Use the draft run as the visible chapter-writing batch number."""
    draft_path = _step_file_read(work_dir, chapter_number, "draft")
    return _step_run_count(work_dir, chapter_number, chapter_title, "draft", has_current=bool(draft_path and draft_path.exists()))


def _finalize_run_count(final_path: Path | None) -> int:
    if not final_path:
        return 0
    count = 1 if final_path.exists() else 0
    parent = final_path.parent
    if parent.exists():
        try:
            count += sum(
                1 for p in parent.iterdir()
                if p.is_file() and p.name.startswith(final_path.stem) and p.name.endswith(".md.bak")
            )
        except Exception:
            logger.exception("count_finalize_history_failed path=%s", final_path)
    return count


def _step_skip_path(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> Path:
    """Return write path for a skip marker."""
    return chapter_dir(work_dir, chapter_number, chapter_title) / f".skip_{step}.json"


def _step_skip_read(work_dir: Path, chapter_number: int, step: str) -> Path | None:
    text_dir = work_dir / "正文"
    prefix = f"第{chapter_number:03d}章"
    if text_dir.exists():
        for p in text_dir.iterdir():
            if p.is_dir() and p.name.startswith(prefix):
                cand = p / f".skip_{step}.json"
                if cand.exists():
                    return cand
    return None


def _step_gate_path(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> Path:
    return chapter_dir(work_dir, chapter_number, chapter_title) / f".gate_{step}.json"


def _step_gate_read(work_dir: Path, chapter_number: int, step: str) -> Path | None:
    cand = chapter_dir(work_dir, chapter_number, "") / f".gate_{step}.json"
    return cand if cand.exists() else None


def _step_force_path(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> Path:
    return chapter_dir(work_dir, chapter_number, chapter_title) / f".force_pass_{step}.json"


def _step_force_read(work_dir: Path, chapter_number: int, step: str) -> Path | None:
    cand = chapter_dir(work_dir, chapter_number, "") / f".force_pass_{step}.json"
    return cand if cand.exists() else None


def _step_progress_path(work_dir: Path, chapter_number: int, chapter_title: str, step: str) -> Path:
    return chapter_dir(work_dir, chapter_number, chapter_title) / f".progress_{step}.json"


def _step_progress_read(work_dir: Path, chapter_number: int, step: str) -> Path | None:
    cand = chapter_dir(work_dir, chapter_number, "") / f".progress_{step}.json"
    return cand if cand.exists() else None


def _write_step_progress(path: Path, status: str, detail: str = "", extra: dict[str, Any] | None = None) -> None:
    payload = {
        "status": status,
        "detail": detail,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _step_status_snapshot(
    book_id: int,
    work_dir: Path,
    ch: dict[str, Any],
    chapter_number: int,
    step_name: str,
) -> dict[str, Any]:
    chapter_title = str(ch.get("title") or "")
    batch_count = _chapter_batch_count(work_dir, chapter_number, chapter_title)
    progress_path = _step_progress_read(work_dir, chapter_number, step_name)
    if progress_path and progress_path.exists():
        data = _read_json_file(progress_path)
        status = str(data.get("status") or "pending")
        detail = str(data.get("detail") or "")
        if status in {"starting", "running"} and not _step_job_active(book_id, chapter_number, step_name):
            age = time.time() - progress_path.stat().st_mtime
            if age > _CHAPTER_STEP_STALE_SECONDS:
                status = "cancelled"
                detail = detail or "任务中断，请重新运行"
                _write_step_progress(progress_path, status, detail, {"result": data.get("result") or {}})
                data = _read_json_file(progress_path)
        return {
            "step": step_name,
            "status": status,
            "detail": detail,
            "updated_at": data.get("updated_at", ""),
            "result": data.get("result") or {},
            "run_count": int((data.get("result") or {}).get("run_count") or 0),
            "batch_count": int((data.get("result") or {}).get("batch_count") or batch_count),
        }

    if step_name == "finalize":
        if ch.get("draft_path"):
            final_path = Path(str(ch.get("draft_path")))
            return {
                "step": step_name,
                "status": "done",
                "detail": "已成稿",
                "updated_at": "",
                "run_count": _finalize_run_count(final_path),
                "batch_count": batch_count,
            }
        return {"step": step_name, "status": "pending", "detail": "", "updated_at": "", "run_count": 0, "batch_count": batch_count}

    step_path = _step_file_read(work_dir, chapter_number, step_name)
    if step_path and step_path.exists():
        return {
            "step": step_name,
            "status": "done",
            "detail": "已完成",
            "updated_at": "",
            "run_count": _step_run_count(work_dir, chapter_number, chapter_title, step_name),
            "batch_count": batch_count,
        }
    skip_marker = _step_skip_read(work_dir, chapter_number, step_name)
    if skip_marker and skip_marker.exists():
        marker_data = _read_json_file(skip_marker)
        return {
            "step": step_name,
            "status": "skipped",
            "detail": str(marker_data.get("reason") or "已跳过"),
            "updated_at": str(marker_data.get("created_at") or ""),
            "run_count": _step_history_count(work_dir, chapter_number, chapter_title, step_name),
            "batch_count": batch_count,
        }
    return {"step": step_name, "status": "pending", "detail": "", "updated_at": "", "run_count": 0, "batch_count": batch_count}


def _read_json_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _step_file_read(work_dir: Path, chapter_number: int, step: str) -> Path | None:
    """Find a step file for reading: chapter folder first, then legacy root."""
    folder_candidates: list[Path] = []
    text_dir = work_dir / "正文"
    prefix = f"第{chapter_number:03d}章"
    if text_dir.exists():
        for p in text_dir.iterdir():
            if p.is_dir() and p.name.startswith(prefix):
                folder_candidates.append(p)
    fname = CHAPTER_STEP_FILES.get(step)
    if fname:
        for folder in folder_candidates:
            cand = folder / fname
            if cand.exists():
                return cand
    legacy = work_dir / _LEGACY_STEP_FILES.get(step, "")
    return legacy if legacy.exists() else None


def _read_step_source(work_dir: Path, chapter_number: int, preferred: list[str] | None = None) -> str:
    order = preferred or ["deslop", "polish", "expand", "draft"]
    for step in order:
        path = _step_file_read(work_dir, chapter_number, step)
        if path and path.exists() and path.suffix != ".json":
            return path.read_text(encoding="utf-8")
    return ""


def _outline_for_chapter(ch: dict[str, Any]) -> str:
    outline_path = ch.get("outline_path")
    if outline_path:
        path = Path(outline_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _draft_context_manifest(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        ("本章细纲", "outline", True),
        ("全书大纲", "book_outline", True),
        ("卷纲", "volume_outline", True),
        ("上章结尾/摘要", "prev_chapter_last_paras", False),
        ("全书进展", "book_progress", False),
        ("续写约束", "continuation_constraints", True),
        ("角色状态", "character_states", False),
        ("角色设定", "character_profiles", True),
        ("人物关系", "relationships", True),
        ("世界观", "world", True),
        ("伏笔", "foreshadowing", False),
        ("时间线", "timeline", False),
        ("题材定位", "premise", True),
    ]
    manifest = []
    for label, key, required in items:
        value = str(ctx.get(key) or "")
        manifest.append({
            "label": label,
            "key": key,
            "required": required,
            "present": bool(value.strip()),
            "chars": len(value),
        })
    return manifest
