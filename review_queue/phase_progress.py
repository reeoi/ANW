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
from typing import Iterable

PHASES: tuple[str, ...] = (
    "phase_0",
    "phase_1",
    "phase_2",
    "phase_3",
    "phase_4",
    "phase_5",
)

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

    completed_idx = -1  # last *fully* completed phase
    running_idx = 0  # phase currently in flight

    if raw in PHASES:
        # Plain "phase_N" — interpret as just-started.
        running_idx = PHASES.index(raw)
        state = "running"
        label = f"{raw} 进行中"
    elif (m := _PHASE_DONE_RE.match(raw)):
        n = int(m.group(1))
        completed_idx = n
        if n >= len(PHASES) - 1:
            state = "done"
            running_idx = -1  # all phases finished, nothing in flight
            label = "全部完成"
        else:
            running_idx = n + 1
            state = "running"
            label = f"phase_{n} 完成,等待 phase_{n + 1}"
    elif (m := _PHASE_RUNNING_RE.match(raw)):
        n = int(m.group(1))
        completed_idx = n - 1
        running_idx = n
        state = "running"
        label = f"phase_{n} 进行中"
    elif (m := _PHASE_REWRITE_RE.match(raw)):
        n = int(m.group(1))
        completed_idx = n - 1
        running_idx = n
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
        completed_idx = n - 1
        running_idx = n
        state = "failed"
        failed_at = f"phase_{n}"
        label = f"phase_{n} 失败"
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


def _build_steps(*, completed_idx: int, running_idx: int, state: str) -> list[PhaseStep]:
    """Return per-phase status rows (one per PHASES entry)."""

    steps: list[PhaseStep] = []
    labels = {
        "phase_0": "phase_0 选题",
        "phase_1": "phase_1 框架/简介",
        "phase_2": "phase_2 大纲",
        "phase_3": "phase_3 逐节",
        "phase_4": "phase_4 精修",
        "phase_5": "phase_5 去 AI 味",
    }
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
        steps.append(PhaseStep(phase=phase, label=labels[phase], status=status))
    return steps


# ============================================================ work_dir browser


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
    Raises ``ValueError`` otherwise — the caller should surface a 400.
    """

    raw = (value or "").strip().lower()
    if raw in PHASES:
        return raw
    # accept "phase_N_done" -> normalize to next phase; phase_5_done -> reject
    m = _PHASE_DONE_RE.match(raw)
    if m:
        n = int(m.group(1))
        if n >= len(PHASES) - 1:
            raise ValueError(f"phase_{n}_done is terminal, nothing to resume")
        return PHASES[n + 1]
    raise ValueError(f"unsupported resume_from={value!r}; expected one of {PHASES}")


__all__ = [
    "MAX_READABLE_FILE_BYTES",
    "PHASES",
    "PhaseProgress",
    "PhaseStep",
    "TEXT_SUFFIXES",
    "WorkDirFile",
    "compute_phase_progress",
    "list_work_dir_files",
    "normalize_resume_from",
    "read_work_dir_file",
]
