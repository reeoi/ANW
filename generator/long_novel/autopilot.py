"""Long-novel autopilot orchestrator.

Runs the open-book pipeline (设定 → 大纲 → 入库 →〔Phase 2: 正文〕) as an ordered
list of stages. The orchestrator itself is deliberately DB-free and side-effect
agnostic:

- ``run_stages`` walks an ordered list of :class:`AutopilotStage`, skipping
  stages already done, honouring a ``is_cancelled`` callback, and stopping on
  the first error. It reports progress through an injected ``write_progress``
  callback so the runtime (API background thread) decides where snapshots go.
- ``build_l0_stages`` wires the eight file-based L0 setup/outline phases
  (题材定位 → 章节细纲) using the existing ``run_l0_*`` functions and per-phase
  artifact checks for idempotency.

The DB-coupled ``finalize`` stage (building the chapter queue) is appended by
the API layer, keeping this module free of database imports and easy to test.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from generator.long_novel.l0_book_setup import (
    run_l0_book_outline,
    run_l0_chapter_outlines,
    run_l0_characters,
    run_l0_factions,
    run_l0_premise,
    run_l0_relations,
    run_l0_volume_outline,
    run_l0_world,
    setup_dir,
    setup_file_read,
)

logger = logging.getLogger(__name__)

AUTOPILOT_FILE = "_autopilot.json"


@dataclass(frozen=True)
class AutopilotStage:
    """One step of the autopilot chain.

    ``run`` performs the work (raises on failure); ``is_done`` reports whether
    the stage's output already exists so a re-run can skip it.
    """

    phase: str
    label: str
    run: Callable[[], Any]
    is_done: Callable[[], bool]


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _progress(
    state: str,
    *,
    stage: str = "",
    label: str = "",
    index: int = 0,
    total: int = 0,
    stage_status: str = "",
    detail: str = "",
    started_at: str = "",
    completed: list[str] | None = None,
    failed_at: str | None = None,
) -> dict[str, Any]:
    """Build a full progress snapshot dict (one write == one snapshot)."""
    return {
        "state": state,  # running | done | error | cancelled
        "stage": stage,
        "label": label,
        "index": index,
        "total": total,
        "stage_status": stage_status,  # running | done | skipped | error
        "detail": detail,
        "completed": list(completed or []),
        "failed_at": failed_at,
        "started_at": started_at,
        "updated_at": _now_hms(),
    }


def run_stages(
    stages: list[AutopilotStage],
    *,
    write_progress: Callable[[dict[str, Any]], None],
    is_cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Run ``stages`` in order, returning the final progress snapshot.

    Skips stages whose ``is_done`` is already true, checks ``is_cancelled``
    before each stage, and stops on the first stage that raises (recording it
    in ``failed_at``). Every transition is reported via ``write_progress``.
    """
    total = len(stages)
    started_at = _now_hms()
    completed: list[str] = []

    for index, stage in enumerate(stages):
        if is_cancelled():
            snapshot = _progress(
                "cancelled",
                stage=stage.phase,
                label=stage.label,
                index=index,
                total=total,
                detail="已取消",
                started_at=started_at,
                completed=completed,
            )
            write_progress(snapshot)
            return snapshot

        if stage.is_done():
            completed.append(stage.phase)
            write_progress(
                _progress(
                    "running",
                    stage=stage.phase,
                    label=stage.label,
                    index=index,
                    total=total,
                    stage_status="skipped",
                    detail=f"已存在，跳过：{stage.label}",
                    started_at=started_at,
                    completed=completed,
                )
            )
            continue

        write_progress(
            _progress(
                "running",
                stage=stage.phase,
                label=stage.label,
                index=index,
                total=total,
                stage_status="running",
                detail=f"正在生成：{stage.label}",
                started_at=started_at,
                completed=completed,
            )
        )
        try:
            stage.run()
        except Exception as exc:
            logger.exception("autopilot stage %s failed", stage.phase)
            snapshot = _progress(
                "error",
                stage=stage.phase,
                label=stage.label,
                index=index,
                total=total,
                stage_status="error",
                detail=str(exc)[:300],
                started_at=started_at,
                completed=completed,
                failed_at=stage.phase,
            )
            write_progress(snapshot)
            return snapshot

        completed.append(stage.phase)
        write_progress(
            _progress(
                "running",
                stage=stage.phase,
                label=stage.label,
                index=index,
                total=total,
                stage_status="done",
                detail=f"完成：{stage.label}",
                started_at=started_at,
                completed=completed,
            )
        )

    snapshot = _progress(
        "done",
        stage="",
        label="",
        index=total,
        total=total,
        detail="全部完成",
        started_at=started_at,
        completed=completed,
    )
    write_progress(snapshot)
    return snapshot


# ── Chapter-writing loop (Phase 2: 正文 autopilot) ─────────────────────


def _writing_progress(
    state: str,
    *,
    total: int,
    results: list[dict[str, Any]],
    setup_completed: list[str] | None = None,
    started_at: str = "",
    current: int = 0,
    current_status: str = "",
    current_revisions: int = 0,
    current_detail: dict[str, Any] | None = None,
    detail: str = "",
    failed_at: int | None = None,
) -> dict[str, Any]:
    """Build a chapter-writing progress snapshot.

    Shares the top-level ``state`` contract with the setup snapshots so the same
    ``/autopilot/status`` endpoint and monitor panel render both. ``completed``
    carries the finished setup phases so the 9 setup chips stay ticked while the
    chapter list fills in below them. Chapter detail lives under ``writing``.
    """
    return {
        "state": state,  # running | done | error | cancelled
        "phase": "writing",
        "stage": "writing",
        "label": "正文写作",
        "completed": list(setup_completed or []),
        "detail": detail,
        "failed_at": failed_at,
        "started_at": started_at,
        "updated_at": _now_hms(),
        "writing": {
            "total": total,
            "done": len(results),
            "current": current,
            "current_status": current_status,  # writing | reviewing | revising | passed | needs_human | error
            "current_revisions": current_revisions,
            "current_detail": dict(current_detail or {}),
            "needs_human": [r.get("chapter") for r in results if r.get("status") == "needs_human"],
            "results": list(results),
        },
    }


def run_chapter_loop(
    chapter_numbers: list[int],
    *,
    write_chapter: Callable[[int, Callable[..., None]], dict[str, Any]],
    write_progress: Callable[[dict[str, Any]], None],
    is_cancelled: Callable[[], bool] = lambda: False,
    setup_completed: list[str] | None = None,
) -> dict[str, Any]:
    """Write each chapter in order, reporting to one progress stream.

    ``write_chapter(chapter_number, report)`` does the heavy lifting
    (run_full_chapter → review → revise) and returns a per-chapter result dict
    that must contain at least ``chapter`` and ``status`` ("passed" |
    "needs_human"). It may call the injected ``report(status, detail="",
    revisions=0)`` to surface live sub-step progress for the current chapter.

    A chapter that fails its review gate (``needs_human``) does NOT stop the
    loop — the autopilot flags it and continues to the next chapter. Only an
    explicit cancel or a raised exception (hard error) stops the run. The
    orchestrator is deliberately DB-free; the API layer injects ``write_chapter``.
    """
    total = len(chapter_numbers)
    started_at = _now_hms()
    results: list[dict[str, Any]] = []

    for ch_num in chapter_numbers:
        if is_cancelled():
            snapshot = _writing_progress(
                "cancelled",
                total=total,
                results=results,
                setup_completed=setup_completed,
                started_at=started_at,
                current=ch_num,
                detail="已取消",
            )
            write_progress(snapshot)
            return snapshot

        def report(
            status: str,
            detail: str = "",
            revisions: int = 0,
            _ch: int = ch_num,
            **extra: Any,
        ) -> None:
            write_progress(
                _writing_progress(
                    "running",
                    total=total,
                    results=results,
                    setup_completed=setup_completed,
                    started_at=started_at,
                    current=_ch,
                    current_status=status,
                    current_revisions=revisions,
                    current_detail=extra,
                    detail=detail or f"第{_ch}章 {status}",
                )
            )

        report("writing", f"第{ch_num}章 写作中…")
        try:
            result = write_chapter(ch_num, report)
        except Exception as exc:
            logger.exception("autopilot chapter %s failed", ch_num)
            snapshot = _writing_progress(
                "error",
                total=total,
                results=results,
                setup_completed=setup_completed,
                started_at=started_at,
                current=ch_num,
                current_status="error",
                detail=str(exc)[:300],
                failed_at=ch_num,
            )
            write_progress(snapshot)
            return snapshot

        results.append(result)
        status = str(result.get("status") or "passed")
        reason = str(result.get("reason") or "").strip()
        if status == "passed":
            detail = f"第{ch_num}章已通过"
        elif reason:
            detail = f"第{ch_num}章重写{int(result.get('revisions') or 0)}次仍未通过，需人工：{reason}"
        else:
            detail = f"第{ch_num}章需人工复核"
        write_progress(
            _writing_progress(
                "running",
                total=total,
                results=results,
                setup_completed=setup_completed,
                started_at=started_at,
                current=ch_num,
                current_status=status,
                current_revisions=int(result.get("revisions") or 0),
                current_detail={"reason": reason} if reason else None,
                detail=detail,
            )
        )

    human = [r.get("chapter") for r in results if r.get("status") == "needs_human"]
    summary = f"正文写作完成：共 {total} 章"
    if human:
        summary += f"，其中 {len(human)} 章未过审需人工复核"
    snapshot = _writing_progress(
        "done",
        total=total,
        results=results,
        setup_completed=setup_completed,
        started_at=started_at,
        detail=summary,
    )
    write_progress(snapshot)
    return snapshot


# ── L0 file-based stage building + idempotency ────────────────────────


def l0_phase_done(work_dir: Path | str, phase: str) -> bool:
    """Return True when ``phase``'s output artifacts already exist on disk."""
    wd = Path(work_dir)
    if phase == "premise":
        return (wd / "设定" / "题材定位.md").exists()
    if phase == "world":
        d = wd / "设定" / "世界观"
        return d.is_dir() and any(d.glob("*.md"))
    if phase == "characters":
        d = wd / "设定" / "角色"
        return d.is_dir() and any(p for p in d.glob("*.md") if not p.name.startswith("_"))
    if phase == "factions":
        d = wd / "设定" / "势力"
        return d.is_dir() and any(p for p in d.glob("*.md") if not p.name.startswith("_"))
    if phase == "relations":
        return (wd / "设定" / "关系.md").exists()
    if phase == "outline":
        return (wd / "大纲" / "大纲.md").exists()
    if phase == "volume_outline":
        d = wd / "大纲"
        return d.is_dir() and any(d.glob("卷纲_*.md"))
    if phase == "chapter_outlines":
        return (wd / "大纲" / "细纲_第001章.md").exists()
    return False


def build_l0_stages(
    client: Any,
    work_dir: Path | str,
    *,
    title: str,
    genre: str,
    premise: str,
    target_chapters: int,
    words_per_chapter: int,
    additional_prompt: str = "",
) -> list[AutopilotStage]:
    """Build the eight file-based L0 stages (题材定位 → 章节细纲), in order."""
    wd = Path(work_dir)

    def stage(phase: str, label: str, fn: Callable[[], Any]) -> AutopilotStage:
        return AutopilotStage(phase=phase, label=label, run=fn, is_done=lambda p=phase: l0_phase_done(wd, p))

    return [
        stage("premise", "题材定位", lambda: run_l0_premise(client, wd, title, genre, premise, None, additional_prompt)),
        stage("world", "世界观", lambda: run_l0_world(client, wd, title, genre, additional_prompt)),
        stage("characters", "角色设计", lambda: run_l0_characters(client, wd, title, genre, additional_prompt)),
        stage("factions", "势力", lambda: run_l0_factions(client, wd, title, genre, additional_prompt)),
        stage("relations", "关系", lambda: run_l0_relations(client, wd, title, genre, additional_prompt)),
        stage("outline", "全书大纲", lambda: run_l0_book_outline(client, wd, title, genre, target_chapters, words_per_chapter, additional_prompt)),
        stage(
            "volume_outline", "卷纲", lambda: run_l0_volume_outline(client, wd, title, genre, target_chapters, words_per_chapter, additional_prompt)
        ),
        stage(
            "chapter_outlines",
            "章节细纲",
            lambda: run_l0_chapter_outlines(client, wd, title, genre, target_chapters, words_per_chapter, additional_prompt),
        ),
    ]


# ── progress file IO (used by the API background thread) ──────────────


def write_autopilot_file(work_dir: Path | str, snapshot: dict[str, Any]) -> None:
    """Persist a progress snapshot to ``<work_dir>/.setup/_autopilot.json``."""
    path = setup_dir(Path(work_dir)) / AUTOPILOT_FILE
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def read_autopilot_file(work_dir: Path | str) -> dict[str, Any] | None:
    """Read the autopilot progress snapshot, or None if it doesn't exist yet."""
    path = setup_file_read(Path(work_dir), AUTOPILOT_FILE)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("failed to read autopilot progress file: %s", path)
        return None


__all__ = [
    "AutopilotStage",
    "run_stages",
    "run_chapter_loop",
    "build_l0_stages",
    "l0_phase_done",
    "write_autopilot_file",
    "read_autopilot_file",
    "AUTOPILOT_FILE",
]
