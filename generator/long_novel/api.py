"""Long novel REST API — book library, writing workbench, review."""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from config_loader import load_from_environment
from generator.long_novel.db import (
    create_book,
    delete_book,
    get_book,
    get_chapter,
    get_next_chapter,
    list_books,
    list_chapters,
    list_volumes,
    update_book,
    upsert_chapter,
    upsert_volume,
)
from generator.long_novel.l0_book_setup import (
    setup_dir,
    setup_file_read,
    setup_glob,
)
from generator.long_novel.l2_chapter_write import (
    CHAPTER_FINAL_FILENAME,
    CHAPTER_STEP_FILES,
    chapter_dir,
    chapter_final_path,
)
from generator.long_novel.theme_manager import (
    get_fanqie_dates,
    get_fanqie_trending_keywords,
    get_hot_themes,
    get_trending_emotions,
    get_trending_genres,
    import_fanqie_trends,
    suggest_books,
)
from review_queue.db import initialize_database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/long-novel", tags=["long-novel"])
_EXPAND_AUTO_SKIP_WORDS = 3000
_AUTOPILOT_DEFAULT_MAX_REVISIONS = 2
_AUTOPILOT_MAX_REVISIONS = 3

# ── Cancel tokens for book operations ──────────────────────────────────
_cancel_tokens: dict[int, bool] = {}
_cancel_lock = threading.Lock()
_autopilot_jobs: set[int] = set()
_autopilot_jobs_lock = threading.Lock()
_chapter_step_jobs: set[tuple[int, int, str]] = set()
_chapter_step_jobs_lock = threading.Lock()
_CHAPTER_STEP_STALE_SECONDS = 60 * 60 * 2


def _is_cancelled(book_id: int) -> bool:
    with _cancel_lock:
        return _cancel_tokens.get(book_id, False)


def _set_cancel(book_id: int, value: bool) -> None:
    with _cancel_lock:
        _cancel_tokens[book_id] = value


def _autopilot_job_active(book_id: int) -> bool:
    with _autopilot_jobs_lock:
        return int(book_id) in _autopilot_jobs


def _autopilot_job_mark(book_id: int, active: bool) -> None:
    with _autopilot_jobs_lock:
        if active:
            _autopilot_jobs.add(int(book_id))
        else:
            _autopilot_jobs.discard(int(book_id))


def _db_path() -> Path:
    config = load_from_environment()
    return initialize_database(config) or Path("data/anw.sqlite3")


def _project_root() -> Path:
    config = load_from_environment()
    return Path(str(config.data.get("runtime", {}).get("project_root") or ".")).resolve()


def _deepseek_client(book: dict[str, Any] | None = None) -> Any:
    """Create a client and bind long-novel usage records to the current book."""
    from generator.api_client import DeepSeekClient

    client = DeepSeekClient(load_from_environment())
    if book:
        client.set_usage_context(
            work_type="long_novel",
            work_id=int(book["id"]),
            work_title=str(book.get("title") or ""),
        )
    return client


def _upsert_chapter_preserving(
    db_path: Path,
    chapter: dict[str, Any],
    **changes: Any,
) -> None:
    """Update one chapter without clearing metadata omitted by the caller."""
    values = {
        "title": str(chapter.get("title") or ""),
        "status": str(chapter.get("status") or "outline_only"),
        "target_words": int(chapter.get("target_words") or 3000),
        "actual_words": int(chapter.get("actual_words") or 0),
        "outline_path": chapter.get("outline_path"),
        "draft_path": chapter.get("draft_path"),
        "review_status": chapter.get("review_status"),
        "ai_review_json": chapter.get("ai_review_json"),
    }
    values.update(changes)
    upsert_chapter(
        db_path,
        int(chapter["book_id"]),
        int(chapter.get("volume_number") or 1),
        int(chapter["chapter_number"]),
        **values,
    )


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        return await request.json() or {}
    except Exception:
        return {}


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


# Step file resolution. New layout writes step files into the per-chapter folder
# (`正文/第NNN章_标题/<step>.md|json`). The legacy work_dir-root paths
# (`_step_*.md|json`) are kept as a read fallback so a half-written chapter from
# before the migration still resolves.

_LEGACY_STEP_FILES = {
    "draft": "_step_draft.md",
    "expand": "_step_expand.md",
    "polish": "_step_polish.md",
    "review": "_step_review.json",
    "deslop": "_step_deslop.md",
}


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


def _step_job_key(book_id: int, chapter_number: int, step_name: str) -> tuple[int, int, str]:
    return (int(book_id), int(chapter_number), str(step_name))


def _step_job_active(book_id: int, chapter_number: int, step_name: str) -> bool:
    with _chapter_step_jobs_lock:
        return _step_job_key(book_id, chapter_number, step_name) in _chapter_step_jobs


def _step_job_mark(book_id: int, chapter_number: int, step_name: str, active: bool) -> None:
    key = _step_job_key(book_id, chapter_number, step_name)
    with _chapter_step_jobs_lock:
        if active:
            _chapter_step_jobs.add(key)
        else:
            _chapter_step_jobs.discard(key)


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


def _score_deai_result(text: str) -> dict[str, Any]:
    """Build a local quality gate for the de-AI step."""
    def _local_char_count(value: str) -> int:
        return sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")

    pass_score = 82
    score = 88
    findings: list[str] = ["已完成本地去 AI 改写，并通过文本特征质量门评估。"]
    recommendations: list[str] = []
    ai_phrases = [
        "仿佛", "似乎", "微微", "淡淡", "不由得", "心中一动", "眼中闪过",
        "嘴角勾起", "一股", "某种", "复杂的情绪",
    ]
    hits = [phrase for phrase in ai_phrases if phrase in text]
    if hits:
        score -= min(18, len(hits) * 3)
        findings.append("仍有高频 AI 味表达：" + " / ".join(hits[:8]))
        recommendations.append("用具体动作、对白和感官细节替换泛泛的情绪标签。")

    paragraph_count = max(1, len([p for p in text.splitlines() if p.strip()]))
    avg_paragraph_len = _local_char_count(text) / paragraph_count
    if avg_paragraph_len > 260:
        score -= 6
        findings.append("部分段落偏长，阅读节奏可能显得整齐或说明感过强。")
        recommendations.append("拆分长段，穿插动作、环境反馈和短对白。")
    if text.count("。") and text.count("，") / max(1, text.count("。")) > 5:
        score -= 5
        findings.append("长句比例偏高，句式层次容易显得机械。")
        recommendations.append("把连续说明句改成短句、停顿和人物反应。")

    score = max(0, min(100, int(score)))
    if score >= pass_score:
        verdict = "APPROVE"
    else:
        verdict = "CONCERNS" if score >= 65 else "REJECT"
    return {
        "verdict": verdict,
        "score": score,
        "pass_score": pass_score,
        "passed": score >= pass_score,
        "pending": False,
        "source": "local_text_quality",
        "findings": findings,
        "recommendations": recommendations,
        "summary": f"去 AI 质量门：{verdict} / 本地文本特征 / {score}分",
    }


def _normalize_review_gate(review: dict[str, Any], chapter_number: int = 0) -> dict[str, Any]:
    required = ["continuity", "logic", "plot_progress", "character_integrity", "environment", "empathy"]
    try:
        from generator.long_novel.l4_review import _normalize_story_review
        has_previous_chapter = chapter_number > 1
        return _normalize_story_review(
            review,
            required,
            chapter_number=chapter_number,
            has_previous_chapter=has_previous_chapter,
        )
    except Exception:
        logger.exception("normalize_review_gate_fallback chapter=%s", chapter_number)
    dims = review.get("dimensions") if isinstance(review.get("dimensions"), dict) else {}
    verdict_score = {"APPROVE": 88, "CONCERNS": 70, "REJECT": 45}
    for key in required:
        dim = dims.get(key) if isinstance(dims.get(key), dict) else {}
        verdict = str(dim.get("verdict") or "CONCERNS").upper()
        if verdict not in verdict_score:
            verdict = "CONCERNS"
        issue_count = len(dim.get("findings") or []) + len(dim.get("recommendations") or [])
        try:
            score = int(round(float(dim.get("score"))))
        except Exception:
            score = verdict_score[verdict]
        if verdict == "APPROVE":
            score = max(80, min(100, score))
            if issue_count:
                score = min(score, 89) - min(8, issue_count * 2)
        elif verdict == "CONCERNS":
            score = min(79, score) - min(10, issue_count * 2)
            score = max(60, score)
        else:
            score = min(59, score) - min(12, issue_count * 2)
            score = max(20, score)
        dim["score"] = max(0, min(100, score))
        dim["verdict"] = verdict
        dim["pass_score"] = int(dim.get("pass_score") or 80)
        dim["passed"] = bool(int(dim.get("score") or 0) >= dim["pass_score"] and verdict == "APPROVE")
        dims[key] = dim
    scores = [int((dims.get(k) or {}).get("score") or 72) for k in required]
    verdicts = [str((dims.get(k) or {}).get("verdict") or "CONCERNS").upper() for k in required]
    pass_score = int(review.get("pass_score") or 80)
    if "REJECT" in verdicts or min(scores) < 60:
        overall = "REJECT"
    elif "CONCERNS" in verdicts or min(scores) < pass_score:
        overall = "CONCERNS"
    else:
        overall = "APPROVE"
    score = round(sum(scores) / max(1, len(scores)))
    if overall == "REJECT":
        score = min(score, 59)
    elif overall == "CONCERNS":
        score = min(score, 79)
    else:
        score = max(score, pass_score)
    review["overall"] = overall
    review["dimensions"] = dims
    review["pass_score"] = pass_score
    review["score"] = int(score)
    review["passed"] = bool(review["score"] >= pass_score and overall == "APPROVE" and min(scores) >= pass_score)
    return review


def _chat_text(client: Any, system: str, user: str, thinking: bool = False) -> str:
    completion = client.chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        thinking_mode=thinking,
    )
    return completion.text if hasattr(completion, "text") else str(completion)


def _review_recommendation_text(review: dict[str, Any]) -> str:
    lines: list[str] = []
    for rec in review.get("recommendations") or []:
        lines.append(str(rec))
    for name, dim in (review.get("dimensions") or {}).items():
        for item in dim.get("findings") or []:
            lines.append(f"[{name} 问题] {item}")
        for item in dim.get("recommendations") or []:
            lines.append(f"[{name} 建议] {item}")
    return "\n".join(f"- {line}" for line in lines if str(line).strip())[:6000]


def _review_issue_count(review: dict[str, Any]) -> int:
    count = len([x for x in review.get("recommendations") or [] if str(x).strip()])
    for dim in (review.get("dimensions") or {}).values():
        if not isinstance(dim, dict):
            continue
        count += len([x for x in dim.get("findings") or [] if str(x).strip()])
        count += len([x for x in dim.get("recommendations") or [] if str(x).strip()])
    return count


def _expand_skip_threshold(target_words: Any) -> int:
    """Return the configured expansion threshold, defaulting to 3000 words."""
    try:
        threshold = int(target_words or _EXPAND_AUTO_SKIP_WORDS)
    except (TypeError, ValueError):
        threshold = _EXPAND_AUTO_SKIP_WORDS
    return max(1, threshold)


def _cleanup_stale_step_outputs(work_dir: Path, chapter_number: int, steps: list[str]) -> None:
    for step in steps:
        path = _step_file_read(work_dir, chapter_number, step)
        if path and path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        for marker in (_step_gate_read(work_dir, chapter_number, step), _step_force_read(work_dir, chapter_number, step)):
            if marker and marker.exists():
                try:
                    marker.unlink()
                except Exception:
                    pass
        for marker in (
            _step_skip_read(work_dir, chapter_number, step),
            _step_progress_read(work_dir, chapter_number, step),
        ):
            if marker and marker.exists():
                try:
                    marker.unlink()
                except Exception:
                    pass


_WRITING_STEP_ORDER = ["draft", "expand", "polish", "deslop", "review", "finalize"]


def _writing_steps_after(step_name: str) -> list[str]:
    try:
        idx = _WRITING_STEP_ORDER.index(step_name)
    except ValueError:
        return []
    return _WRITING_STEP_ORDER[idx + 1 :]


def _archive_file_to_step_history(
    work_dir: Path,
    chapter_number: int,
    chapter_title: str,
    step_name: str,
    path: Path,
) -> None:
    if not path.exists() or not path.is_file():
        return
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step_name)
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archived = history_dir / f"{ts}_{path.name}"
    if archived.exists():
        archived = history_dir / f"{ts}_{len(list(history_dir.iterdir()))}_{path.name}"
    shutil.copy2(path, archived)


def _archive_file_to_invalidated_history(
    work_dir: Path,
    chapter_number: int,
    chapter_title: str,
    step_name: str,
    path: Path,
) -> None:
    if not path.exists() or not path.is_file():
        return
    history_dir = chapter_dir(work_dir, chapter_number, chapter_title) / "_history" / "invalidated" / step_name
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archived = history_dir / f"{ts}_{path.name}"
    if archived.exists():
        archived = history_dir / f"{ts}_{len(list(history_dir.iterdir()))}_{path.name}"
    shutil.copy2(path, archived)


def _archive_and_remove_step_artifact(
    work_dir: Path,
    chapter_number: int,
    chapter_title: str,
    step_name: str,
) -> None:
    paths: list[Path] = []

    def _add(path: Path | None) -> None:
        if path and path.exists() and path.is_file() and path not in paths:
            paths.append(path)

    _add(_step_file_read(work_dir, chapter_number, step_name))
    if step_name in CHAPTER_STEP_FILES:
        _add(_step_file_path(work_dir, chapter_number, chapter_title, step_name))

    for path in paths:
        try:
            _archive_file_to_invalidated_history(work_dir, chapter_number, chapter_title, step_name, path)
            path.unlink()
        except Exception:
            logger.exception("remove_step_artifact_failed step=%s path=%s", step_name, path)

    for marker in (
        _step_gate_read(work_dir, chapter_number, step_name),
        _step_force_read(work_dir, chapter_number, step_name),
        _step_skip_read(work_dir, chapter_number, step_name),
        _step_progress_read(work_dir, chapter_number, step_name),
    ):
        if marker and marker.exists():
            try:
                marker.unlink()
            except Exception:
                logger.exception("remove_step_marker_failed step=%s path=%s", step_name, marker)


def _archive_and_remove_final_artifact(
    work_dir: Path,
    chapter_number: int,
    chapter_title: str,
    chapter: dict[str, Any],
) -> bool:
    paths: list[Path] = []

    def _add(path: Path | None) -> None:
        if path and path.exists() and path.is_file() and path not in paths:
            paths.append(path)

    if chapter.get("draft_path"):
        _add(Path(str(chapter["draft_path"])))
    _add(chapter_final_path(work_dir, chapter_number, chapter_title))

    removed = False
    for path in paths:
        try:
            if not _path_within(path, work_dir):
                logger.warning("skip_final_artifact_outside_work_dir path=%s", path)
                continue
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup = path.with_name(f"{path.stem}.{ts}.md.bak")
            shutil.copy2(path, backup)
            path.unlink()
            removed = True
        except Exception:
            logger.exception("remove_final_artifact_failed path=%s", path)
    return removed


def _invalidate_outputs_after_step(
    db_path: Path,
    book_id: int,
    book: dict[str, Any],
    chapter: dict[str, Any],
    step_name: str,
) -> None:
    later_steps = _writing_steps_after(step_name)
    if not later_steps:
        return

    chapter_number = int(chapter["chapter_number"])
    chapter_title = str(chapter.get("title") or "")
    work_dir = Path(str(book["work_dir"] or ""))
    for later_step in later_steps:
        if _step_job_active(book_id, chapter_number, later_step):
            raise HTTPException(
                status_code=409,
                detail=f"{later_step} is still running; wait for it to finish before rerunning {step_name}.",
            )

    for later_step in later_steps:
        if later_step == "finalize":
            continue
        _archive_and_remove_step_artifact(work_dir, chapter_number, chapter_title, later_step)

    if "finalize" in later_steps:
        _archive_and_remove_final_artifact(work_dir, chapter_number, chapter_title, chapter)
        _upsert_chapter_preserving(
            db_path,
            chapter,
            status="writing",
            actual_words=0,
            draft_path=None,
            review_status=None,
            ai_review_json=None,
        )


def _has_later_saved_chapter(db_path: Path, book_id: int, chapter_number: int) -> bool:
    return any(
        int(chapter.get("chapter_number") or 0) > int(chapter_number)
        and chapter.get("draft_path")
        and Path(str(chapter["draft_path"])).exists()
        for chapter in list_chapters(db_path, book_id)
    )


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _archive_and_reset_chapter_outputs(
    db_path: Path,
    book_id: int,
    book: dict[str, Any],
    chapter: dict[str, Any],
) -> dict[str, Any]:
    """Archive active chapter artifacts, remove them, and reset the DB row."""
    chapter_number = int(chapter["chapter_number"])
    chapter_title = str(chapter.get("title") or "")
    work_dir = Path(str(book["work_dir"] or "")).resolve()
    chapter_folder = chapter_dir(work_dir, chapter_number, chapter_title).resolve()
    text_root = (work_dir / "正文").resolve()
    if not _path_within(chapter_folder, text_root):
        raise HTTPException(status_code=400, detail="章节目录不在正文目录内，无法安全删除")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_dir = chapter_folder / "_history" / "regenerate" / timestamp
    active_paths: list[Path] = []

    def _add(path: Path | None) -> None:
        if not path:
            return
        resolved = path.resolve()
        if resolved.exists() and resolved.is_file() and resolved not in active_paths:
            active_paths.append(resolved)

    for filename in CHAPTER_STEP_FILES.values():
        _add(chapter_folder / filename)
    _add(chapter_folder / CHAPTER_FINAL_FILENAME)
    for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]:
        for marker in (
            f".skip_{step_name}.json",
            f".gate_{step_name}.json",
            f".force_pass_{step_name}.json",
            f".progress_{step_name}.json",
        ):
            _add(chapter_folder / marker)

    saved_draft = Path(str(chapter["draft_path"])) if chapter.get("draft_path") else None
    if saved_draft:
        if not _path_within(saved_draft, work_dir):
            raise HTTPException(status_code=400, detail="当前正文文件不在书籍目录内，无法安全删除")
        _add(saved_draft)

    # Legacy step files were stored at the work-dir root and otherwise become
    # read fallbacks after reset, so archive and remove them as well.
    for legacy_name in _LEGACY_STEP_FILES.values():
        _add(work_dir / legacy_name)

    archived: list[str] = []
    for path in active_paths:
        if not _path_within(path, work_dir):
            raise HTTPException(status_code=400, detail=f"拒绝删除书籍目录外文件：{path}")
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_name = path.name
        if path.parent == work_dir:
            archive_name = f"legacy_{archive_name}"
        target = archive_dir / archive_name
        if target.exists():
            target = archive_dir / f"{target.stem}_{len(archived) + 1}{target.suffix}"
        shutil.copy2(path, target)
        path.unlink()
        archived.append(str(target.relative_to(work_dir)).replace("\\", "/"))

    upsert_chapter(
        db_path,
        book_id,
        int(chapter.get("volume_number") or 1),
        chapter_number,
        title=chapter_title,
        status="outline_only",
        target_words=int(chapter.get("target_words") or book.get("target_words_per_chapter") or 3000),
        actual_words=0,
        outline_path=chapter.get("outline_path"),
        draft_path=None,
        review_status=None,
        ai_review_json=None,
    )
    return {
        "archived_files": archived,
        "archive_dir": str(archive_dir.relative_to(work_dir)).replace("\\", "/") if archived else "",
        "later_written_chapters": [
            int(item["chapter_number"])
            for item in list_chapters(db_path, book_id)
            if int(item.get("chapter_number") or 0) > chapter_number
            and item.get("draft_path")
            and Path(str(item["draft_path"])).exists()
        ],
    }


def _chapter_range(
    db_path: Path,
    book_id: int,
    chapter_start: int,
    chapter_end: int,
) -> list[dict[str, Any]]:
    """Return one validated inclusive chapter range."""
    try:
        start = int(chapter_start)
        end = int(chapter_end)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="正文起止章必须是数字") from None
    if start < 1 or end < 1:
        raise HTTPException(status_code=400, detail="正文起止章必须大于 0")
    if end < start:
        raise HTTPException(status_code=400, detail="正文结束章不能小于起始章")
    by_number = {
        int(chapter.get("chapter_number") or 0): chapter
        for chapter in list_chapters(db_path, book_id)
    }
    chapters: list[dict[str, Any]] = []
    for chapter_number in range(start, end + 1):
        chapter = by_number.get(chapter_number)
        if not chapter:
            raise HTTPException(status_code=400, detail=f"章节队列缺少第{chapter_number}章")
        chapters.append(chapter)
    return chapters


def _chapter_has_outputs(work_dir: Path, chapter: dict[str, Any]) -> bool:
    final_path = Path(str(chapter["draft_path"])) if chapter.get("draft_path") else None
    if final_path and final_path.exists():
        return True
    return any(
        path and path.exists()
        for path in (_step_file_read(work_dir, int(chapter["chapter_number"]), step) for step in CHAPTER_STEP_FILES)
    )


def _reset_chapter_row_for_deleted_outputs(
    db_path: Path,
    book_id: int,
    book: dict[str, Any],
    chapter: dict[str, Any],
) -> None:
    upsert_chapter(
        db_path,
        book_id,
        int(chapter.get("volume_number") or 1),
        int(chapter["chapter_number"]),
        title=str(chapter.get("title") or ""),
        status="outline_only",
        target_words=int(chapter.get("target_words") or book.get("target_words_per_chapter") or 3000),
        actual_words=0,
        outline_path=chapter.get("outline_path"),
        draft_path=None,
        review_status=None,
        ai_review_json=None,
    )


def _reset_chapter_range_for_regeneration(
    db_path: Path,
    book_id: int,
    book: dict[str, Any],
    chapter_start: int,
    chapter_end: int,
) -> dict[str, Any]:
    """Archive and clear a range only after every chapter passes safety checks."""
    if _autopilot_job_active(book_id):
        raise HTTPException(status_code=409, detail="全自动任务正在运行，请暂停后再删除正文")
    chapters = _chapter_range(db_path, book_id, chapter_start, chapter_end)
    work_dir = Path(str(book["work_dir"] or ""))
    for chapter in chapters:
        chapter_number = int(chapter["chapter_number"])
        for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]:
            if _step_job_active(book_id, chapter_number, step_name):
                raise HTTPException(
                    status_code=409,
                    detail=f"第{chapter_number}章 {step_name} 步骤仍在运行，请等待完成后再删除正文",
                )

    chapters_with_outputs = [chapter for chapter in chapters if _chapter_has_outputs(work_dir, chapter)]
    resets = [
        {
            "chapter": int(chapter["chapter_number"]),
            **_archive_and_reset_chapter_outputs(db_path, book_id, book, chapter),
        }
        for chapter in chapters_with_outputs
    ]
    output_numbers = {int(item["chapter"]) for item in resets}
    for chapter in chapters:
        if int(chapter["chapter_number"]) not in output_numbers:
            _reset_chapter_row_for_deleted_outputs(db_path, book_id, book, chapter)
    reset_numbers = [int(chapter["chapter_number"]) for chapter in chapters]
    return {
        "chapter_start": int(chapter_start),
        "chapter_end": int(chapter_end),
        "reset_chapters": reset_numbers,
        "skipped_chapters": [],
        "results": resets,
    }


def _remove_tracking_sections_for_chapters(text: str, chapter_numbers: list[int]) -> str:
    """Remove per-chapter tracking sections for the reset chapters."""
    numbers = sorted({int(n) for n in chapter_numbers if int(n) > 0})
    if not numbers or not text:
        return text
    number_pattern = "|".join(re.escape(str(n)) for n in numbers)
    cleaned = re.sub(
        rf"(?ms)^##\s*第(?:{number_pattern})章[^\n]*\n.*?(?=^##\s|\Z)",
        "",
        text,
    )
    return cleaned.strip() + ("\n" if cleaned.strip() else "")


def _sync_tracking_after_chapter_reset(
    db_path: Path,
    book_id: int,
    book: dict[str, Any],
    reset_chapters: list[int],
) -> None:
    """Drop deleted chapters from tracking memory and roll the current head back."""
    if not reset_chapters:
        return
    from generator.long_novel.l2_chapter_write import ensure_tracking_files, refresh_tracking_head

    work_dir = Path(str(book["work_dir"] or ""))
    ensure_tracking_files(work_dir, int(book.get("target_chapters") or 0))
    tracking_dir = work_dir / "追踪"
    for filename in ("全书进展.md", "时间线.md", "角色状态.md", "伏笔.md", "续写约束.md"):
        path = tracking_dir / filename
        if path.exists():
            path.write_text(
                _remove_tracking_sections_for_chapters(path.read_text(encoding="utf-8"), reset_chapters),
                encoding="utf-8",
            )

    written_chapters: list[tuple[int, dict[str, Any], Path]] = []
    for chapter in list_chapters(db_path, book_id):
        raw_draft_path = str(chapter.get("draft_path") or "").strip()
        if not raw_draft_path:
            continue
        draft_path = Path(raw_draft_path)
        if draft_path.exists():
            written_chapters.append((int(chapter.get("chapter_number") or 0), chapter, draft_path))
    written_chapters.sort(key=lambda item: item[0])

    if written_chapters:
        latest_number, _latest_chapter, latest_path = written_chapters[-1]
        draft = latest_path.read_text(encoding="utf-8")
        summary = draft[:220].replace("\n", " ")
        refresh_tracking_head(work_dir, latest_number, draft, summary_short=summary)
        update_book(db_path, book_id, current_chapter=latest_number)
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (tracking_dir / "上下文.md").write_text(
        "## 写作上下文\n\n"
        "- 当前进度：第0章尚未开始\n"
        "- 字数：0字\n"
        "- 本章摘要：暂无正文\n"
        f"- 上次更新时间：{now}\n"
        "- 下一章：第1章\n",
        encoding="utf-8",
    )
    (tracking_dir / "全书进展.md").write_text(
        "## 全书进展\n\n"
        "- 当前进度：第0章尚未开始\n"
        f"- 最近更新：{now}\n"
        "- 最新章节摘要：暂无正文\n"
        "- 下一章：第1章\n",
        encoding="utf-8",
    )
    update_book(db_path, book_id, current_chapter=0)


def _write_reset_idle_autopilot_snapshot(book: dict[str, Any], message: str) -> None:
    """Replace stale completed-writing snapshots after deleting chapter output."""
    from generator.long_novel.autopilot import write_autopilot_file

    work_dir = Path(str(book["work_dir"] or ""))
    write_autopilot_file(
        work_dir,
        {
            "state": "idle",
            "stage": "writing",
            "completed": [
                "premise",
                "world",
                "characters",
                "factions",
                "relations",
                "outline",
                "volume_outline",
                "chapter_outlines",
                "finalize",
            ],
            "detail": message,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        },
    )


# ── Books ────────────────────────────────────────────────────────────


@router.get("/books")
def api_list_books() -> dict[str, Any]:
    books = list_books(_db_path())
    return {"ok": True, "books": books, "count": len(books)}


@router.post("/books")
async def api_create_book(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="书名不能为空")
    genre = str(payload.get("genre") or "").strip()
    premise = str(payload.get("premise") or "").strip()
    target_chapters = int(payload.get("target_chapters") or 30)
    target_words = int(payload.get("target_words_per_chapter") or 3000)
    root = _project_root()
    work_dir = root / "data" / "books" / title
    book_id = create_book(
        _db_path(),
        title=title,
        genre=genre,
        premise=premise,
        target_chapters=target_chapters,
        target_words_per_chapter=target_words,
        work_dir=str(work_dir),
    )
    logger.info("Created book id=%s title=%s", book_id, title)
    return {"ok": True, "book_id": book_id, "message": f"已创建书籍「{title}」"}


@router.get("/books/{book_id}")
def api_get_book(book_id: int) -> dict[str, Any]:
    db = _db_path()
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    volumes = list_volumes(db, book_id)
    chapters = list_chapters(db, book_id)
    book["volumes"] = volumes
    book["chapters"] = chapters
    book["total_words"] = sum(c.get("actual_words", 0) for c in chapters)
    done_statuses = {"published", "draft", "final", "finalized", "done"}
    book["completed_chapters"] = sum(
        1
        for c in chapters
        if c.get("status") in done_statuses
        or bool(c.get("draft_path"))
        or int(c.get("actual_words") or 0) > 0
    )
    return {"ok": True, "book": book}


@router.put("/books/{book_id}")
async def api_update_book(book_id: int, request: Request) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    fields = {}
    for k in ("title", "genre", "premise", "target_chapters", "target_words_per_chapter"):
        if k in payload and payload[k] is not None:
            fields[k] = payload[k]
    if "status" in payload:
        fields["status"] = payload["status"]
    if fields:
        update_book(_db_path(), book_id, **fields)
    return {"ok": True, "message": "已更新"}


@router.delete("/books/{book_id}")
def api_delete_book(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    import shutil
    work_dir = Path(book.get("work_dir") or "")
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    delete_book(_db_path(), book_id)
    return {"ok": True, "message": f"已删除「{book['title']}」"}


# ── Volumes ──────────────────────────────────────────────────────────


@router.get("/books/{book_id}/volumes")
def api_list_volumes(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    volumes = list_volumes(_db_path(), book_id)
    return {"ok": True, "volumes": volumes}


@router.post("/books/{book_id}/volumes")
async def api_create_volume(book_id: int, request: Request) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    vol_num = int(payload.get("volume_number") or 1)
    title = str(payload.get("title") or f"第{vol_num}卷").strip()
    chapter_count = int(payload.get("chapter_count") or 30)
    upsert_volume(_db_path(), book_id, vol_num, title=title, chapter_count=chapter_count)
    update_book(_db_path(), book_id, total_volumes=max(book["total_volumes"] or 1, vol_num))
    return {"ok": True, "message": f"已创建第{vol_num}卷「{title}」"}


# ── Chapters ─────────────────────────────────────────────────────────


@router.get("/books/{book_id}/chapters")
def api_list_chapters(book_id: int, volume: int | None = None) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    chapters = list_chapters(_db_path(), book_id, volume_number=volume)
    return {"ok": True, "chapters": chapters, "count": len(chapters)}


@router.get("/books/{book_id}/chapters/{chapter_number}")
def api_get_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    # Load draft content if available
    content = ""
    if ch.get("draft_path"):
        p = Path(ch["draft_path"])
        if p.exists():
            content = p.read_text(encoding="utf-8")
    ch["content"] = content
    return {"ok": True, "chapter": ch}


@router.get("/books/{book_id}/next-chapter")
def api_next_chapter(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_next_chapter(_db_path(), book_id)
    if not ch:
        return {"ok": True, "chapter": None, "message": "所有章节已完成"}
    return {"ok": True, "chapter": ch, "message": f"下一章：第{ch['chapter_number']}章"}


# ── Context ──────────────────────────────────────────────────────────


@router.get("/books/{book_id}/context/{chapter_number}")
def api_chapter_context(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Assemble writing context for a chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"] or "")
    context: dict[str, Any] = {
        "book_title": book["title"],
        "chapter_number": chapter_number,
        "chapter_title": ch.get("title", ""),
        "target_words": ch.get("target_words", 3000),
    }

    from generator.long_novel.l2_chapter_write import assemble_context, ensure_tracking_files

    ensure_tracking_files(work_dir, int(book.get("target_chapters") or 0))
    context.update(
        assemble_context(
            work_dir,
            chapter_number,
            str(ch.get("title") or ""),
            int(ch.get("target_words") or book.get("target_words_per_chapter") or 3000),
        )
    )
    context["llm_context"] = _draft_context_manifest(context)
    return {"ok": True, "context": context}

    # Load chapter outline
    outline_path = ch.get("outline_path")
    if outline_path:
        op = Path(outline_path)
        if op.exists():
            context["outline"] = op.read_text(encoding="utf-8")

    # Load previous chapter summary
    if chapter_number > 1:
        prev_ch = get_chapter(_db_path(), book_id, chapter_number - 1)
        if prev_ch and prev_ch.get("draft_path"):
            dp = Path(prev_ch["draft_path"])
            if dp.exists():
                prev_text = dp.read_text(encoding="utf-8")
                context["prev_chapter_summary"] = prev_text[:500]
                context["prev_chapter_last_paragraph"] = prev_text[-300:]

    # Load relevant foreshadowing
    foreshadow_path = work_dir / "追踪" / "伏笔.md"
    if foreshadow_path.exists():
        foreshadow_text = foreshadow_path.read_text(encoding="utf-8")
        context["foreshadowing"] = foreshadow_text

    # Load character states
    char_state_path = work_dir / "追踪" / "角色状态.md"
    if char_state_path.exists():
        context["character_states"] = char_state_path.read_text(encoding="utf-8")

    progress_path = work_dir / "追踪" / "全书进展.md"
    if progress_path.exists():
        context["book_progress"] = progress_path.read_text(encoding="utf-8")

    constraints_path = work_dir / "追踪" / "续写约束.md"
    if constraints_path.exists():
        context["continuation_constraints"] = constraints_path.read_text(encoding="utf-8")

    return {"ok": True, "context": context}


@router.post("/books/{book_id}/tracking/ensure")
def api_ensure_tracking_files(book_id: int) -> dict[str, Any]:
    """Create missing long-memory tracking files for an existing book."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    from generator.long_novel.l2_chapter_write import ensure_tracking_files

    work_dir = Path(book["work_dir"])
    ensure_tracking_files(work_dir, int(book.get("target_chapters") or 0))
    files = sorted(p.name for p in (work_dir / "追踪").glob("*.md"))
    return {"ok": True, "files": files}


# ── Theme & Suggestions ──────────────────────────────────────────────


@router.get("/themes/trending")
def api_trending_themes() -> dict[str, Any]:
    genres = get_trending_genres(8)
    emotions = get_trending_emotions(6)
    hot = get_hot_themes(6)
    fanqie_keywords = get_fanqie_trending_keywords()
    return {
        "ok": True,
        "genres": genres,
        "emotions": emotions,
        "hot_themes": [{"theme": t.get("theme", ""), "genre": t.get("genre", ""),
                         "emotion": t.get("emotion", ""), "hint_title": t.get("hint_title", "")}
                        for t in hot],
        "fanqie_keywords": fanqie_keywords,
    }


@router.post("/themes/suggest-books")
async def api_suggest_books(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    target_type = str(payload.get("type") or "long")
    count = int(payload.get("count") or 5)

    client = _deepseek_client()

    suggestions = suggest_books(client, target_type=target_type, count=count)
    return {"ok": True, "suggestions": suggestions, "count": len(suggestions)}


@router.post("/themes/refresh-fanqie")
async def api_refresh_fanqie(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    date_str = str(payload.get("date") or "").strip() or None
    result = import_fanqie_trends(date_str=date_str)
    return {"ok": result["ok"], "source": result.get("source", "?"),
            "date": result.get("date", "?"),
            "books": result.get("books", 0),
            "categories": result.get("categories", 0),
            "message": f"Fanqie trends: {result.get('books', 0)} books across {result.get('categories', 0)} categories"}


@router.get("/themes/fanqie-dates")
def api_fanqie_dates() -> dict[str, Any]:
    dates = get_fanqie_dates()
    return {"ok": True, "dates": dates, "count": len(dates)}


@router.get("/themes/fanqie-keywords")
def api_fanqie_keywords() -> dict[str, Any]:
    keywords = get_fanqie_trending_keywords(20)
    return {"ok": True, "keywords": keywords, "count": len(keywords)}


# ── Artifact Viewer ───────────────────────────────────────────────────


@router.get("/books/{book_id}/artifact")
def api_read_artifact(book_id: int, path: str = "") -> dict[str, Any]:
    """Read a generated artifact file or list directory contents."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    safe_path = (work_dir / path).resolve()
    if not str(safe_path).startswith(str(work_dir.resolve())):
        raise HTTPException(status_code=403, detail="路径不允许")
    if not safe_path.exists():
        return {"ok": True, "content": "", "message": "文件尚未生成"}
    if safe_path.is_dir():
        files = []
        for f in sorted(safe_path.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "size": f.stat().st_size})
        return {"ok": True, "is_dir": True, "files": files, "path": path}
    content = safe_path.read_text(encoding="utf-8")
    return {"ok": True, "content": content, "path": path, "size": len(content)}


@router.post("/books/{book_id}/artifact")
async def api_write_artifact(book_id: int, request: Request) -> dict[str, Any]:
    """Save edits to a generated markdown artifact inside the book work dir."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    rel_path = str(payload.get("path") or "").strip()
    content = str(payload.get("content") or "")
    if not rel_path or not rel_path.endswith(".md"):
        raise HTTPException(status_code=400, detail="只能保存 markdown 文件")
    work_dir = Path(book["work_dir"]).resolve()
    safe_path = (work_dir / rel_path).resolve()
    if not str(safe_path).startswith(str(work_dir)):
        raise HTTPException(status_code=403, detail="路径不允许")
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": rel_path, "size": len(content)}


@router.post("/books/{book_id}/artifact/regenerate")
async def api_regenerate_artifact(book_id: int, request: Request) -> dict[str, Any]:
    """Regenerate one artifact file with optional user instructions."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    payload = await _json_payload(request)
    rel_path = str(payload.get("path") or "").strip()
    additional_prompt = str(payload.get("additional_prompt") or "").strip()
    if not rel_path or not rel_path.endswith(".md"):
        raise HTTPException(status_code=400, detail="只能重新生成 markdown 文件")
    work_dir = Path(book["work_dir"]).resolve()
    safe_path = (work_dir / rel_path).resolve()
    if not str(safe_path).startswith(str(work_dir)):
        raise HTTPException(status_code=403, detail="路径不允许")

    client = _deepseek_client(book)
    existing = safe_path.read_text(encoding="utf-8")[:4000] if safe_path.exists() else ""

    context_parts = []
    for ctx_rel in [
        "设定/题材定位.md",
        "设定/世界观/背景设定.md",
        "设定/世界观/力量体系.md",
        "设定/角色/_角色索引.md",
        "设定/关系.md",
        "大纲/大纲.md",
        "大纲/卷纲_第一卷.md",
    ]:
        p = work_dir / ctx_rel
        if p.exists() and ctx_rel != rel_path:
            context_parts.append(f"--- {ctx_rel} ---\n{p.read_text(encoding='utf-8')[:1800]}")

    system = "你是一位小说设定与大纲编辑。请只输出目标 markdown 文件正文，不要解释。"
    user = f"""请重新生成《{book['title']}》（{book['genre']}）的文件：{rel_path}

当前文件内容参考：
{existing or '（当前文件不存在或为空）'}

上游上下文：
{chr(10).join(context_parts)}

生成要求：
- 保持与已有题材定位、世界观、角色、关系、大纲一致。
- 如果用户补充要求与上游设定冲突，优先保持设定一致，并用不冲突的方式满足。
- 只输出 markdown 正文，不要说明保存路径。
"""
    if additional_prompt:
        user += f"\n用户本次补充要求：\n{additional_prompt}\n"

    completion = client.chat_completion(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        thinking_mode=True,
    )
    text = completion.text if hasattr(completion, "text") else str(completion)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(text.strip() + "\n", encoding="utf-8")
    return {"ok": True, "path": rel_path, "content": text, "size": len(text)}


@router.get("/books/{book_id}/tree")
def api_book_tree(book_id: int) -> dict[str, Any]:
    """Return the complete file tree of a book's work directory.

    Returns a nested structure so the frontend can render a file browser
    without making N recursive API calls.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])

    def _walk(dir_path: Path, rel_root: Path | None = None) -> dict[str, Any]:
        """Walk a directory, returning a nested dict."""
        if rel_root is None:
            rel_root = dir_path
        rel = str(dir_path.relative_to(rel_root)).replace("\\", "/")
        if rel == ".":
            rel = dir_path.name

        result: dict[str, Any] = {"name": dir_path.name, "path": rel, "is_dir": True, "children": []}
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return result

        for entry in entries:
            if entry.name.startswith("_step_"):
                continue  # skip internal step temp files
            child_rel = str(entry.relative_to(rel_root)).replace("\\", "/")
            if entry.is_dir():
                child = _walk(entry, rel_root)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                child = {
                    "name": entry.name,
                    "path": child_rel,
                    "is_dir": False,
                    "size": size,
                }
            result["children"].append(child)
        return result

    if not work_dir.exists():
        return {"ok": True, "tree": {"name": book.get("title", "无标题"), "path": ".", "is_dir": True, "children": []}}

    tree = _walk(work_dir)
    return {"ok": True, "tree": tree}


# ── Pipeline: Book Setup (L0) - async with polling ──────────────────


def _finalize_book_setup(book_id: int, book: dict[str, Any], work_dir: Path) -> None:
    """Create chapter rows from the generated 细纲 and flip the book to 'writing'.

    Idempotent: chapters that already have a draft are left untouched, so this
    is safe to re-run from either the manual finalize phase or the autopilot.
    """
    db = _db_path()
    for ch_num in range(1, book["target_chapters"] + 1):
        outline_path = work_dir / "大纲" / f"细纲_第{ch_num:03d}章.md"
        existing_chapter = get_chapter(db, book_id, ch_num)
        if existing_chapter and existing_chapter.get("draft_path"):
            continue
        upsert_chapter(
            db, book_id, volume_number=1, chapter_number=ch_num,
            title=(existing_chapter or {}).get("title") or f"第{ch_num}章",
            status=(existing_chapter or {}).get("status") or "outline_only",
            target_words=book["target_words_per_chapter"],
            outline_path=str(outline_path) if outline_path.exists() else None,
        )
    upsert_volume(db, book_id, 1, title="第一卷", chapter_count=book["target_chapters"], status="outlined")
    update_book(db, book_id, status="writing", total_volumes=1, current_volume=1)


def _autopilot_chapters_to_write(db: Path, book_id: int, count: int) -> list[int]:
    """Return the next ``count`` chapter numbers that still need a draft.

    A chapter "needs a draft" when it has no ``draft_path`` yet, so chapters the
    autopilot already finished — including ones flagged ``needs_human`` (they do
    have a saved draft) — are not rewritten on a later run.
    """
    chapters = list_chapters(db, book_id)
    pending = [
        int(c["chapter_number"])
        for c in sorted(chapters, key=lambda c: int(c.get("chapter_number") or 0))
        if not c.get("draft_path")
    ]
    return pending[: max(0, count)]


def _autopilot_chapters_to_write_range(db: Path, book_id: int, start: int, end: int) -> list[int]:
    """Return an explicit contiguous draft range, refusing to skip earlier gaps."""
    start = int(start)
    end = int(end)
    if start < 1 or end < 1:
        raise HTTPException(status_code=400, detail="正文起止章必须大于 0")
    if end < start:
        raise HTTPException(status_code=400, detail="正文结束章不能小于起始章")

    chapters = sorted(list_chapters(db, book_id), key=lambda c: int(c.get("chapter_number") or 0))
    if not chapters:
        raise HTTPException(status_code=400, detail="章节队列还没有生成，请先完成章节细纲并入库")

    by_number = {int(c.get("chapter_number") or 0): c for c in chapters}
    pending = [
        int(c["chapter_number"])
        for c in chapters
        if not c.get("draft_path")
    ]
    if not pending:
        raise HTTPException(status_code=400, detail="所有章节已有正文")

    earliest = pending[0]
    if start != earliest:
        raise HTTPException(
            status_code=400,
            detail=f"需要从第{earliest}章开始连续写，不能跳到第{start}章，否则追踪/伏笔会断。",
        )

    for chapter_number in range(start, end + 1):
        chapter = by_number.get(chapter_number)
        if not chapter:
            raise HTTPException(status_code=400, detail=f"章节队列缺少第{chapter_number}章，请先生成章节细纲并入库")
        if chapter.get("draft_path"):
            raise HTTPException(status_code=400, detail=f"第{chapter_number}章已有正文，请从最早未写章节连续生成")

    return list(range(start, end + 1))


_REVIEW_DIM_LABELS = {
    "continuity": "连续性",
    "logic": "逻辑",
    "plot_progress": "剧情推进",
    "character_integrity": "人物一致性",
    "environment": "环境/设定",
    "empathy": "共情",
    "architecture": "故事架构",
    "characters": "角色对话",
    "writing_quality": "文字质量",
    "consistency": "事实一致性",
}


def _short_review_text(value: Any, *, limit: int = 90) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _review_blocking_reasons(review: dict[str, Any], *, limit: int = 3) -> list[str]:
    """Return concise reasons explaining why review/rewrite is blocked."""
    reasons: list[str] = []
    dims = review.get("dimensions") if isinstance(review, dict) else {}
    if isinstance(dims, dict):
        for key, dim in dims.items():
            if not isinstance(dim, dict):
                continue
            verdict = str(dim.get("verdict") or "").upper()
            score = int(dim.get("score") or 0)
            pass_score = int(dim.get("pass_score") or review.get("pass_score") or 80)
            passed = bool(dim.get("passed"))
            if passed and verdict == "APPROVE" and score >= pass_score:
                continue
            label = _REVIEW_DIM_LABELS.get(str(key), str(key))
            detail = ""
            for item in (dim.get("findings") or []) + (dim.get("recommendations") or []):
                detail = _short_review_text(item)
                if detail:
                    break
            score_text = f"{score}/{pass_score}" if score else f"{verdict or '未通过'}"
            reasons.append(f"{label}{score_text}：{detail or (verdict or '未通过')}")
            if len(reasons) >= limit:
                return reasons

    for item in review.get("recommendations") or []:
        detail = _short_review_text(item)
        if detail:
            reasons.append(detail)
        if len(reasons) >= limit:
            return reasons

    summary = _short_review_text(review.get("summary") or review.get("overall") or "审查未通过")
    return reasons or [summary]


def _review_rewrite_reason(review: dict[str, Any]) -> str:
    return "；".join(_review_blocking_reasons(review))


def _autopilot_write_one_chapter(
    client: Any,
    db: Path,
    book_id: int,
    book: dict[str, Any],
    work_dir: Path,
    chapter_number: int,
    report: Callable[..., None],
    *,
    max_revisions: int = _AUTOPILOT_DEFAULT_MAX_REVISIONS,
) -> dict[str, Any]:
    """Run the visible per-step chapter flow and persist one chapter.

    Autopilot intentionally uses the same step functions as the manual
    workbench so every intermediate artifact remains visible:

    ``初稿 → 扩写判断 → 润色 → 去AI → 审查 → 按建议修改/复审 → 成稿``.

    A failed review triggers up to ``max_revisions`` concrete review-driven
    rewrites. If the gate still does not pass, the saved chapter is marked for
    human review and the multi-chapter autopilot can continue.
    """
    from generator.long_novel.l2_chapter_write import count_chinese_chars, strip_chapter_heading

    ch = get_chapter(db, book_id, chapter_number) or {}
    chapter_title = str(ch.get("title") or "")
    volume_number = int(ch.get("volume_number") or 1)
    target_words = int(ch.get("target_words") or book.get("target_words_per_chapter") or 3000)
    revision_limit = max(0, min(_AUTOPILOT_MAX_REVISIONS, int(max_revisions or 0)))
    revisions = 0
    step_trace: list[dict[str, Any]] = []

    def _report(status: str, detail: str = "", revisions: int = 0, **extra: Any) -> None:
        try:
            report(status, detail, revisions, **extra)
        except TypeError:
            report(status, detail, revisions)

    def _trace_step(
        step: str,
        label: str,
        status: str,
        *,
        word_count: int = 0,
        message: str = "",
    ) -> None:
        step_trace.append({
            "step": step,
            "label": label,
            "status": status,
            "word_count": int(word_count or 0),
            "message": message,
        })

    def _run_step(step: str, label: str, live_status: str) -> dict[str, Any]:
        _report(
            live_status,
            f"第{chapter_number}章：正在{label}",
            revisions,
            step=step,
            step_label=label,
            step_status="running",
            steps=list(step_trace),
        )
        result = _api_write_chapter_step_blocking(
            book_id,
            chapter_number,
            step,
            client=client,
        )
        status = "skipped" if result.get("skipped") else "done"
        message = str(result.get("message") or "")
        _trace_step(
            step,
            label,
            status,
            word_count=int(result.get("word_count") or result.get("final_words") or 0),
            message=message,
        )
        _report(
            live_status,
            message or f"第{chapter_number}章：{label}{'已跳过' if status == 'skipped' else '已完成'}",
            revisions,
            step=step,
            step_label=label,
            step_status=status,
            steps=list(step_trace),
        )
        return result

    # Mark writing without clobbering existing metadata (upsert overwrites all columns).
    upsert_chapter(
        db, book_id, volume_number, chapter_number,
        title=chapter_title, status="writing", target_words=target_words,
        actual_words=int(ch.get("actual_words") or 0),
        outline_path=ch.get("outline_path"), draft_path=ch.get("draft_path"),
        review_status=ch.get("review_status"), ai_review_json=ch.get("ai_review_json"),
    )
    if not _has_later_saved_chapter(db, book_id, chapter_number):
        update_book(db, book_id, current_chapter=chapter_number)

    # An interrupted unwritten chapter may have partial intermediate files.
    # Start the unattended run from a clean per-step chain.
    _cleanup_stale_step_outputs(work_dir, chapter_number, ["draft", "expand", "polish", "deslop", "review"])

    _run_step("draft", "生成初稿", "drafting")
    _run_step("expand", "扩写判断", "expanding")
    _run_step("polish", "润色", "polishing")
    _run_step("deslop", "去 AI", "deslopping")
    review_result = _run_step("review", "六维审查", "reviewing")
    review = dict(review_result.get("review") or {})

    while not review.get("passed") and revisions < revision_limit:
        revisions += 1
        reason = _review_rewrite_reason(review)
        _report(
            "revising",
            f"第{chapter_number}章：审查未通过，正在按建议修改（{revisions}/{revision_limit}）"
            + (f"：{reason}" if reason else ""),
            revisions,
            step="review_fix",
            step_label="按审查建议修改",
            step_status="running",
            steps=list(step_trace),
            reason=reason,
        )
        revised = _api_revise_chapter_step_blocking(
            book_id,
            chapter_number,
            "review",
            {},
            client=client,
        )
        review = dict(revised.get("review") or {})
        _trace_step(
            f"review_fix_{revisions}",
            f"按建议修改 #{revisions}",
            "done" if review.get("passed") else "needs_revision",
            word_count=int(revised.get("word_count") or 0),
            message=str(revised.get("message") or ""),
        )
        _report(
            "reviewing",
            f"第{chapter_number}章：第 {revisions} 次修改后已复审"
            + ("，审查通过" if review.get("passed") else "，仍有待修问题"),
            revisions,
            step="review",
            step_label="复审",
            step_status="done" if review.get("passed") else "needs_revision",
            steps=list(step_trace),
            reason="" if review.get("passed") else _review_rewrite_reason(review),
        )

    final_result = _run_step("finalize", "保存成稿", "finalizing")
    final_text = str(final_result.get("content") or "")
    final_words = int(final_result.get("final_words") or count_chinese_chars(final_text))
    reason = "" if review.get("passed") else _review_rewrite_reason(review)
    status = "passed" if review.get("passed") else "needs_human"
    if status == "needs_human":
        saved_chapter = get_chapter(db, book_id, chapter_number)
        if saved_chapter:
            _upsert_chapter_preserving(db, saved_chapter, status="needs_human")

    return {
        "chapter": chapter_number,
        "status": status,
        "words": final_words,
        "score": int(review.get("score") or 0),
        "review_overall": str(review.get("overall") or ""),
        "revisions": revisions,
        "reason": reason,
        "steps": step_trace,
        "review_summary": str(review.get("summary") or ""),
    }


@router.post("/books/{book_id}/setup-phase/{phase}")
async def api_start_setup_phase(book_id: int, phase: str, request: Request) -> dict[str, Any]:
    """Start a single L0 phase in background. Poll /setup-phase/{phase}/status for progress."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    work_dir = Path(book["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    progress_file = setup_dir(work_dir) / f"_setup_{phase}.json"

    import json as _json_lib

    def _write(s, d=""):
        progress_file.write_text(_json_lib.dumps({
            "status": s, "detail": d,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }, ensure_ascii=False), encoding="utf-8")

    valid_phases = ["premise", "world", "characters", "factions", "relations", "outline", "volume_outline", "chapter_outlines", "finalize"]
    if phase not in valid_phases:
        raise HTTPException(status_code=400, detail=f"未知阶段：{phase}")

    payload = await _json_payload(request)
    additional_prompt = str(payload.get("additional_prompt") or "").strip()

    # Clear any lingering cancel flag when explicitly starting a phase
    _set_cancel(book_id, False)

    def _run():
        from generator.long_novel.l0_book_setup import (
            run_l0_book_outline,
            run_l0_chapter_outlines,
            run_l0_characters,
            run_l0_factions,
            run_l0_premise,
            run_l0_relations,
            run_l0_volume_outline,
            run_l0_world,
        )
        client = _deepseek_client(book)

        def _cancelled() -> bool:
            if _is_cancelled(book_id):
                _write("cancelled", "已取消")
                return True
            return False

        try:
            if phase == "premise":
                _write("running", "AI正在分析题材趋势，生成题材定位文档...")
                if _cancelled():
                    return
                run_l0_premise(client, work_dir, book["title"], book["genre"], book["premise"], additional_prompt)
                fp = work_dir / "设定" / "题材定位.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "world":
                _write("running", "AI正在构建世界观背景和力量体系...")
                if _cancelled():
                    return
                run_l0_world(client, work_dir, book["title"], book["genre"], additional_prompt)
                fp = work_dir / "设定" / "世界观" / "背景设定.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "characters":
                _write("running", "AI正在设计主要角色和关系网络...")
                if _cancelled():
                    return
                result = run_l0_characters(client, work_dir, book["title"], book["genre"], additional_prompt)
                outputs = result.get("outputs", []) if isinstance(result, dict) else []
                # 选择第一个非索引文件作为预览
                preview = ""
                for rel in outputs:
                    if "_角色索引" in rel:
                        continue
                    fp = work_dir / rel
                    if fp.exists():
                        preview = fp.read_text(encoding="utf-8")[:2000]
                        break
                if not preview:
                    fp = work_dir / "设定" / "角色" / "角色设定.md"
                    preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else f"已生成 {len(outputs)} 个角色文件"
                _write("done", preview)
            elif phase == "factions":
                _write("running", "AI正在两阶段生成势力档案（先清单后并发详写）...")
                if _cancelled():
                    return
                result = run_l0_factions(client, work_dir, book["title"], book["genre"], additional_prompt)
                outputs = result.get("outputs", []) if isinstance(result, dict) else []
                preview = ""
                for rel in outputs:
                    if "_势力索引" in rel:
                        continue
                    fp = work_dir / rel
                    if fp.exists():
                        preview = fp.read_text(encoding="utf-8")[:2000]
                        break
                if not preview:
                    preview = f"已生成 {len(outputs)} 个势力文件"
                _write("done", preview)
            elif phase == "relations":
                _write("running", "AI正在梳理角色与势力之间的关系网络...")
                if _cancelled():
                    return
                run_l0_relations(client, work_dir, book["title"], book["genre"], additional_prompt)
                fp = work_dir / "设定" / "关系.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "outline":
                _write("running", "AI正在生成全书级大纲...")
                if _cancelled():
                    return
                run_l0_book_outline(client, work_dir, book["title"], book["genre"],
                                    book["target_chapters"], book["target_words_per_chapter"], additional_prompt)
                fp = work_dir / "大纲" / "大纲.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else ""
                _write("done", preview)
            elif phase == "volume_outline":
                _write("running", "AI正在把全书大纲拆成卷纲...")
                if _cancelled():
                    return
                result = run_l0_volume_outline(client, work_dir, book["title"], book["genre"],
                                               book["target_chapters"], book["target_words_per_chapter"], additional_prompt)
                outputs = result.get("outputs", []) if isinstance(result, dict) else []
                preview = ""
                for rel in outputs:
                    fp = work_dir / rel
                    if fp.exists():
                        preview = fp.read_text(encoding="utf-8")[:2000]
                        break
                if not preview:
                    preview = f"已生成 {len(outputs)} 个卷纲文件"
                _write("done", preview)
            elif phase == "chapter_outlines":
                _write("running", "AI正在根据大纲和卷纲生成章节细纲...")
                if _cancelled():
                    return
                result = run_l0_chapter_outlines(client, work_dir, book["title"], book["genre"],
                                                book["target_chapters"], book["target_words_per_chapter"], additional_prompt)
                count = result.get("chapters_generated", 0)
                fp = work_dir / "大纲" / "细纲_第001章.md"
                preview = fp.read_text(encoding="utf-8")[:2000] if fp.exists() else f"已生成 {count} 章细纲"
                _write("done", preview)
            elif phase == "finalize":
                _write("running", "正在写入数据库...")
                if _cancelled():
                    return
                _finalize_book_setup(book_id, book, work_dir)
                _write("done", f"开书设定完成，共{book['target_chapters']}章")
        except Exception as e:
            _write("error", str(e)[:300])
            logger.exception("Setup phase %s failed for book %s", phase, book_id)

    import threading
    _write("starting", "启动中...")
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"ok": True, "phase": phase, "message": f"{phase} 已启动"}


@router.get("/books/{book_id}/setup-phase/{phase}/status")
def api_setup_phase_status(book_id: int, phase: str) -> dict[str, Any]:
    """Poll status of a running setup phase."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    progress_file = setup_file_read(work_dir, f"_setup_{phase}.json")
    if not progress_file.exists():
        inferred = _inferred_setup_phase_status(work_dir, phase)
        if inferred is not None:
            return {"ok": True, **inferred}
        return {"ok": True, "status": "pending", "detail": "尚未开始"}
    import json as _json_lib
    import time as _time
    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    st = data.get("status", "?")
    # If status is running/starting but file hasn't been updated in 5+ minutes,
    # the generation thread is dead (likely app restart). Reset to cancelled.
    if st in ("running", "starting"):
        file_age = _time.time() - progress_file.stat().st_mtime
        if file_age > 300:  # 5 minutes
            data["status"] = "cancelled"
            data["detail"] = "进程中断（服务重启或超时），可重新生成"
            progress_file.write_text(_json_lib.dumps({
                **data, "updated_at": datetime.now().strftime("%H:%M:%S"),
            }, ensure_ascii=False), encoding="utf-8")
            st = "cancelled"
    return {"ok": True, "status": st, "detail": data.get("detail", ""),
            "updated_at": data.get("updated_at", "")}


def _inferred_setup_phase_status(work_dir: Path, phase: str) -> dict[str, Any] | None:
    """Infer setup status for autopilot and legacy books without phase files."""
    from generator.long_novel.autopilot import l0_phase_done, read_autopilot_file

    autopilot = read_autopilot_file(work_dir) or {}
    completed = {str(item) for item in (autopilot.get("completed") or [])}
    if phase in completed:
        return {
            "status": "done",
            "detail": "全自动生成已完成",
            "updated_at": str(autopilot.get("updated_at") or ""),
        }
    if autopilot.get("state") == "running" and autopilot.get("stage") == phase:
        return {
            "status": "running",
            "detail": str(autopilot.get("detail") or "全自动生成中"),
            "updated_at": str(autopilot.get("updated_at") or ""),
        }
    if phase != "finalize" and l0_phase_done(work_dir, phase):
        return {"status": "done", "detail": "检测到已有产物", "updated_at": ""}
    return None


# ── Autopilot: run the whole open-book pipeline in one background job ──


@router.post("/books/{book_id}/autopilot/start")
async def api_autopilot_start(book_id: int, request: Request) -> dict[str, Any]:
    """Run 设定 → 大纲 → 入库 →〔正文 × N〕as one background job.

    Body: ``{"additional_prompt": "...", "chapter_count": N, "chapter_start": 1,
    "chapter_end": 3, "max_revisions": 2}``.
    When ``chapter_count > 0`` the job continues past 入库 into the 正文 autopilot:
    it writes the next ``chapter_count`` unwritten chapters, each 初稿 → 扩写判断
    → 润色 → 去 AI → 审查 → 按建议修改/复审 → 保存成稿并更新追踪/伏笔/进度
    → 下一章.
    Everything streams to the same ``/autopilot/status`` /
    monitor panel. ``POST /cancel`` stops after the current stage or chapter.
    Stages and chapters already complete are skipped, so re-running resumes an
    interrupted book (and writing more chapters later just adds to it).
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    if _autopilot_job_active(book_id):
        raise HTTPException(status_code=409, detail="该书的全自动生成任务仍在运行")

    payload = await _json_payload(request)
    additional_prompt = str(payload.get("additional_prompt") or "").strip()
    range_start_raw = payload.get("chapter_start")
    range_end_raw = payload.get("chapter_end")
    has_chapter_range = range_start_raw not in (None, "") or range_end_raw not in (None, "")
    chapter_range: tuple[int, int] | None = None
    if has_chapter_range:
        if range_start_raw in (None, "") or range_end_raw in (None, ""):
            raise HTTPException(status_code=400, detail="正文范围需要同时填写起始章和结束章")
        try:
            chapter_start = int(range_start_raw)
            chapter_end = int(range_end_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="正文范围必须是章节数字") from None
        if chapter_start < 1 or chapter_end < chapter_start:
            raise HTTPException(status_code=400, detail="正文范围无效")
        chapter_range = (chapter_start, chapter_end)
        chapter_count = chapter_end - chapter_start + 1
        if list_chapters(_db_path(), book_id):
            _autopilot_chapters_to_write_range(_db_path(), book_id, chapter_start, chapter_end)
    else:
        chapter_count = max(0, int(payload.get("chapter_count") or 0))
    try:
        max_revisions = int(payload.get("max_revisions", _AUTOPILOT_DEFAULT_MAX_REVISIONS))
    except (TypeError, ValueError):
        max_revisions = _AUTOPILOT_DEFAULT_MAX_REVISIONS
    max_revisions = max(0, min(_AUTOPILOT_MAX_REVISIONS, max_revisions))

    work_dir = Path(book["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    _set_cancel(book_id, False)

    def _run() -> None:
        from generator.long_novel.autopilot import (
            AutopilotStage,
            build_l0_stages,
            run_chapter_loop,
            run_stages,
            write_autopilot_file,
        )

        try:
            client = _deepseek_client(book)
            stages = build_l0_stages(
                client, work_dir,
                title=book["title"], genre=book["genre"], premise=book.get("premise", ""),
                target_chapters=book["target_chapters"],
                words_per_chapter=book["target_words_per_chapter"],
                additional_prompt=additional_prompt,
            )
            stages.append(AutopilotStage(
                phase="finalize", label="入库",
                run=lambda: _finalize_book_setup(book_id, book, work_dir),
                is_done=lambda: (get_book(_db_path(), book_id) or {}).get("status") == "writing",
            ))

            def _setup_progress(snap: dict[str, Any]) -> None:
                # When chapters will follow, don't let setup's terminal "done"
                # stop the frontend poller — bridge it to a "running" handoff.
                if chapter_count > 0 and snap.get("state") == "done":
                    snap = {**snap, "state": "running", "detail": "设定完成，开始写正文…"}
                write_autopilot_file(work_dir, snap)

            setup_result = run_stages(
                stages,
                write_progress=_setup_progress,
                is_cancelled=lambda: _is_cancelled(book_id),
            )
            if setup_result.get("state") != "done":
                return  # error / cancelled snapshot already written
            if chapter_count <= 0 or _is_cancelled(book_id):
                return  # setup-only run; real terminal "done" already written

            db = _db_path()
            fresh_book = get_book(db, book_id) or book
            setup_completed = [s.phase for s in stages]
            if chapter_range:
                chapter_numbers = _autopilot_chapters_to_write_range(db, book_id, chapter_range[0], chapter_range[1])
            else:
                chapter_numbers = _autopilot_chapters_to_write(db, book_id, chapter_count)

            def _write_one(ch_num: int, report: Callable[..., None]) -> dict[str, Any]:
                return _autopilot_write_one_chapter(
                    client, db, book_id, fresh_book, work_dir, ch_num, report,
                    max_revisions=max_revisions,
                )

            run_chapter_loop(
                chapter_numbers,
                write_chapter=_write_one,
                write_progress=lambda snap: write_autopilot_file(work_dir, snap),
                is_cancelled=lambda: _is_cancelled(book_id),
                setup_completed=setup_completed,
            )
        except Exception as exc:
            from generator.long_novel.autopilot import write_autopilot_file
            write_autopilot_file(work_dir, {
                "state": "error", "stage": "", "detail": str(exc)[:300],
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            })
            logger.exception("autopilot failed for book %s", book_id)
        finally:
            _autopilot_job_mark(book_id, False)

    from generator.long_novel.autopilot import write_autopilot_file
    write_autopilot_file(work_dir, {
        "state": "running", "stage": "", "detail": "启动中...", "total": 0,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    })
    _autopilot_job_mark(book_id, True)
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _autopilot_job_mark(book_id, False)
        raise
    msg = "autopilot 已启动"
    if chapter_count > 0:
        if chapter_range:
            msg = f"autopilot 已启动（设定 + 正文 第{chapter_range[0]}-{chapter_range[1]}章）"
        else:
            msg = f"autopilot 已启动（设定 + 正文 {chapter_count} 章）"
    return {"ok": True, "message": msg, "chapter_count": chapter_count, "chapter_range": chapter_range}


@router.get("/books/{book_id}/autopilot/status")
def api_autopilot_status(book_id: int) -> dict[str, Any]:
    """Poll autopilot progress. Flags a dead worker (>5 min without update)."""
    db = _db_path()
    book = get_book(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    from generator.long_novel.autopilot import AUTOPILOT_FILE, read_autopilot_file, write_autopilot_file

    work_dir = Path(book["work_dir"])
    data = read_autopilot_file(work_dir)
    if not data:
        return {"ok": True, "state": "idle"}
    if data.get("state") == "running" and not _autopilot_job_active(book_id):
        progress_file = setup_file_read(work_dir, AUTOPILOT_FILE)
        if progress_file.exists() and (time.time() - progress_file.stat().st_mtime) > 300:
            data["state"] = "cancelled"
            data["detail"] = "进程中断（服务重启或超时），可重新开始"
            data["updated_at"] = datetime.now().strftime("%H:%M:%S")
            write_autopilot_file(work_dir, data)
    data = _repair_invalid_autopilot_writing_snapshot(work_dir, data)
    data = _sync_paused_autopilot_snapshot(book_id, work_dir, data)
    return {"ok": True, **data}


def _repair_invalid_autopilot_writing_snapshot(work_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Fix stale snapshots that say done while chapter writing is incomplete."""
    writing = data.get("writing")
    if data.get("state") not in {"done", "error"} or not isinstance(writing, dict):
        return data
    total = int(writing.get("total") or 0)
    done = int(writing.get("done") or 0)
    if total <= 0 or done >= total:
        return data

    from generator.long_novel.autopilot import write_autopilot_file

    failed_at = data.get("failed_at") or writing.get("current") or 1
    detail = str(data.get("detail") or "")
    if "完成" in detail or "同步" in detail or "继续全自动" in detail or not detail:
        detail = f"第{failed_at}章生成失败，正文完成 {done}/{total} 章"
    repaired = {
        **data,
        "state": "error",
        "phase": "writing",
        "stage": "writing",
        "detail": detail,
        "failed_at": failed_at,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }
    write_autopilot_file(work_dir, repaired)
    return repaired


def _sync_paused_autopilot_snapshot(book_id: int, work_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Merge manually completed setup phases into a paused autopilot snapshot."""
    if _autopilot_job_active(book_id) or data.get("state") not in {"cancelled", "error"}:
        return data

    from generator.long_novel.autopilot import write_autopilot_file

    stages = [
        ("premise", "题材定位"),
        ("world", "世界观"),
        ("characters", "角色设计"),
        ("factions", "势力"),
        ("relations", "关系"),
        ("outline", "全书大纲"),
        ("volume_outline", "卷纲"),
        ("chapter_outlines", "章节细纲"),
        ("finalize", "入库"),
    ]
    completed = {str(phase) for phase in (data.get("completed") or [])}
    for phase, _label in stages:
        progress_file = setup_file_read(work_dir, f"_setup_{phase}.json")
        if not progress_file.exists():
            continue
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if progress.get("status") == "done":
            completed.add(phase)

    ordered_completed = [phase for phase, _label in stages if phase in completed]
    next_index = next((index for index, (phase, _label) in enumerate(stages) if phase not in completed), len(stages))
    next_phase, next_label = stages[next_index] if next_index < len(stages) else ("", "")
    setup_done = next_index == len(stages)
    writing = data.get("writing")
    has_writing = isinstance(writing, dict) and int(writing.get("total") or 0) > 0
    writing_done = bool(
        has_writing
        and int(writing.get("done") or 0) >= int(writing.get("total") or 0)
    )
    all_done = setup_done and (not has_writing or writing_done)
    display_stage = "writing" if setup_done and has_writing and not writing_done else next_phase
    display_label = "正文" if display_stage == "writing" else next_label
    if (
        ordered_completed == list(data.get("completed") or [])
        and display_stage == str(data.get("stage") or "")
        and (not setup_done or data.get("state") == "done" or has_writing)
    ):
        return data

    detail = "全自动生成完成" if all_done else "已同步手动生成结果，可继续全自动"
    if has_writing and not writing_done:
        detail = str(data.get("detail") or detail)
    synced = {
        **data,
        "state": "done" if all_done else data.get("state"),
        "stage": display_stage,
        "label": display_label,
        "index": next_index,
        "total": len(stages),
        "stage_status": "done" if setup_done else "",
        "detail": detail,
        "completed": ordered_completed,
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }
    write_autopilot_file(work_dir, synced)
    return synced


@router.post("/books/{book_id}/extend-chapters")
async def api_extend_chapters(book_id: int, request: Request) -> dict[str, Any]:
    """Extend a book beyond its current planned chapter count.

    This only generates new chapter outlines and inserts new chapter rows; it
    does not rewrite existing outlines or finished drafts.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    payload = await _json_payload(request)
    work_dir = Path(book["work_dir"])
    chapters = list_chapters(_db_path(), book_id)
    max_db_chapter = max((int(c.get("chapter_number") or 0) for c in chapters), default=0)
    old_target = max(int(book.get("target_chapters") or 0), max_db_chapter, _max_outline_chapter(work_dir))

    if payload.get("new_target_chapters") is not None:
        new_target = int(payload.get("new_target_chapters") or 0)
    else:
        additional = int(payload.get("additional_chapters") or 0)
        new_target = old_target + additional

    if new_target <= old_target:
        raise HTTPException(status_code=400, detail=f"新总章数必须大于当前 {old_target} 章")
    if new_target > 2000:
        raise HTTPException(status_code=400, detail="总章数不能超过 2000")

    additional_prompt = str(payload.get("additional_prompt") or "").strip()
    work_dir.mkdir(parents=True, exist_ok=True)
    progress_file = setup_dir(work_dir) / "_extend_chapters.json"
    # Migrate legacy location if it exists
    legacy_extend = work_dir / "_extend_chapters.json"
    if legacy_extend.exists() and not progress_file.exists():
        try:
            progress_file.write_text(legacy_extend.read_text(encoding="utf-8"), encoding="utf-8")
            legacy_extend.unlink()
        except Exception:
            pass

    import json as _json_lib
    import time as _time

    if progress_file.exists():
        try:
            existing_progress = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
            if existing_progress.get("status") in ("starting", "running"):
                file_age = _time.time() - progress_file.stat().st_mtime
                if file_age <= 600:
                    raise HTTPException(status_code=409, detail="已有追加章节任务正在运行")
        except HTTPException:
            raise
        except Exception:
            pass

    def _write(s: str, d: str = "", extra: dict[str, Any] | None = None) -> None:
        progress_file.write_text(_json_lib.dumps({
            "status": s,
            "detail": d,
            "old_target_chapters": old_target,
            "new_target_chapters": new_target,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
            **(extra or {}),
        }, ensure_ascii=False), encoding="utf-8")

    def _run() -> None:
        try:
            _write("running", f"正在生成第{old_target + 1}-{new_target}章续写规划与细纲...")
            from generator.long_novel.l0_book_setup import run_l0_extend_chapter_outlines

            client = _deepseek_client(book)
            result = run_l0_extend_chapter_outlines(
                client,
                work_dir,
                book["title"],
                book["genre"],
                old_target,
                new_target,
                int(book.get("target_words_per_chapter") or 3000),
                additional_prompt,
            )

            for ch_num in range(old_target + 1, new_target + 1):
                outline_path = work_dir / "大纲" / f"细纲_第{ch_num:03d}章.md"
                volume_number = max(1, ((ch_num - 1) // 30) + 1)
                upsert_chapter(
                    _db_path(),
                    book_id,
                    volume_number=volume_number,
                    chapter_number=ch_num,
                    title=_outline_title(outline_path, ch_num),
                    status="outline_only",
                    target_words=int(book.get("target_words_per_chapter") or 3000),
                    outline_path=str(outline_path) if outline_path.exists() else None,
                )

            total_volumes = max(int(book.get("total_volumes") or 1), ((new_target - 1) // 30) + 1)
            existing_volumes = {int(v.get("volume_number") or 0) for v in list_volumes(_db_path(), book_id)}
            for vol_num in range(1, total_volumes + 1):
                if vol_num not in existing_volumes:
                    first_ch = (vol_num - 1) * 30 + 1
                    chapter_count = max(0, min(30, new_target - first_ch + 1))
                    upsert_volume(
                        _db_path(),
                        book_id,
                        vol_num,
                        title=f"第{vol_num}卷",
                        chapter_count=chapter_count,
                        status="outlined",
                    )

            update_book(
                _db_path(),
                book_id,
                target_chapters=new_target,
                total_volumes=total_volumes,
                status="writing",
            )
            _write(
                "done",
                f"已追加第{old_target + 1}-{new_target}章，共{new_target - old_target}章",
                {"result": result},
            )
        except Exception as e:
            _write("error", str(e)[:500])
            logger.exception("Extend chapters failed for book %s", book_id)

    _write("starting", "启动中...")
    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True,
        "old_target_chapters": old_target,
        "new_target_chapters": new_target,
        "message": f"已启动追加章节：第{old_target + 1}-{new_target}章",
    }


@router.get("/books/{book_id}/extend-chapters/status")
def api_extend_chapters_status(book_id: int) -> dict[str, Any]:
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    progress_file = setup_file_read(Path(book["work_dir"]), "_extend_chapters.json")
    if not progress_file.exists():
        return {"ok": True, "status": "pending", "detail": "尚未开始"}
    import json as _json_lib
    import time as _time

    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    st = data.get("status", "pending")
    if st in ("running", "starting"):
        file_age = _time.time() - progress_file.stat().st_mtime
        if file_age > 600:
            data["status"] = "cancelled"
            data["detail"] = "进程中断（服务重启或超时），可重新追加"
            data["updated_at"] = datetime.now().strftime("%H:%M:%S")
            progress_file.write_text(_json_lib.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, **data}


@router.get("/books/{book_id}/setup-progress")
def api_setup_progress(book_id: int) -> dict[str, Any]:
    """Poll the current L0 setup progress."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    progress_file = setup_file_read(Path(book["work_dir"]), "_setup_progress.json")
    if not progress_file.exists():
        return {"ok": True, "progress": {"phase": "pending", "status": "not_started", "detail": "尚未开始"}}
    import json as _json_lib
    data = _json_lib.loads(progress_file.read_text(encoding="utf-8"))
    return {"ok": True, "progress": data}


# ── Real-time progress + cancel ────────────────────────────────────────


@router.get("/books/{book_id}/progress")
def api_book_progress(book_id: int) -> dict[str, Any]:
    """Get current progress of all operations for this book.

    Returns status of each setup phase (pending/running/done/error/cancelled)
    and chapter writing progress.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    import json as _json_lib
    work_dir = Path(book["work_dir"])

    phases = ["premise", "world", "characters", "outline", "volume_outline", "chapter_outlines", "extend_chapters"]
    phase_statuses = {}
    active_phase = None

    for ph in phases:
        fname = "_extend_chapters.json" if ph == "extend_chapters" else f"_setup_{ph}.json"
        pf = setup_file_read(work_dir, fname)
        if pf.exists():
            data = _json_lib.loads(pf.read_text(encoding="utf-8"))
            st = data.get("status", "?")
            phase_statuses[ph] = {
                "status": st,
                "detail": data.get("detail", ""),
                "updated_at": data.get("updated_at", ""),
            }
            if st == "running":
                active_phase = ph
        else:
            phase_statuses[ph] = {"status": "pending", "detail": "尚未开始", "updated_at": ""}

    return {
        "ok": True,
        "book_status": book.get("status"),
        "phase_statuses": phase_statuses,
        "active_phase": active_phase,
        "cancelled": _is_cancelled(book_id),
    }


@router.post("/books/{book_id}/cancel")
def api_cancel_book_operation(book_id: int) -> dict[str, Any]:
    """Cancel any running operation for this book."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    _set_cancel(book_id, True)
    logger.info("Cancel requested for book %s", book_id)
    return {"ok": True, "message": "已发送取消信号，当前操作将在下一个检查点停止"}


@router.post("/books/{book_id}/resume")
def api_resume_book_operation(book_id: int) -> dict[str, Any]:
    """Clear the cancel flag so new operations can start."""
    _set_cancel(book_id, False)
    return {"ok": True, "message": "已清除取消状态"}


# ── Prompt viewing ─────────────────────────────────────────────────────


_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_PHASE_PROMPT_INFO = {
    "premise": {
        "label": "题材定位",
        "system_file": "l0_premise_system.txt",
        "user_file": "l0_premise_user.txt",
        "placeholders": ["title", "genre", "genre_note", "premise"],
        "user_template": """请为以下长篇小说撰写题材定位文档：

书名：{title}
题材：{genre}
一句话梗概：{premise}

请按以下结构输出（Markdown格式）：

## 题材定位
- 核心梗概（三分法：表层/中层/深层）
- 目标读者画像
- 题材竞争力分析

## 卖点设计
- 核心卖点（至少3个）
- 情绪卖点
- 创新点

## 注意事项
- 该题材常见坑点
- 规避建议""",
    },
    "world": {
        "label": "世界观",
        "system_file": "l0_world_system.txt",
        "user_file": "l0_world_user.txt",
        "placeholders": ["title", "genre", "section_name", "section_focus", "premise_text"],
        "user_template": """请为以下长篇小说设计世界观：

书名：{title}
题材：{genre}
题材定位参考：{premise_summary}

请生成以下文件内容：

## 背景设定（设定/世界观/背景设定.md）
- 时代背景（古代/现代/架空）
- 地理版图（主要区域及特征）
- 历史大事件（影响当前格局的关键事件）

## 力量体系（设定/世界观/力量体系.md）
- 修炼/能力等级体系（如有）
- 核心规则与限制
- 特殊设定（如有）

## 势力分布（设定/势力/主要势力.md）
- 各大势力的名称、定位、关系
- 势力间的冲突与平衡""",
    },
    "characters": {
        "label": "角色设计",
        "system_file": "l0_characters_roster_system.txt",
        "user_file": "l0_characters_roster_user.txt",
        "placeholders": ["title", "genre", "premise_text"],
        "related_prompts": ["characters_detail"],
        "user_template": """请为以下长篇小说设计主要角色：

书名：{title}
题材：{genre}
已有设定：{settings_summary}

请设计3-5个核心角色，每个角色包含：

## 主角：[角色名]
- 身份背景（出身/职业/秘密）
- 性格特质（3个核心特质+1个缺陷）
- 核心动机（想要什么/害怕什么）
- 成长弧线（起点→终点）
- 关键关系（与其他角色的关系）
- 语言风格（说话方式/口头禅）
- 能力/技能（如有）

## 反派：[角色名]
- 同上结构

## 配角（1-3个）
- 简化版角色卡

## 角色关系图
描述角色之间的核心关系网络。""",
    },
    "factions": {
        "label": "势力",
        "system_file": "l0_factions_roster_system.txt",
        "user_file": "l0_factions_roster_user.txt",
        "placeholders": ["title", "genre", "context_text"],
        "related_prompts": ["factions_detail"],
        "user_template": """两阶段生成势力档案。

阶段1（pro+thinking）：让 LLM 返回 JSON 清单 [{name,type,brief}, ...] 共 3-6 个势力。
阶段2（flash 并发）：对每个势力分别详写 设定/势力/{name}.md。

阶段1 prompt 上下文：
- 题材定位（首 1500 字）
- 世界观/背景设定（首 1500 字）
- 世界观/力量体系（首 1500 字）
- 角色/_角色索引（首 1500 字）

阶段2 每项 prompt 模板：
「为《{title}》撰写势力「{name}」的完整档案。
结构：起源历史/组织架构/核心人物/势力范围/资源底牌/与其他势力关系/在剧情中的作用。600-1200 字。」
""",
    },
    "relations": {
        "label": "关系",
        "system_file": "l0_relations_system.txt",
        "user_file": "l0_relations_user.txt",
        "placeholders": ["title", "genre", "char_list", "faction_list", "context_text"],
        "user_template": """单次调用生成 设定/关系.md。

输入：
- 设定/角色/_角色索引.md
- 设定/势力/_势力索引.md
- 设定/题材定位.md
- 角色文件列表（仅文件名）
- 势力文件列表（仅文件名）

输出结构：
## 一、人物关系
## 二、人物-势力归属
## 三、势力之间的关系
## 四、关系演化时间线
""",
    },
    "outline": {
        "label": "大纲",
        "system_file": "l0_outline_system.txt",
        "user_file": "l0_outline_user.txt",
        "placeholders": ["title", "genre", "target_chapters", "words_per_chapter", "all_settings"],
        "related_prompts": ["extend_chapters"],
        "user_template": """请为以下长篇小说设计全书大纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定（必须继承，尤其是角色名、身份、动机、关系、世界观规则）：{all_settings}

一致性硬约束：
- 人物只能沿用“设定/角色/角色设定.md”中的核心角色；不得改名、换身份、换动机或重新发明主角团。
- 临时配角必须标注为临时配角，不能替代既有核心角色。
- 事件推进必须服从题材定位、世界观背景、角色关系图，不能另起一套世界观或人物关系。

只输出全书级结构，不要写章节细纲。请包含：
- 全书核心主线
- 主要人物线与关系变化
- 核心矛盾升级
- 爽点/情绪曲线
- 重要伏笔与回收计划
- 按卷划分建议""",
    },
    "volume_outline": {
        "label": "卷纲",
        "system_file": "l0_volume_outline_system.txt",
        "user_file": "l0_volume_outline_user.txt",
        "placeholders": [
            "title", "genre", "volume_name", "target_chapters", "words_per_chapter", "vol_num", "ch_start",
            "ch_end", "chapter_count", "volume_words", "plan_title", "all_settings", "book_outline", "full_plan_brief",
        ],
        "user_template": """请基于已有设定和全书大纲生成卷纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定：{all_settings}
全书大纲：{book_outline}

一致性硬约束：
- 卷纲必须承接全书大纲，并继续沿用角色设计中的人物名、身份、动机和关系。
- 不得新增核心主角/反派替换已设计角色；不得把已设计人物改成另一套关系。
- 每卷的人物线必须说明这些既有角色的关系如何变化。

只输出卷级结构，不要写章节细纲。每卷包含：卷名、章节范围、核心事件、起始状态→结束状态、人物线、爽点、伏笔。

输出格式要求：
- 每一卷用二级标题分隔，例如 ## 第一卷：卷名、## 第二卷：卷名。
- 系统会按卷标题拆成 大纲/卷纲_第一卷.md、卷纲_第二卷.md 等独立文件。""",
    },
    "chapter_outlines": {
        "label": "章节细纲",
        "system_file": "l0_chapter_outlines_system.txt",
        "user_file": "l0_chapter_outlines_user.txt",
        "placeholders": ["title", "genre", "target_chapters", "words_per_chapter", "outline_context"],
        "related_prompts": ["chapter_outlines_fill"],
        "user_template": """请基于已有设定、全书大纲和卷纲生成章节细纲：

书名：{title} 题材：{genre}
计划章数：{target_chapters}章 每章约{words_per_chapter}字
已有设定：{all_settings}
全书大纲：{book_outline}
卷纲：{volume_outline}

一致性硬约束：
- 章节细纲只能使用角色设计、全书大纲、卷纲中已经确立的核心人物与关系。
- 每章“出场角色”必须优先从角色设定中选择，并保持身份、动机、说话方式、关系不变。
- 不得凭空替换人物名、阵营、情感线或世界观规则；确需新增路人/工具人时标注为临时配角。

每章：核心事件、章首钩子、主要冲突、爽点、章尾钩子、出场角色、伏笔、情绪目标。
用"## 第N章"分隔每章。""",
    },
}

_CHAPTER_PROMPT_INFO = {
    "characters_detail": {
        "label": "角色详情卡",
        "system_file": "l0_characters_detail_system.txt",
        "user_file": "l0_characters_detail_user.txt",
        "placeholders": ["title", "genre", "name", "role", "brief", "premise_text"],
    },
    "factions_detail": {
        "label": "势力详情档案",
        "system_file": "l0_factions_detail_system.txt",
        "user_file": "l0_factions_detail_user.txt",
        "placeholders": ["title", "genre", "name", "ftype", "brief", "context_text"],
    },
    "chapter_outlines_fill": {
        "label": "补全章节细纲",
        "system_file": "l0_chapter_outlines_fill_system.txt",
        "user_file": "l0_chapter_outlines_fill_user.txt",
        "placeholders": ["title", "genre", "batch_start", "batch_end", "words_per_chapter", "all_settings", "prev_outline"],
    },
    "extend_chapters": {
        "label": "追加章节规划",
        "system_file": "l0_extend_chapters_system.txt",
        "user_file": "l0_extend_chapters_user.txt",
        "placeholders": [
            "title", "genre", "start_ch", "end_ch", "old_target_chapters",
            "new_target_chapters", "words_per_chapter", "extension_context",
        ],
    },
    "draft": {
        "label": "正文初稿",
        "system_file": "l2_draft_system.txt",
        "user_file": "l2_draft_user.txt",
        "placeholders": ["chapter_number", "chapter_title", "target_words", "context_sections"],
    },
    "expand": {
        "label": "扩写",
        "system_file": "l2_expand_system.txt",
        "user_file": "l2_expand_user.txt",
        "placeholders": ["draft", "current_words", "target_words", "shortfall"],
    },
    "polish": {
        "label": "润色",
        "system_file": "l2_polish_system.txt",
        "user_file": "l2_polish_user.txt",
        "placeholders": ["draft"],
    },
    "deslop": {
        "label": "去 AI",
        "system_file": "l2_deslop_system.txt",
        "user_file": "l2_deslop_user.txt",
        "placeholders": ["draft", "hit_text"],
        "related_prompts": ["deslop_fix"],
    },
    "review": {
        "label": "审查",
        "system_file": "l4_story_review_system.txt",
        "user_file": "l4_story_review_user.txt",
        "placeholders": ["chapter_number", "outline", "context", "chapter_text", "continuity_rule"],
        "related_prompts": ["review_fix"],
    },
    "finalize": {
        "label": "成稿/长期记忆",
        "system_file": "l2_tracking_memory_system.txt",
        "user_file": "l2_tracking_memory_user.txt",
        "placeholders": ["chapter_number", "tracking_context", "chapter_text"],
    },
    "continuity": {
        "label": "连续性检查",
        "system_file": "l2_continuity_system.txt",
        "user_file": "l2_continuity_user.txt",
        "placeholders": ["previous_chapter", "chapter_text", "character_profiles", "book_outline", "volume_outline"],
    },
    "review_fix": {
        "label": "按审查建议修改",
        "system_file": "l2_review_fix_system.txt",
        "user_file": "l2_review_fix_user.txt",
        "placeholders": ["chapter_number", "outline", "suggestions", "extra_prompt", "source"],
    },
    "deslop_fix": {
        "label": "继续降低 AI 味",
        "system_file": "l2_deslop_fix_system.txt",
        "user_file": "l2_deslop_fix_user.txt",
        "placeholders": ["chapter_number", "suggestions", "extra_prompt", "source"],
    },
}


def _prompt_file_text(filename: str | None) -> str:
    if not filename:
        return ""
    path = _PROMPTS_DIR / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


class _PromptValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_prompt_template(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format_map(_PromptValues({k: "" if v is None else v for k, v in values.items()}))
    except Exception as exc:
        logger.warning("api prompt template render failed: %s", exc)
        return template


def _load_prompt_template(filename: str, fallback: str) -> str:
    text = _prompt_file_text(filename).strip()
    return text or fallback


def _missing_prompt_placeholders(content: str, placeholders: list[str]) -> list[str]:
    """Return required template placeholders that are absent from content."""
    return [p for p in placeholders if "{" + p + "}" not in content]


_PROMPT_THINKING_MODE = {
    "expand": False,
    "polish": False,
    "deslop": False,
    "continuity": False,
}


def _prompt_call_parameters(phase: str) -> dict[str, Any]:
    """Return the effective default parameters used by a prompt phase."""
    settings = _deepseek_client().settings
    thinking_mode = _PROMPT_THINKING_MODE.get(phase, True)
    return {
        "model": settings.model,
        "thinking_mode": thinking_mode,
        "temperature": 0.8,
        "max_output_tokens": settings.max_output_tokens,
        "timeout_seconds": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }


def _save_prompt_file(filename: str, content: str) -> str:
    path = _PROMPTS_DIR / filename
    if path.suffix.lower() != ".txt":
        raise HTTPException(status_code=400, detail="只支持编辑 .txt 格式的 prompt 文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    return backup.name


@router.get("/prompts/{phase}")
def api_get_phase_prompt(phase: str) -> dict[str, Any]:
    """Return system and user prompt templates for a setup phase or chapter step."""
    info = _PHASE_PROMPT_INFO.get(phase)
    kind = "setup"
    if not info:
        info = _CHAPTER_PROMPT_INFO.get(phase)
        kind = "chapter"
    if not info:
        raise HTTPException(status_code=404, detail=f"未知阶段：{phase}")

    system_file = str(info.get("system_file") or "")
    user_file = str(info.get("user_file") or "")
    system_prompt = _prompt_file_text(system_file)
    user_template = _prompt_file_text(user_file) if user_file else str(info.get("user_template") or "")

    return {
        "ok": True,
        "phase": phase,
        "kind": kind,
        "label": info["label"],
        "system_file": system_file,
        "user_file": user_file,
        "editable_system": bool(system_file),
        "editable_user": bool(user_file),
        "placeholders": list(info.get("placeholders") or []),
        "related_prompts": list(info.get("related_prompts") or []),
        "call_parameters": _prompt_call_parameters(phase),
        "system_prompt": system_prompt,
        "user_template": user_template,
    }


@router.post("/prompts/{phase}")
async def api_save_phase_prompt(phase: str, request: Request) -> dict[str, Any]:
    """Save editable long-novel prompt templates."""
    info = _PHASE_PROMPT_INFO.get(phase) or _CHAPTER_PROMPT_INFO.get(phase)
    if not info:
        raise HTTPException(status_code=404, detail=f"未知阶段：{phase}")
    payload = await _json_payload(request)
    saved: list[str] = []
    backups: list[str] = []
    if "system_prompt" in payload:
        filename = str(info.get("system_file") or "")
        if not filename:
            raise HTTPException(status_code=400, detail="该阶段没有可编辑的 system prompt 文件")
        content = str(payload.get("system_prompt") or "")
        if not content.strip():
            raise HTTPException(status_code=400, detail="system prompt 不能为空")
        backups.append(_save_prompt_file(filename, content))
        saved.append(filename)
    if "user_template" in payload:
        filename = str(info.get("user_file") or "")
        if not filename:
            raise HTTPException(status_code=400, detail="该阶段的 user prompt 仍由源码拼装，暂不能保存为文件")
        content = str(payload.get("user_template") or "")
        if not content.strip():
            raise HTTPException(status_code=400, detail="user prompt 不能为空")
        missing = _missing_prompt_placeholders(content, list(info.get("placeholders") or []))
        if missing:
            missing_text = "、".join("{" + p + "}" for p in missing)
            raise HTTPException(status_code=400, detail=f"user prompt 缺少必要变量：{missing_text}")
        backups.append(_save_prompt_file(filename, content))
        saved.append(filename)
    if not saved:
        raise HTTPException(status_code=400, detail="没有可保存的提示词内容")
    logger.info("long novel prompts saved phase=%s files=%s", phase, saved)
    return {"ok": True, "phase": phase, "saved": saved, "backups": backups, "message": "提示词已保存，下一次运行会使用新内容"}


@router.post("/prompts/{phase}/revert")
def api_revert_phase_prompt(phase: str) -> dict[str, Any]:
    """Restore editable long-novel prompt templates from .bak files."""
    info = _PHASE_PROMPT_INFO.get(phase) or _CHAPTER_PROMPT_INFO.get(phase)
    if not info:
        raise HTTPException(status_code=404, detail=f"未知阶段：{phase}")
    restored: list[str] = []
    for key in ("system_file", "user_file"):
        filename = str(info.get(key) or "")
        if not filename:
            continue
        path = _PROMPTS_DIR / filename
        backup = path.with_suffix(path.suffix + ".bak")
        if backup.exists():
            path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            restored.append(filename)
    if not restored:
        raise HTTPException(status_code=404, detail="没有找到可恢复的提示词备份")
    return {"ok": True, "phase": phase, "restored": restored, "message": "已恢复上一版提示词"}


@router.get("/books/{book_id}/setup-phase/{phase}/trace")
def api_setup_phase_trace(book_id: int, phase: str) -> dict[str, Any]:
    """Return the recorded LLM trace JSON for a setup phase (system, real user, output, usage)."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    main_path = setup_file_read(work_dir, f"_setup_{phase}_trace.json")
    import json as _json_lib

    sub_traces: list[dict[str, Any]] = []
    if work_dir.exists():
        prefix = f"_setup_{phase}_"
        for p in setup_glob(work_dir, f"{prefix}*_trace.json"):
            if p.name == main_path.name:
                continue
            try:
                sub_traces.append({
                    "file": p.name,
                    "suffix": p.stem.replace(f"_setup_{phase}", "").replace("_trace", ""),
                    "data": _json_lib.loads(p.read_text(encoding="utf-8")),
                })
            except Exception:
                pass

    if not main_path.exists() and not sub_traces:
        return {"ok": True, "has_trace": False, "phase": phase}

    main_data: dict[str, Any] | None = None
    if main_path.exists():
        try:
            main_data = _json_lib.loads(main_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": True, "has_trace": False, "phase": phase, "error": f"trace 文件解析失败：{e}"}

    return {
        "ok": True,
        "has_trace": True,
        "phase": phase,
        "trace": main_data,
        "sub_traces": sub_traces,
    }


@router.get("/books/{book_id}/setup-pipeline")
def api_setup_pipeline(book_id: int) -> dict[str, Any]:
    """Return an overview of all 6 L0 phases: status + has_trace + inputs/outputs preview."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])
    import json as _json_lib

    phase_meta = [
        {"id": "premise", "label": "题材定位", "icon": "", "output": "设定/题材定位.md"},
        {"id": "world", "label": "世界观", "icon": "", "output": "设定/世界观/"},
        {"id": "characters", "label": "角色设计", "icon": "", "output": "设定/角色/"},
        {"id": "factions", "label": "势力", "icon": "", "output": "设定/势力/"},
        {"id": "relations", "label": "关系", "icon": "", "output": "设定/关系.md"},
        {"id": "outline", "label": "全书大纲", "icon": "", "output": "大纲/大纲.md"},
        {"id": "volume_outline", "label": "卷纲", "icon": "", "output": "大纲/卷纲_第N卷.md × N"},
        {"id": "chapter_outlines", "label": "章节细纲", "icon": "", "output": "大纲/细纲_第NNN章.md × N"},
    ]

    phases: list[dict[str, Any]] = []
    for meta in phase_meta:
        ph_id = meta["id"]
        status = "pending"
        detail = ""
        updated_at = ""
        pf = setup_file_read(work_dir, f"_setup_{ph_id}.json")
        if pf.exists():
            try:
                pdata = _json_lib.loads(pf.read_text(encoding="utf-8"))
                status = pdata.get("status", "pending")
                detail = (pdata.get("detail") or "")[:160]
                updated_at = pdata.get("updated_at", "")
            except Exception:
                pass
        trace_path = setup_file_read(work_dir, f"_setup_{ph_id}_trace.json")
        sub_trace_count = 0
        if work_dir.exists():
            sub_trace_count = sum(
                1 for _ in setup_glob(work_dir, f"_setup_{ph_id}_*_trace.json")
                if _.name != trace_path.name
            )
        from generator.long_novel.autopilot import l0_phase_done
        out_exists = l0_phase_done(work_dir, ph_id)
        if status == "pending":
            inferred = _inferred_setup_phase_status(work_dir, ph_id)
            if inferred is not None:
                status = str(inferred["status"])
                detail = str(inferred.get("detail") or "")
                updated_at = str(inferred.get("updated_at") or "")
        phases.append({
            **meta,
            "status": status,
            "detail": detail,
            "updated_at": updated_at,
            "has_trace": trace_path.exists(),
            "sub_trace_count": sub_trace_count,
            "output_exists": out_exists,
        })

    return {
        "ok": True,
        "book_id": book_id,
        "title": book.get("title", ""),
        "phases": phases,
    }


@router.get("/books/{book_id}/setup-files")
def api_setup_phase_files(book_id: int, phase: str) -> dict[str, Any]:
    """List all artifact files produced by a setup phase (for chip file-list UI).

    Returns ``{ok, phase, files: [{path, name, bytes, mtime, is_index}]}``.
    Single-file phases return one entry; multi-file phases (world/characters/factions)
    return one entry per .md file under the phase's output dir.
    """
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    work_dir = Path(book["work_dir"])

    # phase → (list_of_known_single_files, list_of_dirs_to_glob)
    layout = {
        "premise": (["设定/题材定位.md"], []),
        "world": (
            # legacy single files + new per-topic files
            ["设定/世界观/背景设定.md", "设定/世界观/力量体系.md", "设定/世界观/时代地理.md", "设定/世界观/历史大事件.md"],
            ["设定/世界观"],
        ),
        "characters": (["设定/角色/角色设定.md"], ["设定/角色"]),
        "factions": (["设定/势力/主要势力.md"], ["设定/势力"]),
        "relations": (["设定/关系.md"], []),
        "outline": (["大纲/大纲.md"], []),
        "volume_outline": ([], ["大纲"]),
        "chapter_outlines": ([], ["大纲"]),
    }
    if phase not in layout:
        raise HTTPException(status_code=400, detail=f"未知阶段：{phase}")

    explicit_files, dirs_to_glob = layout[phase]
    seen: set[str] = set()
    files: list[dict[str, Any]] = []

    def _add(rel_path: str) -> None:
        if rel_path in seen:
            return
        p = work_dir / rel_path
        if not p.exists() or not p.is_file():
            return
        seen.add(rel_path)
        try:
            st = p.stat()
            files.append({
                "path": rel_path,
                "name": p.name,
                "bytes": st.st_size,
                "mtime": st.st_mtime,
                "is_index": p.name.startswith("_"),
            })
        except Exception:
            pass

    for rel in explicit_files:
        _add(rel)
    for d in dirs_to_glob:
        dp = work_dir / d
        if dp.exists() and dp.is_dir():
            if phase == "volume_outline":
                try:
                    from generator.long_novel.l0_book_setup import ensure_volume_outlines_split
                    ensure_volume_outlines_split(work_dir)
                except Exception:
                    pass
                patterns = ["卷纲_*.md"]
            elif phase == "chapter_outlines":
                patterns = ["细纲_*.md", "续写规划_*.md"]
            else:
                patterns = ["*.md"]
            for pattern in patterns:
                for p in sorted(dp.glob(pattern)):
                    _add(str(p.relative_to(work_dir)).replace("\\", "/"))

    # Sort: index files first, then by name
    files.sort(key=lambda f: (not f["is_index"], f["name"]))

    return {"ok": True, "phase": phase, "files": files}


# ── Pipeline: Write Chapter (L2) ──────────────────────────────────────


@router.post("/books/{book_id}/write-chapter/{chapter_number}")
async def api_write_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    return await run_in_threadpool(_api_write_chapter_blocking, book_id, chapter_number)


def _api_write_chapter_blocking(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Run the full L2 chapter writing pipeline for a single chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import run_full_chapter

    client = _deepseek_client(book)
    work_dir = Path(book["work_dir"])

    _upsert_chapter_preserving(_db_path(), ch, status="writing")
    update_book(_db_path(), book_id, current_chapter=chapter_number)

    result = run_full_chapter(
        client, work_dir, chapter_number,
        chapter_title=ch.get("title", ""),
        target_words=ch.get("target_words", book["target_words_per_chapter"]),
    )

    # Auto-run 4-dimension review
    from generator.long_novel.l4_review import run_full_review
    chapter_content = Path(result["draft_path"]).read_text(encoding="utf-8") if result.get("draft_path") else ""
    outline_path = ch.get("outline_path")
    outline_text = Path(outline_path).read_text(encoding="utf-8") if outline_path and Path(outline_path).exists() else ""

    review = run_full_review(
        client, chapter_content, work_dir, chapter_number, outline_text,
    )

    import json as _json
    _upsert_chapter_preserving(
        _db_path(), ch,
        status="draft",
        draft_path=result["draft_path"],
        actual_words=result["final_words"],
        review_status=review["overall"],
        ai_review_json=_json.dumps(review, ensure_ascii=False),
    )

    result["review"] = review
    return {"ok": True, "message": f"第{chapter_number}章写作完成", "result": result}


# ── Pipeline: Step-by-step Chapter Writing ─────────────────────────────


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/start")
async def api_start_write_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    force = bool(payload.get("force"))
    valid_steps = {"draft", "expand", "polish", "review", "deslop", "continuity", "finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    progress_path = _step_progress_path(work_dir, chapter_number, chapter_title, step_name)
    current = _step_status_snapshot(book_id, work_dir, ch, chapter_number, step_name)
    if current.get("status") in {"starting", "running"}:
        return {
            "ok": True,
            "accepted": True,
            "already_running": True,
            "step": step_name,
            "status": current.get("status"),
            "detail": current.get("detail", ""),
            "updated_at": current.get("updated_at", ""),
            "run_count": current.get("run_count") or 0,
        }

    _write_step_progress(progress_path, "starting", "后台任务已启动", {"step": step_name})
    _step_job_mark(book_id, chapter_number, step_name, True)

    def _run() -> None:
        try:
            _write_step_progress(progress_path, "running", f"{step_name} 执行中…", {"step": step_name})
            result = _api_write_chapter_step_blocking(book_id, chapter_number, step_name, force)
            result_summary = {
                "word_count": int(result.get("word_count") or 0),
                "final_words": int(result.get("final_words") or 0),
                "skipped": bool(result.get("skipped")),
                "next_step": result.get("next_step") or "",
                "run_count": int(result.get("run_count") or 0),
                "batch_count": int(result.get("batch_count") or 0),
            }
            status = "skipped" if result.get("skipped") else "done"
            detail = str(result.get("message") or ("步骤已完成" if status == "done" else "步骤已跳过"))
            _write_step_progress(progress_path, status, detail, {"step": step_name, "result": result_summary})
        except HTTPException as exc:
            _write_step_progress(
                progress_path,
                "error",
                str(exc.detail)[:500],
                {"step": step_name, "http_status": exc.status_code},
            )
        except Exception as exc:
            logger.exception("chapter step failed book=%s chapter=%s step=%s", book_id, chapter_number, step_name)
            _write_step_progress(progress_path, "error", str(exc)[:500], {"step": step_name})
        finally:
            _step_job_mark(book_id, chapter_number, step_name, False)

    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True,
        "accepted": True,
        "step": step_name,
        "status": "starting",
        "detail": "后台任务已启动",
    }


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/status")
def api_write_chapter_step_progress(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    work_dir = Path(book["work_dir"])
    data = _step_status_snapshot(book_id, work_dir, ch, chapter_number, step_name)
    return {"ok": True, **data}


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}")
async def api_write_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    force = bool(payload.get("force"))
    return await run_in_threadpool(_api_write_chapter_step_blocking, book_id, chapter_number, step_name, force)


def _api_write_chapter_step_blocking(
    book_id: int,
    chapter_number: int,
    step_name: str,
    force: bool = False,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run a single step of the L2 chapter writing pipeline.

    Steps: draft | expand | polish | deslop | continuity | finalize

    Each step (except finalize) saves intermediate output to the work_dir
    so the next step can pick it up. The frontend can show each output before
    the user decides to continue.
    """
    valid_steps = {"draft", "expand", "polish", "review", "deslop", "continuity", "finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import (
        assemble_context,
        count_chinese_chars,
        ensure_chapter_heading,
        run_continuity_check,
        run_deslop,
        run_draft,
        run_expand,
        run_polish,
        strip_chapter_heading,
        update_tracking_files,
    )

    client = client or _deepseek_client(book)
    work_dir = Path(book["work_dir"])
    target_words = ch.get("target_words", book["target_words_per_chapter"])
    chapter_title = ch.get("title", "")
    expand_threshold = _expand_skip_threshold(target_words)
    batch_count = _chapter_batch_count(work_dir, chapter_number, str(chapter_title or ""))

    # Step: draft
    if step_name == "draft":
        if ch.get("status") not in ("outline_only", "writing", "draft", "published"):
            raise HTTPException(status_code=400, detail=f"章节状态 {ch.get('status')} 无法开始写作")
        _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "draft")
        _upsert_chapter_preserving(
            _db_path(),
            ch,
            status="writing",
            actual_words=0,
            draft_path=None,
            review_status=None,
            ai_review_json=None,
        )
        if not _has_later_saved_chapter(_db_path(), book_id, chapter_number):
            update_book(_db_path(), book_id, current_chapter=chapter_number)
        _archive_step_version(work_dir, chapter_number, chapter_title, "draft")
        draft = run_draft(client, work_dir, chapter_number, chapter_title, target_words)
        draft_words = count_chinese_chars(draft)
        draft_path = _step_file_path(work_dir, chapter_number, chapter_title, "draft")
        draft_path.write_text(draft, encoding="utf-8")
        ctx = assemble_context(work_dir, chapter_number, chapter_title, target_words)
        run_count = _step_run_count(work_dir, chapter_number, chapter_title, "draft")
        batch_count = _chapter_batch_count(work_dir, chapter_number, str(chapter_title or ""))
        return {
            "ok": True, "step": "draft", "word_count": draft_words,
            "content": draft, "target_words": target_words,
            "llm_context": _draft_context_manifest(ctx),
            "needs_expand": draft_words < expand_threshold,
            "next_step": "expand" if draft_words < expand_threshold else "polish",
            "run_count": run_count,
            "batch_count": batch_count,
        }

    # Step: expand
    if step_name == "expand":
        draft_path = _step_file_read(work_dir, chapter_number, "draft")
        if not draft_path or not draft_path.exists():
            raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
        draft = draft_path.read_text(encoding="utf-8")
        draft_words = count_chinese_chars(draft)
        _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "expand")
        if draft_words >= expand_threshold and not force:
            _archive_and_remove_step_artifact(work_dir, chapter_number, chapter_title, "expand")
            marker = _step_skip_path(work_dir, chapter_number, chapter_title, "expand")
            marker.write_text(
                json.dumps(
                    {
                        "step": "expand",
                        "skipped": True,
                        "reason": "draft_reached_target_words",
                        "word_count": draft_words,
                        "threshold": expand_threshold,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return {
                "ok": True,
                "step": "expand",
                "skipped": True,
                "word_count": draft_words,
                "content": draft,
                "target_words": target_words,
                "source_before": draft,
                "source_word_count": draft_words,
                "next_step": "polish",
                "message": f"初稿已达到 {draft_words} 字（目标 {expand_threshold} 字），自动跳过扩写。",
                "run_count": _step_history_count(work_dir, chapter_number, chapter_title, "expand"),
                "batch_count": batch_count,
            }
        if force:
            try:
                old_marker = _step_skip_read(work_dir, chapter_number, "expand")
                if old_marker:
                    old_marker.unlink(missing_ok=True)
            except Exception:
                logger.exception("remove_expand_skip_marker_failed book=%s chapter=%s", book_id, chapter_number)
        _archive_step_version(work_dir, chapter_number, chapter_title, "expand")
        expanded = run_expand(client, draft, target_words)
        expanded_words = count_chinese_chars(expanded)
        expand_path = _step_file_path(work_dir, chapter_number, chapter_title, "expand")
        expand_path.write_text(expanded, encoding="utf-8")
        run_count = _step_run_count(work_dir, chapter_number, chapter_title, "expand")
        return {
            "ok": True, "step": "expand", "word_count": expanded_words,
            "content": expanded, "target_words": target_words,
            "source_before": draft,
            "source_word_count": draft_words,
            "next_step": "polish",
            "run_count": run_count,
            "batch_count": batch_count,
        }

    # Step: polish
    if step_name == "polish":
        expand_path = _step_file_read(work_dir, chapter_number, "expand")
        draft_path = _step_file_read(work_dir, chapter_number, "draft")
        if expand_path and expand_path.exists():
            source = expand_path.read_text(encoding="utf-8")
        elif draft_path and draft_path.exists():
            source = draft_path.read_text(encoding="utf-8")
        else:
            raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
        _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "polish")
        polished = run_polish(client, source)
        polished_words = count_chinese_chars(polished)
        polish_path = _step_file_path(work_dir, chapter_number, chapter_title, "polish")
        _archive_step_version(work_dir, chapter_number, chapter_title, "polish")
        polish_path.write_text(polished, encoding="utf-8")
        run_count = _step_run_count(work_dir, chapter_number, chapter_title, "polish")
        return {
            "ok": True, "step": "polish", "word_count": polished_words,
            "content": polished, "next_step": "deslop",
            "source_before": source,
            "source_word_count": count_chinese_chars(source),
            "run_count": run_count,
            "batch_count": batch_count,
        }

    # Step: review
    if step_name == "review":
        source = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
        if not source:
            raise HTTPException(status_code=400, detail="请先运行去 AI 步骤")
        _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "review")
        from generator.long_novel.l4_review import run_story_review
        review = run_story_review(client, source, work_dir, chapter_number, _outline_for_chapter(ch))
        review_path = _step_file_path(work_dir, chapter_number, chapter_title, "review")
        _archive_step_version(work_dir, chapter_number, chapter_title, "review")
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        run_count = _step_run_count(work_dir, chapter_number, chapter_title, "review")
        return {
            "ok": True,
            "step": "review",
            "review": review,
            "next_step": "finalize",
            "run_count": run_count,
            "batch_count": batch_count,
        }

    # Step: deslop
    if step_name == "deslop":
        polish_path = _step_file_read(work_dir, chapter_number, "polish")
        expand_path = _step_file_read(work_dir, chapter_number, "expand")
        draft_path = _step_file_read(work_dir, chapter_number, "draft")
        if polish_path and polish_path.exists():
            source = polish_path.read_text(encoding="utf-8")
        elif expand_path and expand_path.exists():
            source = expand_path.read_text(encoding="utf-8")
        elif draft_path and draft_path.exists():
            source = draft_path.read_text(encoding="utf-8")
        else:
            raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
        _invalidate_outputs_after_step(_db_path(), book_id, book, ch, "deslop")
        final = strip_chapter_heading(run_deslop(client, source))
        final_words = count_chinese_chars(final)
        deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
        _archive_step_version(work_dir, chapter_number, chapter_title, "deslop")
        deslop_path.write_text(final, encoding="utf-8")
        deai = _score_deai_result(final)
        gate_path = _step_gate_path(work_dir, chapter_number, chapter_title, "deslop")
        gate_path.write_text(
            json.dumps({"deai": deai}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_count = _step_run_count(work_dir, chapter_number, chapter_title, "deslop")
        return {
            "ok": True, "step": "deslop", "word_count": final_words,
            "content": final, "deai": deai, "next_step": "review",
            "source_before": strip_chapter_heading(source),
            "source_word_count": count_chinese_chars(strip_chapter_heading(source)),
            "run_count": run_count,
            "batch_count": batch_count,
        }

    # Step: continuity
    if step_name == "continuity":
        deslop_path = _step_file_read(work_dir, chapter_number, "deslop")
        polish_path = _step_file_read(work_dir, chapter_number, "polish")
        if deslop_path and deslop_path.exists():
            source = deslop_path.read_text(encoding="utf-8")
        elif polish_path and polish_path.exists():
            source = polish_path.read_text(encoding="utf-8")
        else:
            raise HTTPException(status_code=400, detail="请先运行 draft 步骤")
        if chapter_number <= 1:
            return {
                "ok": True,
                "step": "continuity",
                "skipped": True,
                "reason": "第一章无需连续性检查",
                "next_step": "finalize",
                "run_count": 0,
                "batch_count": batch_count,
            }
        continuity = run_continuity_check(client, work_dir, chapter_number, source)
        return {
            "ok": True, "step": "continuity",
            "issues": continuity.get("issues", []),
            "issue_count": continuity.get("issue_count", 0),
            "passed": continuity.get("ok", False),
            "next_step": "finalize",
            "run_count": 1,
            "batch_count": batch_count,
        }

    # Step: finalize — save the post-deAI text, update tracking, and persist review.
    # All intermediate step files (初稿/扩写/润色/去AI/审查) are kept inside
    # the chapter folder per user request.
    if step_name == "finalize":
        final_text = ""
        for step in ("deslop", "polish", "expand", "draft"):
            sp = _step_file_read(work_dir, chapter_number, step)
            if sp and sp.exists():
                final_text = sp.read_text(encoding="utf-8")
                break
        if not final_text:
            raise HTTPException(status_code=400, detail="请先运行至少一个写作步骤")

        final_text = ensure_chapter_heading(final_text, chapter_number)
        final_words = count_chinese_chars(final_text)

        final_draft_path = chapter_final_path(work_dir, chapter_number, chapter_title)
        if final_draft_path.exists():
            backup = final_draft_path.with_suffix(".md.bak")
            if backup.exists():
                backup = final_draft_path.with_name(
                    f"{final_draft_path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md.bak"
                )
            shutil.copy2(final_draft_path, backup)
        final_draft_path.write_text(final_text, encoding="utf-8")

        has_later_draft = _has_later_saved_chapter(_db_path(), book_id, chapter_number)
        update_tracking_files(
            work_dir,
            chapter_number,
            final_text,
            client,
            advance_current=not has_later_draft,
        )

        review_existing = _step_file_read(work_dir, chapter_number, "review")
        if review_existing and review_existing.exists():
            review = json.loads(review_existing.read_text(encoding="utf-8"))
        else:
            from generator.long_novel.l4_review import run_story_review
            review = run_story_review(client, final_text, work_dir, chapter_number, _outline_for_chapter(ch))
            # Persist the review next to the chapter so it survives finalize.
            review_path = _step_file_path(work_dir, chapter_number, chapter_title, "review")
            review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

        upsert_chapter(
            _db_path(), book_id, int(ch.get("volume_number") or 1), chapter_number,
            title=str(ch.get("title") or ""),
            status="draft", draft_path=str(final_draft_path),
            target_words=int(ch.get("target_words") or book["target_words_per_chapter"]),
            actual_words=final_words,
            outline_path=ch.get("outline_path"),
            review_status=review.get("overall", "CONCERNS"),
            ai_review_json=json.dumps(review, ensure_ascii=False),
        )

        # Migrate any legacy `_step_*` files at work_dir root into the chapter
        # folder, then remove the legacy copies (one-time cleanup per chapter).
        for step, legacy_name in _LEGACY_STEP_FILES.items():
            legacy = work_dir / legacy_name
            if legacy.exists():
                target = _step_file_path(work_dir, chapter_number, chapter_title, step)
                if not target.exists():
                    try:
                        target.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
                    except Exception:
                        pass
                try:
                    legacy.unlink()
                except Exception:
                    pass

        return {
            "ok": True, "step": "finalize",
            "final_words": final_words,
            "draft_path": str(final_draft_path),
            "content": final_text,
            "review": review,
            "message": f"第{chapter_number}章已保存，共{final_words}字",
            "run_count": _finalize_run_count(final_draft_path),
            "batch_count": batch_count,
        }


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step-status")
def api_write_chapter_step_status(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Get current step status and available intermediate outputs."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import count_chinese_chars, strip_chapter_heading

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    batch_count = _chapter_batch_count(work_dir, chapter_number, chapter_title)
    steps_available = []
    for step_name in CHAPTER_STEP_FILES.keys():
        fp = _step_file_read(work_dir, chapter_number, step_name)
        if fp and fp.exists():
            text = fp.read_text(encoding="utf-8")
            steps_available.append({
                "step": step_name,
                "word_count": count_chinese_chars(text),
                "has_content": True,
                "run_count": _step_run_count(work_dir, chapter_number, str(ch.get("title") or ""), step_name),
                "batch_count": batch_count,
            })
        elif _step_skip_read(work_dir, chapter_number, step_name):
            steps_available.append({
                "step": step_name,
                "word_count": 0,
                "has_content": False,
                "skipped": True,
                "run_count": _step_history_count(work_dir, chapter_number, str(ch.get("title") or ""), step_name),
                "batch_count": batch_count,
            })
    if ch.get("draft_path"):
        final_path = Path(str(ch.get("draft_path")))
        steps_available.append({
            "step": "finalize",
            "word_count": int(ch.get("actual_words") or 0),
            "has_content": True,
            "run_count": _finalize_run_count(final_path),
            "batch_count": batch_count,
        })
    steps_progress = [
        _step_status_snapshot(book_id, work_dir, ch, chapter_number, step_name)
        for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]
    ]

    return {
        "ok": True,
        "chapter_status": ch.get("status"),
        "review_status": ch.get("review_status"),
        "batch_count": batch_count,
        "steps_available": steps_available,
        "steps_progress": steps_progress,
    }


# ── Pipeline: Review Only (L4) ────────────────────────────────────────


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}")
def api_write_chapter_step_output(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    """Read saved output for one chapter-writing step."""
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import count_chinese_chars, strip_chapter_heading

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    batch_count = _chapter_batch_count(work_dir, chapter_number, chapter_title)
    if step_name == "finalize":
        draft_path = Path(ch["draft_path"]) if ch.get("draft_path") else None
        content = draft_path.read_text(encoding="utf-8") if draft_path and draft_path.exists() else ""
        review = {}
        if ch.get("ai_review_json"):
            try:
                review = json.loads(ch["ai_review_json"])
            except Exception:
                review = {}
        if review:
            review = _normalize_review_gate(review, chapter_number)
        return {
            "ok": True,
            "step": "finalize",
            "content": content,
            "word_count": count_chinese_chars(content),
            "final_words": int(ch.get("actual_words") or count_chinese_chars(content)),
            "draft_path": str(draft_path) if draft_path else "",
            "review": review,
            "run_count": _finalize_run_count(draft_path),
            "batch_count": batch_count,
        }

    step_path = _step_file_read(work_dir, chapter_number, step_name)
    if not step_path or not step_path.exists():
        skip_marker = _step_skip_read(work_dir, chapter_number, step_name)
        if step_name == "expand" and skip_marker:
            marker_data = _read_json_file(skip_marker)
            draft_path = _step_file_read(work_dir, chapter_number, "draft")
            draft = draft_path.read_text(encoding="utf-8") if draft_path and draft_path.exists() else ""
            word_count = count_chinese_chars(draft)
            threshold = int(marker_data.get("threshold") or _EXPAND_AUTO_SKIP_WORDS)
            reason = str(marker_data.get("reason") or "")
            message = (
                f"初稿已达到 {word_count} 字（目标 {threshold} 字），自动跳过扩写。"
                if reason in {"draft_reached_3000_words", "draft_reached_target_words"}
                else "扩写已跳过。"
            )
            return {
                "ok": True,
                "step": "expand",
                "skipped": True,
                "content": draft,
                "source_before": draft,
                "source_word_count": word_count,
                "word_count": word_count,
                "target_words": int(ch.get("target_words") or book.get("target_words_per_chapter") or 0),
                "skip": marker_data,
                "message": message,
                "threshold": threshold,
                "run_count": _step_history_count(work_dir, chapter_number, chapter_title, "expand"),
                "batch_count": batch_count,
            }
        if step_name == "review" and ch.get("ai_review_json"):
            raw = str(ch["ai_review_json"])
            try:
                review = json.loads(raw)
            except Exception:
                review = {"overall": "CONCERNS", "dimensions": {}, "raw": raw}
            review = _normalize_review_gate(review, chapter_number)
            revised_content = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
            revised_content = strip_chapter_heading(revised_content) if revised_content else ""
            return {
                "ok": True,
                "step": "review",
                "review": review,
                "force_pass": {},
                "content": raw,
                "revised_content": revised_content,
                "revised_word_count": count_chinese_chars(revised_content),
                "word_count": count_chinese_chars(raw),
                "fallback_from_final": True,
                "batch_count": batch_count,
            }
        final_draft_path = Path(ch["draft_path"]) if ch.get("draft_path") else None
        if step_name in {"draft", "expand", "polish", "deslop"} and final_draft_path and final_draft_path.exists():
            content = final_draft_path.read_text(encoding="utf-8")
            return {
                "ok": True,
                "step": step_name,
                "content": content,
                "source_before": "",
                "word_count": count_chinese_chars(content),
                "target_words": int(ch.get("target_words") or book.get("target_words_per_chapter") or 0),
                "fallback_from_final": True,
                "message": "该步骤的中间产物不存在，已显示最终成稿。",
                "run_count": 0,
                "batch_count": batch_count,
            }
        raise HTTPException(status_code=404, detail="步骤产物不存在")

    raw = step_path.read_text(encoding="utf-8")
    if step_name == "deslop":
        cleaned_raw = strip_chapter_heading(raw)
        if cleaned_raw != raw:
            step_path.write_text(cleaned_raw, encoding="utf-8")
            raw = cleaned_raw
    if step_name == "review":
        try:
            review = json.loads(raw)
        except Exception:
            review = {"overall": "CONCERNS", "dimensions": {}, "raw": raw}
        review = _normalize_review_gate(review, chapter_number)
        force_pass = _read_json_file(_step_force_read(work_dir, chapter_number, "review"))
        revised_content = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
        revised_content = strip_chapter_heading(revised_content) if revised_content else ""
        return {
            "ok": True,
            "step": "review",
            "review": review,
            "force_pass": force_pass,
            "content": raw,
            "revised_content": revised_content,
            "revised_word_count": count_chinese_chars(revised_content),
            "word_count": count_chinese_chars(raw),
            "run_count": _step_run_count(work_dir, chapter_number, chapter_title, "review"),
            "batch_count": batch_count,
        }

    gate = _read_json_file(_step_gate_read(work_dir, chapter_number, step_name))
    force_pass = _read_json_file(_step_force_read(work_dir, chapter_number, step_name))
    if step_name == "deslop" and (
        not gate.get("deai")
        or gate.get("deai", {}).get("source") != "local_text_quality"
    ):
        gate["deai"] = _score_deai_result(raw)

    # 给前端做"原文/修改后"对比用：找当前步骤的上一步内容。
    source_before = ""
    if step_name in ("expand", "polish", "deslop"):
        chain = {"expand": ["draft"], "polish": ["expand", "draft"], "deslop": ["polish", "expand", "draft"]}
        for prev in chain.get(step_name, []):
            prev_path = _step_file_read(work_dir, chapter_number, prev)
            if prev_path and prev_path.exists():
                source_before = prev_path.read_text(encoding="utf-8")
                break
        if step_name == "deslop" and source_before:
            source_before = strip_chapter_heading(source_before)

    return {
        "ok": True,
        "step": step_name,
        "content": raw,
        "source_before": source_before,
        "source_word_count": count_chinese_chars(source_before),
        "word_count": count_chinese_chars(raw),
        "target_words": int(ch.get("target_words") or book.get("target_words_per_chapter") or 0),
        "deai": gate.get("deai") if step_name == "deslop" else None,
        "force_pass": force_pass,
        "run_count": _step_run_count(work_dir, chapter_number, chapter_title, step_name),
        "batch_count": batch_count,
    }


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/skip")
def api_skip_chapter_step(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    """Mark one writing step as skipped so the UI can continue to the next step."""
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    if step_name == "draft" and not _read_step_source(work_dir, chapter_number, ["draft"]) and not ch.get("draft_path"):
        raise HTTPException(status_code=400, detail="初稿是后续步骤的正文来源。请先写初稿，或已有正文后再跳过。")
    if step_name == "finalize":
        raise HTTPException(status_code=400, detail="成稿步骤不能跳过；需要保存正文时请运行成稿。")

    marker = _step_skip_path(work_dir, chapter_number, chapter_title, step_name)
    marker.write_text(
        json.dumps({
            "step": step_name,
            "skipped": True,
            "chapter_number": chapter_number,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "step": step_name,
        "skipped": True,
        "message": f"已跳过 {step_name}",
    }


@router.put("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/content")
async def api_save_step_content(
    book_id: int,
    chapter_number: int,
    step_name: str,
    content: str = Body(..., embed=True),
) -> dict[str, Any]:
    """Save edited content for a chapter writing step."""
    valid_steps = set(CHAPTER_STEP_FILES.keys()) | {"finalize"}
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {', '.join(sorted(valid_steps))}")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = ch.get("title", "")
    from generator.long_novel.l2_chapter_write import count_chinese_chars, ensure_chapter_heading, strip_chapter_heading

    if step_name == "finalize":
        final_text = ensure_chapter_heading(content, chapter_number)
        step_file = Path(ch["draft_path"]) if ch.get("draft_path") else chapter_final_path(work_dir, chapter_number, chapter_title)
        step_file.parent.mkdir(parents=True, exist_ok=True)
        if step_file.exists():
            backup = step_file.with_suffix(".md.bak")
            if backup.exists():
                backup = step_file.with_name(
                    f"{step_file.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md.bak"
                )
            shutil.copy2(step_file, backup)
        step_file.write_text(final_text, encoding="utf-8")
        final_words = count_chinese_chars(final_text)
        _upsert_chapter_preserving(
            _db_path(),
            ch,
            status=str(ch.get("status") or "draft"),
            draft_path=str(step_file),
            actual_words=final_words,
        )
        return {
            "ok": True,
            "message": "内容已保存",
            "step": step_name,
            "content": final_text,
            "word_count": final_words,
            "final_words": final_words,
            "draft_path": str(step_file),
        }

    if step_name == "deslop":
        content = strip_chapter_heading(content)
    step_file = _step_file_path(work_dir, chapter_number, chapter_title, step_name)
    step_file.parent.mkdir(parents=True, exist_ok=True)
    step_file.write_text(content, encoding="utf-8")

    return {"ok": True, "message": "内容已保存", "step": step_name}


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/force-pass")
async def api_force_pass_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    return await run_in_threadpool(
        _api_force_pass_chapter_step_blocking,
        book_id,
        chapter_number,
        step_name,
        payload,
    )


def _api_force_pass_chapter_step_blocking(
    book_id: int,
    chapter_number: int,
    step_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持强行通过")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    marker = _step_force_path(work_dir, chapter_number, chapter_title, step_name)
    data = {
        "step": step_name,
        "force_passed": True,
        "reason": str(payload.get("reason") or "人工强行通过"),
        "chapter_number": chapter_number,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    marker.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "step": step_name, "force_pass": data, "message": "已记录强行通过"}


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/revise")
async def api_revise_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    return await run_in_threadpool(
        _api_revise_chapter_step_blocking,
        book_id,
        chapter_number,
        step_name,
        payload,
    )


def _revise_progress_step(step_name: str) -> str:
    return f"{step_name}_revise"


@router.post("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/revise/start")
async def api_start_revise_chapter_step(
    book_id: int,
    chapter_number: int,
    step_name: str,
    request: Request,
) -> dict[str, Any]:
    payload = await _json_payload(request)
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持按建议修改")

    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    progress_step = _revise_progress_step(step_name)
    progress_path = _step_progress_path(work_dir, chapter_number, chapter_title, progress_step)
    current = _step_status_snapshot(book_id, work_dir, ch, chapter_number, progress_step)
    if current.get("status") in {"starting", "running"}:
        return {
            "ok": True,
            "accepted": True,
            "already_running": True,
            "step": step_name,
            "progress_step": progress_step,
            "status": current.get("status"),
            "detail": current.get("detail", ""),
            "updated_at": current.get("updated_at", ""),
            "run_count": current.get("run_count") or 0,
            "batch_count": current.get("batch_count") or 0,
        }

    _write_step_progress(progress_path, "starting", "按建议修改任务已启动", {"step": step_name, "progress_step": progress_step})
    _step_job_mark(book_id, chapter_number, progress_step, True)

    def _run() -> None:
        try:
            _write_step_progress(progress_path, "running", "按建议修改中，完成后会自动复审…", {"step": step_name, "progress_step": progress_step})
            result = _api_revise_chapter_step_blocking(book_id, chapter_number, step_name, payload)
            review = result.get("review") if isinstance(result.get("review"), dict) else {}
            result_summary = {
                "word_count": int(result.get("word_count") or 0),
                "revised_word_count": int(result.get("revised_word_count") or result.get("word_count") or 0),
                "review_passed": bool(review.get("passed")),
                "run_count": int(result.get("run_count") or 0),
                "batch_count": int(result.get("batch_count") or 0),
            }
            detail = str(result.get("message") or "已按建议修改，并已自动复审")
            _write_step_progress(progress_path, "done", detail, {"step": step_name, "progress_step": progress_step, "result": result_summary})
        except HTTPException as exc:
            _write_step_progress(
                progress_path,
                "error",
                str(exc.detail)[:500],
                {"step": step_name, "progress_step": progress_step, "http_status": exc.status_code},
            )
        except Exception as exc:
            logger.exception("chapter revise failed book=%s chapter=%s step=%s", book_id, chapter_number, step_name)
            _write_step_progress(progress_path, "error", str(exc)[:500], {"step": step_name, "progress_step": progress_step})
        finally:
            _step_job_mark(book_id, chapter_number, progress_step, False)

    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True,
        "accepted": True,
        "step": step_name,
        "progress_step": progress_step,
        "status": "starting",
        "detail": "按建议修改任务已启动",
    }


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/revise/status")
def api_revise_chapter_step_progress(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持按建议修改")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")
    work_dir = Path(book["work_dir"])
    progress_step = _revise_progress_step(step_name)
    data = _step_status_snapshot(book_id, work_dir, ch, chapter_number, progress_step)
    return {"ok": True, **data, "step": step_name, "progress_step": progress_step}


def _api_revise_chapter_step_blocking(
    book_id: int,
    chapter_number: int,
    step_name: str,
    payload: dict[str, Any],
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    if step_name not in {"review", "deslop"}:
        raise HTTPException(status_code=400, detail="只有审查和去 AI 步骤支持按建议修改")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    from generator.long_novel.l2_chapter_write import count_chinese_chars, run_deslop, strip_chapter_heading

    client = client or _deepseek_client(book)
    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    extra_prompt = str(payload.get("prompt") or "").strip()

    if step_name == "review":
        source = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
        if not source:
            raise HTTPException(status_code=400, detail="没有可修改的正文来源，请先运行去 AI")
        review = _read_json_file(_step_file_read(work_dir, chapter_number, "review"))
        review = _normalize_review_gate(review, chapter_number) if review else {}
        suggestions = _review_recommendation_text(review)
        previous_issue_count = _review_issue_count(review)
        system = _load_prompt_template(
            "l2_review_fix_system.txt",
            "你是长篇网文改稿编辑。你的任务是逐条落实审查问题，不是笼统润色。只输出修改后的完整正文，不要解释。",
        )
        user_template = _load_prompt_template("l2_review_fix_user.txt", "请根据审查建议修改第{chapter_number}章。\n{source}")
        user = _render_prompt_template(user_template, {
            "chapter_number": chapter_number,
            "outline": _outline_for_chapter(ch)[:2000],
            "suggestions": suggestions or "没有结构化建议，请整体提升连续性、逻辑、剧情推进、人设、环境与共情。",
            "extra_prompt": extra_prompt or "无",
            "source": source,
        })
        revised = _chat_text(client, system, user, thinking=True).strip()
        revised = strip_chapter_heading(run_deslop(client, revised))
        deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
        _archive_step_version(work_dir, chapter_number, chapter_title, "deslop")
        deslop_path.write_text(revised, encoding="utf-8")
        deai = _score_deai_result(revised)
        gate_path = _step_gate_path(work_dir, chapter_number, chapter_title, "deslop")
        gate_path.write_text(
            json.dumps({"deai": deai}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        from generator.long_novel.l4_review import run_story_review
        new_review = run_story_review(client, revised, work_dir, chapter_number, _outline_for_chapter(ch))
        new_review = _normalize_review_gate(new_review, chapter_number)
        new_review["revision_audit"] = {
            "mode": "review_fix_then_auto_recheck",
            "source_step": "deslop",
            "previous_overall": review.get("overall"),
            "previous_score": review.get("score"),
            "previous_issue_count": previous_issue_count,
            "new_overall": new_review.get("overall"),
            "new_score": new_review.get("score"),
            "remaining_issue_count": _review_issue_count(new_review),
            "remaining_summary": _review_recommendation_text(new_review)[:1800],
        }
        review_path = _step_file_path(work_dir, chapter_number, chapter_title, "review")
        _archive_step_version(work_dir, chapter_number, chapter_title, "review")
        review_path.write_text(json.dumps(new_review, ensure_ascii=False, indent=2), encoding="utf-8")
        force = _step_force_read(work_dir, chapter_number, "review")
        if force and force.exists():
            force.unlink()
        passed = bool(new_review.get("passed"))
        msg = "已按审查建议修改，并已自动复审"
        msg += "：审查已通过" if passed else "：仍有未解决项，请查看新的审查结果"
        return {
            "ok": True,
            "step": "review",
            "revised_step": "deslop",
            "content": revised,
            "word_count": count_chinese_chars(revised),
            "deai": deai,
            "review": new_review,
            "revised_content": revised,
            "revised_word_count": count_chinese_chars(revised),
            "source_before": source,
            "source_word_count": count_chinese_chars(source),
            "run_count": _step_run_count(work_dir, chapter_number, chapter_title, "review"),
            "batch_count": _chapter_batch_count(work_dir, chapter_number, chapter_title),
            "message": msg,
        }

    source = _read_step_source(work_dir, chapter_number, ["deslop", "polish", "expand", "draft"])
    if not source:
        raise HTTPException(status_code=400, detail="没有可去 AI 的正文来源")
    gate = _read_json_file(_step_gate_read(work_dir, chapter_number, "deslop"))
    deai = gate.get("deai") or {}
    suggestions = "\n".join([*(deai.get("findings") or []), *(deai.get("recommendations") or [])])
    system = _load_prompt_template(
        "l2_deslop_fix_system.txt",
        "你是中文网文资深去 AI 味编辑。只改文风，不改剧情、人设、关系、伏笔和章节推进。只输出修改后的完整正文，不要解释。",
    )
    user_template = _load_prompt_template("l2_deslop_fix_user.txt", "请继续降低第{chapter_number}章的 AI 味。\n{source}")
    user = _render_prompt_template(user_template, {
        "chapter_number": chapter_number,
        "suggestions": suggestions or "重点减少工整模板句、抽象情绪、泛泛转折和排比说明。",
        "extra_prompt": extra_prompt or "无",
        "source": source,
    })
    revised = _chat_text(client, system, user, thinking=True).strip()
    revised = strip_chapter_heading(run_deslop(client, revised))
    deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
    if deslop_path.exists():
        deslop_path.with_suffix(".md.bak").write_text(deslop_path.read_text(encoding="utf-8"), encoding="utf-8")
    deslop_path.write_text(revised, encoding="utf-8")
    deai = _score_deai_result(revised)
    gate_path = _step_gate_path(work_dir, chapter_number, chapter_title, "deslop")
    gate_path.write_text(json.dumps({"deai": deai}, ensure_ascii=False, indent=2), encoding="utf-8")
    force = _step_force_read(work_dir, chapter_number, "deslop")
    if force and force.exists():
        force.unlink()
    return {
        "ok": True,
        "step": "deslop",
        "content": revised,
        "word_count": count_chinese_chars(revised),
        "deai": deai,
        "message": "已按去 AI 建议修改完成，并重新完成本地质量门评估",
        "source_before": source,
    }


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/history")
def api_list_step_history(book_id: int, chapter_number: int, step_name: str) -> dict[str, Any]:
    """列出某步骤的历史版本（每次运行/重做生成一份归档）。"""
    valid_steps = set(CHAPTER_STEP_FILES.keys())
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step_name)
    if not history_dir.exists():
        return {"ok": True, "step": step_name, "versions": []}
    batch_started_at = 0.0
    if step_name != "draft":
        draft_path = _step_file_read(work_dir, chapter_number, "draft")
        if draft_path and draft_path.exists():
            try:
                batch_started_at = draft_path.stat().st_mtime
            except Exception:
                batch_started_at = 0.0
    versions: list[dict[str, Any]] = []
    for p in sorted(history_dir.iterdir(), key=lambda x: x.name, reverse=True):
        if not p.is_file():
            continue
        # Direct reruns are archived as YYYYMMDD_HHMMSS.md/json. Stale outputs
        # invalidated by rerunning an upstream step are stored separately and
        # should not appear as this step's current-batch history.
        if not re.match(r"^\d{8}_\d{6}\.(?:md|json)$", p.name):
            continue
        try:
            stat = p.stat()
            if batch_started_at and stat.st_mtime < batch_started_at:
                continue
            versions.append({
                "id": p.stem,
                "filename": p.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
        except Exception:
            continue
    return {"ok": True, "step": step_name, "versions": versions}


@router.get("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/history/{version_id}")
def api_read_step_history(book_id: int, chapter_number: int, step_name: str, version_id: str) -> dict[str, Any]:
    """读取某个历史版本的完整内容。"""
    valid_steps = set(CHAPTER_STEP_FILES.keys())
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    if "/" in version_id or "\\" in version_id or ".." in version_id:
        raise HTTPException(status_code=400, detail="Invalid version id")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step_name)
    matches = [p for p in history_dir.glob(f"{version_id}.*")] if history_dir.exists() else []
    if not matches:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    path = matches[0]
    content = path.read_text(encoding="utf-8")
    from generator.long_novel.l2_chapter_write import count_chinese_chars
    return {
        "ok": True,
        "step": step_name,
        "version_id": version_id,
        "content": content,
        "word_count": count_chinese_chars(content),
    }


@router.delete("/books/{book_id}/write-chapter/{chapter_number}/step/{step_name}/history/{version_id}")
def api_delete_step_history(book_id: int, chapter_number: int, step_name: str, version_id: str) -> dict[str, Any]:
    """Delete one archived history version file from disk."""
    valid_steps = set(CHAPTER_STEP_FILES.keys())
    if step_name not in valid_steps:
        raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}")
    if "/" in version_id or "\\" in version_id or ".." in version_id:
        raise HTTPException(status_code=400, detail="Invalid version id")
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    chapter_title = str(ch.get("title") or "")
    history_dir = _step_history_dir(work_dir, chapter_number, chapter_title, step_name)
    matches = [p for p in history_dir.glob(f"{version_id}.*")] if history_dir.exists() else []
    if not matches:
        raise HTTPException(status_code=404, detail="历史版本不存在")
    path = matches[0]
    if not _path_within(path, history_dir):
        raise HTTPException(status_code=400, detail="拒绝删除历史目录外文件")
    deleted_name = path.name
    path.unlink()
    return {
        "ok": True,
        "step": step_name,
        "version_id": version_id,
        "deleted": deleted_name,
        "message": "历史版本源文件已删除",
    }


@router.put("/books/{book_id}/chapters/{chapter_number}")
async def api_update_chapter(book_id: int, chapter_number: int, request: Request) -> dict[str, Any]:
    """更新章节信息，目前只支持改章节标题。"""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    payload = await _json_payload(request)
    new_title = str(payload.get("title") or "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="章节标题不能为空")
    if len(new_title) > 60:
        raise HTTPException(status_code=400, detail="章节标题过长（限 60 字符）")

    upsert_chapter(
        _db_path(),
        book_id,
        int(ch.get("volume_number") or 1),
        chapter_number,
        title=new_title,
        status=str(ch.get("status") or "outline_only"),
        target_words=int(ch.get("target_words") or 3000),
        actual_words=int(ch.get("actual_words") or 0),
        outline_path=ch.get("outline_path"),
        draft_path=ch.get("draft_path"),
        review_status=ch.get("review_status"),
        ai_review_json=ch.get("ai_review_json"),
    )
    updated = get_chapter(_db_path(), book_id, chapter_number) or {}
    return {"ok": True, "chapter": updated, "message": "章节标题已更新"}


@router.post("/books/{book_id}/chapters/{chapter_number}/generate-title")
async def api_generate_chapter_title(book_id: int, chapter_number: int) -> dict[str, Any]:
    return await run_in_threadpool(_generate_chapter_title_blocking, book_id, chapter_number)


def _generate_chapter_title_blocking(book_id: int, chapter_number: int) -> dict[str, Any]:
    """让 LLM 根据章节大纲/正文给出标题候选；返回但不直接落库。"""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch:
        raise HTTPException(status_code=404, detail="章节不存在")

    work_dir = Path(book["work_dir"])
    # 优先用本地正文/初稿，没有就退化成章节大纲。
    sample_text = ""
    for step in ("deslop", "polish", "expand", "draft"):
        path = _step_file_read(work_dir, chapter_number, step)
        if path and path.exists():
            sample_text = path.read_text(encoding="utf-8")[:1500]
            break
    if not sample_text and ch.get("draft_path"):
        p = Path(ch["draft_path"])
        if p.exists():
            sample_text = p.read_text(encoding="utf-8")[:1500]
    outline = _outline_for_chapter(ch)[:1200]
    if not sample_text and not outline:
        raise HTTPException(status_code=400, detail="本章还没有大纲或正文，无法生成标题")

    client = _deepseek_client(book)
    system = "你是中文网文资深编辑，根据章节内容拟一个 6 到 14 字、有钩子、不剧透太多的章节小标题。只输出标题文本，不要序号、不要书名号、不要解释。"
    user = f"""书名：{book.get("title", "")}
题材：{book.get("genre", "")}
第{chapter_number}章。

章节大纲：
{outline or "（无）"}

章节正文节选：
{sample_text or "（无）"}

请给出一个 6-14 字的章节小标题，只输出标题，不要任何前缀。"""
    title = _chat_text(client, system, user, thinking=False).strip()
    # 兜底清洗：去掉书名号/引号/编号前缀。
    for ch_strip in ("《", "》", "「", "」", "\"", "'", "“", "”"):
        title = title.replace(ch_strip, "")
    title = title.lstrip("0123456789.、 -·").strip()
    title = title.splitlines()[0].strip() if title else ""
    if not title:
        raise HTTPException(status_code=500, detail="LLM 没有返回有效标题，请重试")
    if len(title) > 30:
        title = title[:30]
    return {"ok": True, "title": title}


@router.post("/books/{book_id}/review/{chapter_number}")
async def api_review_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Run the 4-dimension review on an existing chapter."""
    book = get_book(_db_path(), book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    ch = get_chapter(_db_path(), book_id, chapter_number)
    if not ch or not ch.get("draft_path"):
        raise HTTPException(status_code=400, detail="章节尚未生成正文")

    from generator.long_novel.l4_review import run_full_review

    client = _deepseek_client(book)
    work_dir = Path(book["work_dir"])

    draft_path = Path(ch["draft_path"])
    chapter_content = draft_path.read_text(encoding="utf-8") if draft_path.exists() else ""
    outline_path = ch.get("outline_path")
    outline_text = Path(outline_path).read_text(encoding="utf-8") if outline_path and Path(outline_path).exists() else ""

    review = run_full_review(client, chapter_content, work_dir, chapter_number, outline_text)

    import json as _json
    _upsert_chapter_preserving(
        _db_path(), ch,
        review_status=review["overall"],
        ai_review_json=_json.dumps(review, ensure_ascii=False),
    )

    return {"ok": True, "review": review}


# ── Pipeline: Rewrite Chapter (L3) ────────────────────────────────────


@router.post("/books/{book_id}/reset-chapter/{chapter_number}")
async def api_reset_chapter_for_regeneration(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Delete active chapter outputs so the chapter can be generated from scratch."""
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    chapter = get_chapter(db_path, book_id, chapter_number)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    if _autopilot_job_active(book_id):
        raise HTTPException(status_code=409, detail="全自动任务正在运行，请暂停后再删除正文")
    for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]:
        if _step_job_active(book_id, chapter_number, step_name):
            raise HTTPException(status_code=409, detail=f"本章 {step_name} 步骤仍在运行，请等待完成后再删除正文")

    work_dir = Path(str(book["work_dir"] or ""))
    final_path = Path(str(chapter["draft_path"])) if chapter.get("draft_path") else None
    has_final = bool(final_path and final_path.exists())
    has_steps = any(_step_file_read(work_dir, chapter_number, step) for step in CHAPTER_STEP_FILES)
    if has_final or has_steps:
        reset = _archive_and_reset_chapter_outputs(db_path, book_id, book, chapter)
    else:
        _reset_chapter_row_for_deleted_outputs(db_path, book_id, book, chapter)
        reset = {
            "archived_files": [],
            "archive_dir": "",
            "later_written_chapters": [
                int(item["chapter_number"])
                for item in list_chapters(db_path, book_id)
                if int(item.get("chapter_number") or 0) > chapter_number
                and item.get("draft_path")
                and Path(str(item["draft_path"])).exists()
            ],
        }
    later = reset["later_written_chapters"]
    message = f"第{chapter_number}章正文已归档并删除"
    if later:
        message += f"；后续已有 {len(later)} 章正文，重写后请检查连续性"
    _sync_tracking_after_chapter_reset(db_path, book_id, book, [chapter_number])
    _write_reset_idle_autopilot_snapshot(book, message)
    return {
        "ok": True,
        "chapter": chapter_number,
        "message": message,
        **reset,
    }


@router.post("/books/{book_id}/reset-chapters")
async def api_reset_chapter_range_for_regeneration(book_id: int, request: Request) -> dict[str, Any]:
    """Archive and clear an inclusive chapter range for fresh generation."""
    payload = await _json_payload(request)
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    reset = _reset_chapter_range_for_regeneration(
        db_path,
        book_id,
        book,
        payload.get("chapter_start"),
        payload.get("chapter_end"),
    )
    count = len(reset["reset_chapters"])
    message = f"已归档并清空 {count} 章正文"
    _sync_tracking_after_chapter_reset(db_path, book_id, book, reset["reset_chapters"])
    _write_reset_idle_autopilot_snapshot(book, message)
    return {
        "ok": True,
        "message": message,
        **reset,
    }


def _cascade_continuity_issues(
    client: Any,
    work_dir: Path,
    chapters: list[dict[str, Any]],
    *,
    after_chapter: int,
) -> list[dict[str, Any]]:
    from generator.long_novel.l2_chapter_write import run_continuity_check

    cascade_issues: list[dict[str, Any]] = []
    for chapter in chapters:
        chapter_number = int(chapter["chapter_number"])
        if chapter_number <= int(after_chapter) or not chapter.get("draft_path"):
            continue
        draft_path = Path(str(chapter["draft_path"]))
        if not draft_path.exists():
            continue
        result = run_continuity_check(
            client,
            work_dir,
            chapter_number,
            draft_path.read_text(encoding="utf-8"),
        )
        if result.get("issue_count", 0) > 0:
            cascade_issues.append({"chapter": chapter_number, "issues": result["issues"]})
    return cascade_issues


def _rewrite_chapter_blocking(
    book_id: int,
    chapter_number: int,
    *,
    client: Any | None = None,
    check_cascade: bool = True,
) -> dict[str, Any]:
    """Rewrite one saved chapter from its prior text and optionally check later continuity."""
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    from generator.long_novel.l2_chapter_write import (
        count_chinese_chars,
        ensure_chapter_heading,
        rewrite_chapter_from_source,
        run_deslop,
        run_polish,
        update_tracking_files,
    )

    active_client = client or _deepseek_client(book)
    work_dir = Path(book["work_dir"])
    chapter = get_chapter(db_path, book_id, chapter_number)
    if not chapter or not chapter.get("draft_path"):
        raise HTTPException(status_code=400, detail=f"第{chapter_number}章尚未生成正文")
    old_path = Path(str(chapter["draft_path"]))
    if not old_path.exists():
        raise HTTPException(status_code=400, detail=f"第{chapter_number}章正文文件不存在")
    source_text = old_path.read_text(encoding="utf-8")

    # Keep the prior text available even if rewriting fails midway.
    backup = old_path.with_suffix(".md.bak")
    if backup.exists():
        backup = old_path.with_name(
            f"{old_path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md.bak"
    )
    shutil.copy2(old_path, backup)
    _invalidate_outputs_after_step(db_path, book_id, book, chapter, "draft")

    # Rewrite this chapter from its own source text. Loading global tracking
    # here would leak later chapters into rewrites of an earlier chapter.
    chapter_title = str(chapter.get("title") or "")
    draft = rewrite_chapter_from_source(
        active_client,
        source_text,
        chapter_number,
        chapter_title,
        _outline_for_chapter(chapter),
    )
    _archive_step_version(work_dir, chapter_number, chapter_title, "draft")
    step_draft_path = _step_file_path(work_dir, chapter_number, chapter_title, "draft")
    step_draft_path.write_text(draft, encoding="utf-8")

    polished = run_polish(active_client, draft)
    step_polish_path = _step_file_path(work_dir, chapter_number, chapter_title, "polish")
    step_polish_path.write_text(polished, encoding="utf-8")

    final = ensure_chapter_heading(run_deslop(active_client, polished), chapter_number)
    step_deslop_path = _step_file_path(work_dir, chapter_number, chapter_title, "deslop")
    step_deslop_path.write_text(final, encoding="utf-8")

    draft_path = chapter_final_path(work_dir, chapter_number, chapter_title)
    draft_path.write_text(final, encoding="utf-8")

    all_chapters = list_chapters(db_path, book_id)
    cascade_issues = (
        _cascade_continuity_issues(
            active_client,
            work_dir,
            all_chapters,
            after_chapter=chapter_number,
        )
        if check_cascade
        else []
    )
    has_later_draft = any(
        int(item.get("chapter_number") or 0) > chapter_number
        and item.get("draft_path")
        and Path(str(item["draft_path"])).exists()
        for item in all_chapters
    )
    update_tracking_files(
        work_dir,
        chapter_number,
        final,
        active_client,
        advance_current=not has_later_draft,
    )
    upsert_chapter(
        db_path,
        book_id,
        int(chapter.get("volume_number") or 1),
        chapter_number,
        title=str(chapter.get("title") or ""),
        status="draft",
        draft_path=str(draft_path),
        target_words=int(chapter.get("target_words") or book["target_words_per_chapter"]),
        actual_words=count_chinese_chars(final),
        outline_path=chapter.get("outline_path"),
        review_status=None,
        ai_review_json=None,
    )
    return {
        "ok": True,
        "chapter": chapter_number,
        "message": f"第{chapter_number}章已重写",
        "cascade_affected": len(cascade_issues),
        "cascade_issues": cascade_issues,
        "batch_count": _chapter_batch_count(work_dir, chapter_number, chapter_title),
    }


@router.post("/books/{book_id}/rewrite-chapter/{chapter_number}")
async def api_rewrite_chapter(book_id: int, chapter_number: int) -> dict[str, Any]:
    """Rewrite a chapter and check cascade continuity."""
    return await run_in_threadpool(_rewrite_chapter_blocking, book_id, chapter_number)


@router.post("/books/{book_id}/rewrite-chapters")
async def api_rewrite_chapter_range(book_id: int, request: Request) -> dict[str, Any]:
    """Rewrite an inclusive range in the background and expose progress in the autopilot panel."""
    payload = await _json_payload(request)
    db_path = _db_path()
    book = get_book(db_path, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    if _autopilot_job_active(book_id):
        raise HTTPException(status_code=409, detail="全自动任务正在运行，请暂停后再批量改写")
    chapters = _chapter_range(
        db_path,
        book_id,
        payload.get("chapter_start"),
        payload.get("chapter_end"),
    )
    for chapter in chapters:
        chapter_number = int(chapter["chapter_number"])
        if not chapter.get("draft_path") or not Path(str(chapter["draft_path"])).exists():
            raise HTTPException(status_code=400, detail=f"第{chapter_number}章还没有可用旧稿")
        for step_name in [*CHAPTER_STEP_FILES.keys(), "finalize"]:
            if _step_job_active(book_id, chapter_number, step_name):
                raise HTTPException(
                    status_code=409,
                    detail=f"第{chapter_number}章 {step_name} 步骤仍在运行，请等待完成后再批量改写",
                )

    work_dir = Path(str(book["work_dir"] or ""))
    work_dir.mkdir(parents=True, exist_ok=True)
    chapter_numbers = [int(chapter["chapter_number"]) for chapter in chapters]
    total = len(chapter_numbers)
    _set_cancel(book_id, False)

    def _snapshot(
        state: str,
        *,
        detail: str,
        results: list[dict[str, Any]],
        current: int = 0,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "operation": "batch_rewrite",
            "operation_label": "批量重写",
            "phase": "batch_rewrite",
            "stage": "writing",
            "detail": detail,
            "writing": {
                "total": total,
                "done": len(results),
                "current": current,
                "current_status": "rewriting" if state == "running" and current else "",
                "results": list(results),
            },
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }

    from generator.long_novel.autopilot import write_autopilot_file

    def _run() -> None:
        results: list[dict[str, Any]] = []
        try:
            active_client = _deepseek_client(book)
            for chapter_number in chapter_numbers:
                if _is_cancelled(book_id):
                    write_autopilot_file(
                        work_dir,
                        _snapshot("cancelled", detail="批量改写已暂停，已完成的章节会保留", results=results),
                    )
                    return
                write_autopilot_file(
                    work_dir,
                    _snapshot(
                        "running",
                        detail=f"正在重写第{chapter_number}章",
                        results=results,
                        current=chapter_number,
                    ),
                )
                _rewrite_chapter_blocking(
                    book_id,
                    chapter_number,
                    client=active_client,
                    check_cascade=False,
                )
                results.append({"chapter": chapter_number, "status": "rewritten"})

            cascade_issues = _cascade_continuity_issues(
                active_client,
                work_dir,
                list_chapters(db_path, book_id),
                after_chapter=chapter_numbers[0] - 1,
            )
            detail = f"批量改写完成：共 {total} 章"
            if cascade_issues:
                detail += f"，连续性检查发现 {len(cascade_issues)} 章需要留意"
            write_autopilot_file(
                work_dir,
                {
                    **_snapshot("done", detail=detail, results=results),
                    "cascade_affected": len(cascade_issues),
                    "cascade_issues": cascade_issues,
                },
            )
        except Exception as exc:
            write_autopilot_file(
                work_dir,
                _snapshot("error", detail=str(exc)[:300], results=results),
            )
            logger.exception("batch rewrite failed for book %s", book_id)
        finally:
            _autopilot_job_mark(book_id, False)

    write_autopilot_file(
        work_dir,
        _snapshot("running", detail=f"准备批量改写第{chapter_numbers[0]}-{chapter_numbers[-1]}章", results=[]),
    )
    _autopilot_job_mark(book_id, True)
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        _autopilot_job_mark(book_id, False)
        raise
    return {
        "ok": True,
        "accepted": True,
        "message": f"已开始批量改写第{chapter_numbers[0]}-{chapter_numbers[-1]}章",
        "chapter_start": chapter_numbers[0],
        "chapter_end": chapter_numbers[-1],
        "chapter_count": total,
    }


__all__ = ["router"]
