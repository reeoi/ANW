"""开书设定（L0）相关路由：分阶段启动/轮询、追加章节、trace/pipeline/文件清单。

由 ``generator.long_novel.api`` 聚合进主 router。LLM 客户端经
``deps._deepseek_client(...)`` 模块属性获取（统一 monkeypatch 缝）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from generator.long_novel import deps
from generator.long_novel.db import (
    get_book,
    get_chapter,
    list_chapters,
    list_volumes,
    update_book,
    upsert_chapter,
    upsert_volume,
)
from generator.long_novel.deps import _db_path, _json_payload
from generator.long_novel.jobs import _is_cancelled, _set_cancel
from generator.long_novel.l0_book_setup import (
    setup_dir,
    setup_file_read,
    setup_glob,
)
from generator.long_novel.step_artifacts import _max_outline_chapter, _outline_title

logger = logging.getLogger(__name__)

router = APIRouter()


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
        client = deps._deepseek_client(book)

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

            client = deps._deepseek_client(book)
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

    import threading

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
