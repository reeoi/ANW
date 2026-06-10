"""章节产物的失效与重置级联：重跑某步后归档/清除后续产物、
按章/按范围删除正文并回滚追踪文件与 DB 行。"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from generator.long_novel.db import list_chapters, update_book, upsert_chapter
from generator.long_novel.deps import _upsert_chapter_preserving
from generator.long_novel.jobs import _autopilot_job_active, _step_job_active
from generator.long_novel.l2_chapter_write import (
    CHAPTER_FINAL_FILENAME,
    CHAPTER_STEP_FILES,
    chapter_dir,
    chapter_final_path,
)
from generator.long_novel.step_artifacts import (
    _LEGACY_STEP_FILES,
    _step_file_path,
    _step_file_read,
    _step_force_read,
    _step_gate_read,
    _step_history_dir,
    _step_progress_read,
    _step_skip_read,
)

logger = logging.getLogger(__name__)


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
