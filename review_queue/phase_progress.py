"""Phase F (decision #27 / U2) helpers.

Pure functions consumed by ``review_queue/human_review.py`` to expose:

- Phase-by-phase progress for a story (phase_0 → phase_5_done with the
  per-section / rewrite / failed labels the orchestrator may set).
- Work_dir file browser: list files inside ``data/works/{story_id}/`` and
  read a single text file safely (path-traversal protection + size cap).
- A normalized ``resume_from`` validator so the dashboard's resume button
  can never pass an unsupported phase identifier to ``run_pipeline``.

These helpers are stateless and can be unit-tested without spinning up
FastAPI; the API surface in ``human_review.py`` is a thin wrapper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PHASES: tuple[str, ...] = (
    "phase_0",
    "phase_1",
    "phase_2",
    "phase_3",
    "phase_4",
    "phase_5",
    "phase_5_5",
    "phase_6",
    "phase_7",
)

PHASE_LABELS: dict[str, str] = {
    "phase_0": "phase_0 选题",
    "phase_1": "phase_1 框架/简介",
    "phase_2": "phase_2 大纲",
    "phase_3": "phase_3 逐节",
    "phase_4": "phase_4 精修",
    "phase_5": "phase_5 去 AI 味",
    "phase_5_5": "phase_5_5 朱雀检测",
    "phase_6": "phase_6 审核",
    "phase_7": "phase_7 发布",
}

# Per-phase artifact filenames produced by generator/c_pipeline/*. The
# dashboard uses this to surface "查看产物" links once a phase completes.
# phase_6 / phase_7 don't produce file artifacts (review report lives in
# stories.review_detail JSON; publish has no local artifact).
PHASE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "phase_0": ("0_选题.json",),
    "phase_1": ("1_设定.md",),
    "phase_2": ("2_小节大纲.md",),
    "phase_3": ("3_正文_合稿.md",),
    "phase_4": ("4_精修稿.md",),
    "phase_5": ("5_最终稿.md",),
    "phase_5_5": ("5_5_朱雀通过稿.md",),
    "phase_6": (),
    "phase_7": (),
}

# Default safety limits for the file browser.
MAX_READABLE_FILE_BYTES = 1_048_576  # 1 MiB
TEXT_SUFFIXES: tuple[str, ...] = (
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".html",
    ".csv",
)

_PHASE_RUNNING_RE = re.compile(r"^phase_(\d)_running$")
_PHASE_DONE_RE = re.compile(r"^phase_(\d)_done$")
_PHASE_REWRITE_RE = re.compile(r"^phase_(\d)_rewrite$")
_PHASE_FAILED_RE = re.compile(r"^failed_at_phase_(\d)(?:_.*)?$")
_PHASE_SECTION_RE = re.compile(r"^phase_3_section_(\d{1,3})(?:_done)?$")
# phase_5_5 朱雀检测闭环专用
_PHASE_5_5_RUNNING_RE = re.compile(r"^phase_5_5_running$")
_PHASE_5_5_DONE_RE = re.compile(r"^phase_5_5_done$")
_PHASE_5_5_SKIPPED_RE = re.compile(r"^phase_5_5_skipped$")
_PHASE_5_5_PAUSED_RE = re.compile(r"^phase_5_5_paused$")
_PHASE_5_5_REJECTED_RE = re.compile(r"^phase_5_5_rejected$")
# phase_6 审核 / phase_7 发布 状态机扩展
_PHASE_NEEDS_HUMAN_RE = re.compile(r"^phase_6_needs_human$")
_PHASE_REJECTED_RE = re.compile(r"^phase_6_rejected$")
_PHASE_PUBLISH_FAILED_RE = re.compile(r"^phase_7_failed$")
_PHASE_PUBLISH_PAUSED_RE = re.compile(r"^phase_7_paused$")
_OUTLINE_SECTION_COUNT_RE = re.compile(r"^-\s*section_count\s*:\s*(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PhaseStep:
    """One row in the phase progress strip."""

    phase: str
    label: str
    status: str  # "done" / "in_progress" / "rewrite" / "failed" / "pending"


@dataclass(frozen=True)
class PhaseProgress:
    """Aggregated progress info for a single story."""

    current_phase: str
    percent: float
    label: str
    state: str  # "running" / "done" / "rewrite" / "failed" / "pending"
    steps: list[PhaseStep] = field(default_factory=list)
    failed_at: str | None = None
    section_index: int | None = None


@dataclass(frozen=True)
class WorkDirFile:
    """One file row in the work_dir browser."""

    name: str
    relative_path: str
    size_bytes: int
    modified_at: str
    is_text: bool


def compute_phase_progress(current_phase: str | None) -> PhaseProgress:
    """Map a ``stories.current_phase`` string into a UI progress payload.

    Recognized inputs (mirroring orchestrator + R2 rewrite):

    - ``phase_N`` / ``phase_N_running``         — phase N is in flight.
    - ``phase_N_done``                          — phase N just completed.
    - ``phase_3_section_NN`` /
      ``phase_3_section_NN_done``               — Phase 3 mid-flight.
    - ``phase_N_rewrite``                       — Phase 4-5 R2 rerun.
    - ``failed_at_phase_N`` /
      ``failed_at_phase_N_running``             — pipeline halted at N.
    - anything else falls back to the ``phase_0`` baseline.
    """

    raw = (current_phase or "").strip() or "phase_0"
    state: str = "pending"
    failed_at: str | None = None
    section_index: int | None = None
    label = raw

    def _phase_idx(digit: int) -> int:
        """Map a regex-captured digit to the actual PHASES index.

        phase_5_5 was inserted at index 6, pushing phase_6→7, phase_7→8.
        Digits 0-5 map directly; digits 6+ need +1 offset.
        """
        if digit <= 5:
            return digit
        return digit + 1  # skip over phase_5_5 at index 6

    completed_idx = -1  # last *fully* completed phase
    running_idx = 0  # phase currently in flight

    if raw in PHASES:
        # Plain "phase_N" — interpret as just-started.
        running_idx = PHASES.index(raw)
        state = "running"
        label = f"{raw} 进行中"
    elif (m := _PHASE_DONE_RE.match(raw)):
        n = int(m.group(1))
        idx = _phase_idx(n)
        completed_idx = idx
        if idx >= len(PHASES) - 1:
            state = "done"
            running_idx = -1  # all phases finished, nothing in flight
            label = "全部完成"
        else:
            running_idx = idx + 1
            next_phase = PHASES[running_idx] if running_idx < len(PHASES) else f"phase_{n + 1}"
            state = "running"
            label = f"phase_{n} 完成,等待 {next_phase}"
    elif (m := _PHASE_RUNNING_RE.match(raw)):
        n = int(m.group(1))
        idx = _phase_idx(n)
        completed_idx = idx - 1
        running_idx = idx
        state = "running"
        label = f"phase_{n} 进行中"
    elif (m := _PHASE_REWRITE_RE.match(raw)):
        n = int(m.group(1))
        idx = _phase_idx(n)
        completed_idx = idx - 1
        running_idx = idx
        state = "rewrite"
        label = f"phase_{n} R2 重写中"
    elif (m := _PHASE_SECTION_RE.match(raw)):
        section_index = int(m.group(1))
        completed_idx = 2  # phase_2 done
        running_idx = 3
        state = "running"
        suffix = "完成" if raw.endswith("_done") else "生成中"
        label = f"phase_3 第 {section_index:02d} 节 {suffix}"
    elif (m := _PHASE_FAILED_RE.match(raw)):
        n = int(m.group(1))
        idx = _phase_idx(n)
        completed_idx = idx - 1
        running_idx = idx
        state = "failed"
        failed_at = f"phase_{n}"
        label = f"phase_{n} 失败"
    elif _PHASE_5_5_RUNNING_RE.match(raw):
        completed_idx = 5  # phase_5 done
        running_idx = 6
        state = "running"
        label = "phase_5_5 朱雀检测中"
    elif _PHASE_5_5_DONE_RE.match(raw):
        completed_idx = 6  # phase_5_5 done
        running_idx = 7  # phase_6
        state = "running"
        label = "phase_5_5 朱雀通过"
    elif _PHASE_5_5_SKIPPED_RE.match(raw):
        completed_idx = 6
        running_idx = 7
        state = "running"
        label = "phase_5_5 已跳过（mock/dry-run）"
    elif _PHASE_5_5_PAUSED_RE.match(raw):
        completed_idx = 5
        running_idx = 6
        state = "needs_human"
        label = "phase_5_5 朱雀异常，需人工介入"
    elif _PHASE_5_5_REJECTED_RE.match(raw):
        completed_idx = 5
        running_idx = 6
        state = "failed"
        failed_at = "phase_5_5"
        label = "phase_5_5 朱雀拒绝（多轮仍不显著）"
    elif _PHASE_NEEDS_HUMAN_RE.match(raw):
        # AI 审核未通过 → needs_human：phase_6 卡住等人工 (index 7, after phase_5_5)
        completed_idx = 5  # phases 0-5 done
        running_idx = 7    # phase_6 at index 7
        state = "needs_human"
        label = "AI 审核未通过，等待人工"
    elif _PHASE_REJECTED_RE.match(raw):
        # 人工拒绝：phase_6 失败终态 (index 7)
        completed_idx = 5
        running_idx = 7
        state = "failed"
        failed_at = "phase_6"
        label = "人工拒绝"
    elif _PHASE_PUBLISH_FAILED_RE.match(raw):
        # 发布失败：phase_7 失败 (index 8), phase_0-6 completed (indices 0-5,7)
        completed_idx = 7  # phase_6 done at index 7
        running_idx = 8    # phase_7 at index 8
        state = "failed"
        failed_at = "phase_7"
        label = "发布失败"
    elif _PHASE_PUBLISH_PAUSED_RE.match(raw):
        # 发布暂停：风控/验证码 / 登录态缺失
        completed_idx = 7
        running_idx = 8
        state = "paused"
        failed_at = "phase_7"
        label = "发布已暂停（风控）"
    elif raw == "complete":
        # Preset pipeline completed all steps
        completed_idx = len(PHASES) - 1
        running_idx = -1
        state = "done"
        label = "全部完成"
    elif raw.endswith("_done"):
        # Custom step completed — treat as terminal (preset pipeline done)
        completed_idx = len(PHASES) - 1
        running_idx = -1
        state = "done"
        label = f"{raw} · 全部完成"
    else:
        # Unknown — surface verbatim and treat as phase 0 baseline.
        running_idx = 0
        state = "pending"
        label = f"未知 current_phase={raw}"

    # Compute percent: each phase is one step of the 6-step bar; "done"
    # rounds to 100%. failed_at and rewrite don't advance the bar past
    # the failing/rewriting phase.
    total = len(PHASES)
    if state == "done":
        percent = 100.0
    else:
        completed = max(0, min(completed_idx + 1, total))
        percent = round(completed / total * 100, 1)

    steps = _build_steps(
        completed_idx=completed_idx,
        running_idx=running_idx,
        state=state,
    )

    return PhaseProgress(
        current_phase=raw,
        percent=percent,
        label=label,
        state=state,
        steps=steps,
        failed_at=failed_at,
        section_index=section_index,
    )


@dataclass(frozen=True)
class PhaseTimelineEntry:
    """One row in the per-phase timeline.

    ``entered_at`` is the ISO timestamp when the orchestrator first emitted
    a marker for this phase (running / done / rewrite / failed / section).
    ``completed_at`` is set when a ``phase_N_done`` (or terminal failure)
    marker was seen. ``duration_seconds`` is None while still in flight.
    """

    phase: str
    label: str
    status: str
    entered_at: str
    completed_at: str | None
    duration_seconds: float | None


@dataclass(frozen=True)
class PhaseAttempt:
    """One generation attempt — orchestrator restarts from phase_0 on retry,
    so a "story" can have several attempts. Each attempt has its own
    timeline plus the highest phase it actually reached.
    """

    attempt: int
    started_at: str
    ended_at: str | None
    status: str  # "in_progress" / "done" / "failed" / "rewrite"
    failed_at: str | None
    phases: list[PhaseTimelineEntry]


@dataclass(frozen=True)
class Phase3SectionProgress:
    """Sub-progress for the per-section Phase 3."""

    current: int
    total: int | None
    completed: list[int]


def _build_steps(*, completed_idx: int, running_idx: int, state: str) -> list[PhaseStep]:
    """Return per-phase status rows (one per PHASES entry)."""

    steps: list[PhaseStep] = []
    for idx, phase in enumerate(PHASES):
        if idx <= completed_idx:
            status = "done"
        elif idx == running_idx:
            if state == "failed":
                status = "failed"
            elif state == "rewrite":
                status = "rewrite"
            else:
                status = "in_progress"
        else:
            status = "pending"
        steps.append(PhaseStep(phase=phase, label=PHASE_LABELS[phase], status=status))
    return steps


# ============================================================ work_dir browser


def split_attempts(transitions: Iterable[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Split a flat transition list into per-attempt buckets.

    Two kinds of retries land in ``phase_transitions``:

    * **Reset-style** (legacy): orchestrator wipes ``current_phase`` back
      to ``phase_0`` so we see a ``phase_0`` / ``phase_0_running`` marker
      after the previous failure.
    * **Resume-style** (current): ``atomic_runner`` re-runs from the
      failed phase via ``resume_from`` so we see ``phase_N_running``
      directly after ``failed_at_phase_N`` with no ``phase_0`` reset in
      between.

    A new attempt starts when either signal appears.
    """

    attempts: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    seen_higher = False
    has_failure = False
    for row in transitions:
        marker = (row.get("phase") or "").strip()
        is_reset = marker in ("phase_0", "phase_0_running")
        is_running = bool(_PHASE_RUNNING_RE.match(marker))

        is_boundary = False
        if is_reset and seen_higher:
            is_boundary = True
        elif is_running and has_failure:
            is_boundary = True

        if is_boundary:
            if current:
                attempts.append(current)
            current = []
            seen_higher = False
            has_failure = False

        current.append(row)
        if not is_reset:
            seen_higher = True
        if _PHASE_FAILED_RE.match(marker):
            has_failure = True
    if current:
        attempts.append(current)
    return attempts


def compute_attempts(
    transitions: Iterable[dict[str, str]],
    *,
    now_iso: str | None = None,
) -> list[PhaseAttempt]:
    """Group transitions into ``PhaseAttempt`` records.

    Each attempt's timeline is computed via :func:`compute_phase_timeline`
    on its own slice of transitions, so durations never bleed across
    retries.
    """

    out: list[PhaseAttempt] = []
    buckets = split_attempts(list(transitions))
    for idx, bucket in enumerate(buckets, start=1):
        is_last = idx == len(buckets)
        bucket_now = now_iso if is_last else None
        timeline = compute_phase_timeline(bucket, now_iso=bucket_now)
        started_at = timeline[0].entered_at if timeline else ""
        failed_at = None
        status = "in_progress"
        for entry in timeline:
            if entry.status == "failed":
                failed_at = entry.phase
                status = "failed"
        if status != "failed":
            if timeline and timeline[-1].status == "done" and timeline[-1].phase == "phase_5":
                status = "done"
            elif timeline and timeline[-1].status in {"in_progress", "rewrite"}:
                status = timeline[-1].status
            elif timeline:
                status = timeline[-1].status
        ended_at = None
        if status in {"done", "failed"} and timeline:
            ended_at = timeline[-1].completed_at or timeline[-1].entered_at
        out.append(
            PhaseAttempt(
                attempt=idx,
                started_at=started_at,
                ended_at=ended_at,
                status=status,
                failed_at=failed_at,
                phases=timeline,
            )
        )
    return out


def compute_overall_steps(
    transitions: Iterable[dict[str, str]],
    current_phase: str | None,
) -> list[PhaseStep]:
    """Per-phase status across all retries — what the chip strip should show.

    For each phase 0..5 we walk every transition in chronological order and
    keep the *latest* terminal status emitted for that phase (done /
    failed / rewrite). The currently-active phase derived from
    ``current_phase`` always wins so the chip reflects the live retry. If
    a later phase has a terminal failure but the active attempt has not
    yet retried that far, we keep the historical "failed" so the user can
    still see where the pipeline got stuck.

    When the transitions list is empty (legacy story rows created before
    the table existed) we fall back to :func:`compute_phase_progress` so
    the chip strip still derives sensibly from ``current_phase`` alone.
    """

    rows = list(transitions)
    baseline_steps = compute_phase_progress(current_phase).steps
    base_status = {step.phase: step.status for step in baseline_steps}

    historical: dict[int, str] = {}
    for row in rows:
        marker = (row.get("phase") or "").strip()
        n = _phase_index_for_marker(marker)
        if n is None or marker == "phase_0":
            continue
        if _PHASE_DONE_RE.match(marker):
            historical[n] = "done"
        elif _PHASE_FAILED_RE.match(marker):
            historical[n] = "failed"
        elif _PHASE_REWRITE_RE.match(marker):
            historical[n] = "rewrite"
        else:
            historical[n] = "in_progress"

    active_idx, active_state = _interpret_current_phase(current_phase)

    steps: list[PhaseStep] = []
    for idx, phase in enumerate(PHASES):
        if idx in historical:
            status = historical[idx]
        else:
            status = base_status[phase]
        if idx == active_idx:
            if active_state == "failed":
                status = "failed"
            elif active_state == "rewrite":
                status = "rewrite"
            elif active_state == "done":
                status = "done"
            else:
                status = "in_progress"
        steps.append(PhaseStep(phase=phase, label=PHASE_LABELS[phase], status=status))
    return steps


def _interpret_current_phase(current_phase: str | None) -> tuple[int, str]:
    """Map a stories.current_phase value to (phase_index, state)."""

    raw = (current_phase or "").strip() or "phase_0"
    if raw in PHASES:
        return PHASES.index(raw), "in_progress"
    m = _PHASE_RUNNING_RE.match(raw)
    if m:
        return int(m.group(1)), "in_progress"
    m = _PHASE_DONE_RE.match(raw)
    if m:
        n = int(m.group(1))
        # If phase_5_done, no active phase.
        if n >= len(PHASES) - 1:
            return -1, "done"
        return n + 1, "in_progress"
    m = _PHASE_REWRITE_RE.match(raw)
    if m:
        return int(m.group(1)), "rewrite"
    m = _PHASE_FAILED_RE.match(raw)
    if m:
        return int(m.group(1)), "failed"
    m = _PHASE_SECTION_RE.match(raw)
    if m:
        return 3, "in_progress"
    return 0, "in_progress"


def compute_phase_timeline(
    transitions: Iterable[dict[str, str]],
    *,
    now_iso: str | None = None,
) -> list[PhaseTimelineEntry]:
    """Aggregate raw phase_transitions rows into a per-phase timeline.

    ``transitions`` is the chronological output of
    ``review_queue.db.list_phase_transitions``. We collapse all markers
    referencing the same Phase N (running / section_NN / done / failed /
    rewrite) into a single entry whose ``entered_at`` is the first marker's
    timestamp and ``completed_at`` is the matching ``phase_N_done`` (or
    ``failed_at_phase_N``) timestamp.

    The returned list is in execution order. Phases that have not started
    yet are omitted — the dashboard combines this with PHASES to render
    placeholders for pending steps.
    """

    by_phase: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for row in transitions:
        marker = (row.get("phase") or "").strip()
        ts = row.get("occurred_at") or ""
        if not marker:
            continue
        n = _phase_index_for_marker(marker)
        if n is None:
            continue
        bucket = by_phase.get(n)
        if bucket is None:
            bucket = {"entered_at": ts, "completed_at": None, "status": "in_progress"}
            by_phase[n] = bucket
            order.append(n)
        if _PHASE_DONE_RE.match(marker):
            bucket["completed_at"] = ts
            bucket["status"] = "done"
        elif _PHASE_FAILED_RE.match(marker):
            bucket["completed_at"] = ts
            bucket["status"] = "failed"
        elif _PHASE_REWRITE_RE.match(marker):
            bucket["status"] = "rewrite"

    out: list[PhaseTimelineEntry] = []
    for n in order:
        bucket = by_phase[n]
        phase_key = f"phase_{n}"
        duration = _duration_seconds(
            bucket["entered_at"],
            bucket["completed_at"] or now_iso,
        )
        out.append(
            PhaseTimelineEntry(
                phase=phase_key,
                label=PHASE_LABELS.get(phase_key, phase_key),
                status=bucket["status"],
                entered_at=bucket["entered_at"],
                completed_at=bucket["completed_at"],
                duration_seconds=duration,
            )
        )
    return out


def compute_phase3_section_progress(
    transitions: Iterable[dict[str, str]],
    *,
    work_dir: Path | None = None,
) -> Phase3SectionProgress | None:
    """Return Phase 3 sub-progress (sections completed vs. total).

    Only the *current attempt* is considered — the orchestrator restarts
    from phase_0 on retry, so previously completed sections from older
    attempts would otherwise look like duplicate progress.

    ``current`` is the highest section index seen in the current attempt;
    ``total`` is parsed from ``2_小节大纲.md``'s ``- section_count: N``
    line when the outline already exists.
    """

    rows = list(transitions)
    if not rows:
        return None
    attempts = split_attempts(rows)
    last_attempt = attempts[-1] if attempts else []
    completed: list[int] = []
    for row in last_attempt:
        marker = (row.get("phase") or "").strip()
        m = _PHASE_SECTION_RE.match(marker)
        if not m:
            continue
        idx = int(m.group(1))
        if idx not in completed:
            completed.append(idx)
    if not completed:
        return None
    completed.sort()
    total = _read_outline_section_count(work_dir) if work_dir is not None else None
    return Phase3SectionProgress(current=completed[-1], total=total, completed=completed)


def list_phase_artifacts(
    work_dir: Path | None,
) -> dict[str, list[dict[str, Any]]]:
    """Return ``{phase: [{name, exists, size_bytes}]}`` for each known phase.

    Used by the dashboard to wire "查看产物" buttons. ``exists`` is False
    when the file has not been written yet (phase still pending or in
    flight). ``size_bytes`` is None when the file is missing.
    """

    out: dict[str, list[dict[str, Any]]] = {}
    for phase, names in PHASE_ARTIFACTS.items():
        rows: list[dict[str, Any]] = []
        for name in names:
            target = (work_dir / name) if work_dir is not None else None
            if target is not None and target.exists() and target.is_file():
                try:
                    size = target.stat().st_size
                except OSError:
                    size = None
                rows.append({"name": name, "exists": True, "size_bytes": size})
            else:
                rows.append({"name": name, "exists": False, "size_bytes": None})
        out[phase] = rows
    return out


def _phase_index_for_marker(marker: str) -> int | None:
    """Map a transition marker like ``phase_3_section_07_done`` to phase 3."""

    if marker in PHASES:
        return PHASES.index(marker)
    for regex in (_PHASE_RUNNING_RE, _PHASE_DONE_RE, _PHASE_REWRITE_RE):
        m = regex.match(marker)
        if m:
            return int(m.group(1))
    m = _PHASE_FAILED_RE.match(marker)
    if m:
        return int(m.group(1))
    m = _PHASE_SECTION_RE.match(marker)
    if m:
        return 3
    return None


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        s = _parse_ts(start)
        e = _parse_ts(end)
    except ValueError:
        return None
    delta = (e - s).total_seconds()
    return round(delta, 1) if delta >= 0 else None


def _parse_ts(value: str) -> datetime:
    """Parse a SQLite ``CURRENT_TIMESTAMP`` value (UTC, no timezone suffix)."""

    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1]
    if "T" not in cleaned and " " in cleaned:
        cleaned = cleaned.replace(" ", "T", 1)
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _read_outline_section_count(work_dir: Path) -> int | None:
    outline = work_dir / "2_小节大纲.md"
    if not outline.exists() or not outline.is_file():
        return None
    try:
        text = outline.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _OUTLINE_SECTION_COUNT_RE.search(text)
    if not match:
        return None
    try:
        n = int(match.group(1))
        return n if n > 0 else None
    except ValueError:
        return None


def list_work_dir_files(
    work_dir: Path,
    *,
    suffixes: Iterable[str] | None = None,
) -> list[WorkDirFile]:
    """Return a sorted listing of files inside ``work_dir`` (top level only).

    The c_pipeline writes flat artifacts (0_选题.json, 1_设定.md, ...,
    5_最终稿.md, meta.json) so a single-level scan is sufficient; sub-
    directories are ignored to keep the UI predictable.
    """

    work_dir = Path(work_dir)
    if not work_dir.exists() or not work_dir.is_dir():
        return []
    suffixes_set = set(suffixes or TEXT_SUFFIXES)
    out: list[WorkDirFile] = []
    for entry in sorted(work_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        suffix = entry.suffix.lower()
        out.append(
            WorkDirFile(
                name=entry.name,
                relative_path=entry.name,
                size_bytes=int(stat.st_size),
                modified_at=modified_at,
                is_text=suffix in suffixes_set or not suffix,
            )
        )
    return out


def read_work_dir_file(
    work_dir: Path,
    filename: str,
    *,
    max_bytes: int = MAX_READABLE_FILE_BYTES,
) -> str:
    """Return ``work_dir / filename`` text content, with safety guards.

    Raises:
        FileNotFoundError: file does not exist.
        PermissionError: filename escapes ``work_dir`` (path traversal).
        ValueError: file is too large to send to the dashboard or is
            not within the allow-listed text suffix set.
    """

    work_dir = Path(work_dir).resolve()
    if not work_dir.exists():
        raise FileNotFoundError(f"work_dir does not exist: {work_dir}")

    candidate = (work_dir / filename).resolve()
    try:
        candidate.relative_to(work_dir)
    except ValueError as exc:
        raise PermissionError(
            f"refused: {filename!r} escapes work_dir {work_dir}"
        ) from exc

    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"file not found: {filename}")

    suffix = candidate.suffix.lower()
    if suffix and suffix not in TEXT_SUFFIXES:
        raise ValueError(
            f"refused: {filename!r} suffix {suffix!r} is not in text allow-list"
        )

    size = candidate.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"refused: {filename!r} size {size} bytes exceeds limit {max_bytes}"
        )
    return candidate.read_text(encoding="utf-8", errors="replace")


# ============================================================ resume_from


def normalize_resume_from(value: str | None) -> str:
    """Validate a phase identifier supplied by the dashboard's resume button.

    Returns the canonical ``phase_N`` form if the input is recognized.
    Only c_pipeline generation phases (phase_0 through phase_5) are
    resumable — phase_6 (审核) and phase_7 (发布) are not orchestrator
    steps and have their own buttons (批准 / 拒绝 / 改写 phase_5 /
    重试发布 / 复制内容).

    Raises ``ValueError`` otherwise — the caller should surface a 400.
    """

    raw = (value or "").strip().lower()
    # c_pipeline orchestrator only generates phase_0..phase_5.
    GENERATION_PHASES = PHASES[:6]
    if raw in GENERATION_PHASES:
        return raw
    # accept "phase_N_done" -> normalize to next generation phase;
    # phase_5_done -> reject (review is the next step, not a c_pipeline phase)
    m = _PHASE_DONE_RE.match(raw)
    if m:
        n = int(m.group(1))
        if n >= len(GENERATION_PHASES) - 1:
            raise ValueError(f"phase_{n}_done is terminal for generation, nothing to resume")
        return GENERATION_PHASES[n + 1]
    raise ValueError(f"unsupported resume_from={value!r}; expected one of {GENERATION_PHASES}")


__all__ = [
    "MAX_READABLE_FILE_BYTES",
    "PHASE_ARTIFACTS",
    "PHASE_LABELS",
    "PHASES",
    "Phase3SectionProgress",
    "PhaseAttempt",
    "PhaseProgress",
    "PhaseStep",
    "PhaseTimelineEntry",
    "TEXT_SUFFIXES",
    "WorkDirFile",
    "compute_attempts",
    "compute_overall_steps",
    "compute_phase3_section_progress",
    "compute_phase_progress",
    "compute_phase_timeline",
    "list_phase_artifacts",
    "list_work_dir_files",
    "normalize_resume_from",
    "read_work_dir_file",
    "split_attempts",
]
