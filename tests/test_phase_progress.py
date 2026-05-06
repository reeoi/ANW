"""Unit tests for ``review_queue.phase_progress`` (Phase F / decision #27)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from review_queue.phase_progress import (
    MAX_READABLE_FILE_BYTES,
    PHASES,
    compute_phase_progress,
    list_work_dir_files,
    normalize_resume_from,
    read_work_dir_file,
)


# ============================================================ compute_phase_progress


def test_progress_phase_0_baseline_treats_as_running() -> None:
    info = compute_phase_progress("phase_0")
    assert info.state == "running"
    assert info.percent == 0.0
    assert info.steps[0].status == "in_progress"
    for step in info.steps[1:]:
        assert step.status == "pending"


def test_progress_phase_2_done_reports_one_third_progress() -> None:
    info = compute_phase_progress("phase_2_done")
    assert info.state == "running"
    assert info.percent == round(3 / 6 * 100, 1)  # phase_0..phase_2 done
    assert [s.status for s in info.steps[:3]] == ["done", "done", "done"]
    assert info.steps[3].status == "in_progress"
    assert info.steps[3].phase == "phase_3"


def test_progress_phase_5_done_is_complete() -> None:
    info = compute_phase_progress("phase_5_done")
    assert info.state == "done"
    assert info.percent == 100.0
    for step in info.steps:
        assert step.status == "done"


def test_progress_phase_3_running_marks_phase_3_in_progress() -> None:
    info = compute_phase_progress("phase_3_running")
    assert info.state == "running"
    assert info.steps[2].status == "done"
    assert info.steps[3].status == "in_progress"


def test_progress_phase_3_section_05_done_keeps_phase_3_in_progress() -> None:
    info = compute_phase_progress("phase_3_section_05_done")
    assert info.state == "running"
    assert info.section_index == 5
    assert info.steps[2].status == "done"
    assert info.steps[3].status == "in_progress"
    assert info.steps[4].status == "pending"


def test_progress_phase_4_rewrite_marks_step_as_rewrite() -> None:
    info = compute_phase_progress("phase_4_rewrite")
    assert info.state == "rewrite"
    assert info.steps[3].status == "done"
    assert info.steps[4].status == "rewrite"


def test_progress_failed_at_phase_2_marks_step_as_failed() -> None:
    info = compute_phase_progress("failed_at_phase_2")
    assert info.state == "failed"
    assert info.failed_at == "phase_2"
    assert info.steps[1].status == "done"
    assert info.steps[2].status == "failed"
    assert info.steps[3].status == "pending"


def test_progress_unknown_string_falls_back_to_phase_0() -> None:
    info = compute_phase_progress("garbage_value")
    assert info.state == "pending"
    assert info.steps[0].status == "in_progress"


def test_progress_none_input_treated_as_phase_0() -> None:
    info = compute_phase_progress(None)
    assert info.current_phase == "phase_0"
    assert info.state == "running"


def test_progress_phases_constant_is_six_entries() -> None:
    assert len(PHASES) == 6
    assert PHASES[0] == "phase_0"
    assert PHASES[-1] == "phase_5"


# ============================================================ list_work_dir_files


def test_list_work_dir_files_returns_files_only_sorted(tmp_path: Path) -> None:
    work_dir = tmp_path / "works" / "1"
    work_dir.mkdir(parents=True)
    (work_dir / "1_设定.md").write_text("setup", encoding="utf-8")
    (work_dir / "0_选题.json").write_text("{}", encoding="utf-8")
    (work_dir / "subdir").mkdir()
    (work_dir / "subdir" / "ignored.md").write_text("nope", encoding="utf-8")

    files = list_work_dir_files(work_dir)
    names = [f.name for f in files]
    assert names == ["0_选题.json", "1_设定.md"]
    for entry in files:
        assert entry.size_bytes >= 0
        assert entry.modified_at.endswith("Z")
        assert entry.is_text is True


def test_list_work_dir_files_missing_returns_empty(tmp_path: Path) -> None:
    assert list_work_dir_files(tmp_path / "no_such") == []


def test_list_work_dir_files_marks_unknown_suffix_as_non_text(tmp_path: Path) -> None:
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    (work_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (work_dir / "1_设定.md").write_text("setup", encoding="utf-8")

    files = list_work_dir_files(work_dir)
    by_name = {f.name: f for f in files}
    assert by_name["image.png"].is_text is False
    assert by_name["1_设定.md"].is_text is True


# ============================================================ read_work_dir_file


def test_read_work_dir_file_returns_text(tmp_path: Path) -> None:
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    (work_dir / "1_设定.md").write_text("# 框架\n\n正文。", encoding="utf-8")

    text = read_work_dir_file(work_dir, "1_设定.md")
    assert "# 框架" in text


def test_read_work_dir_file_path_traversal_rejected(tmp_path: Path) -> None:
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    (tmp_path / "secret.md").write_text("pwd", encoding="utf-8")

    with pytest.raises(PermissionError):
        read_work_dir_file(work_dir, "../secret.md")


def test_read_work_dir_file_missing_raises_filenotfound(tmp_path: Path) -> None:
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        read_work_dir_file(work_dir, "nope.md")


def test_read_work_dir_file_non_text_suffix_rejected(tmp_path: Path) -> None:
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    (work_dir / "binary.bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError):
        read_work_dir_file(work_dir, "binary.bin")


def test_read_work_dir_file_size_limit(tmp_path: Path) -> None:
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    target = work_dir / "huge.md"
    target.write_bytes(b"x" * (MAX_READABLE_FILE_BYTES + 1))
    with pytest.raises(ValueError):
        read_work_dir_file(work_dir, "huge.md")


# ============================================================ normalize_resume_from


def test_normalize_resume_from_accepts_phase_n() -> None:
    assert normalize_resume_from("phase_4") == "phase_4"
    assert normalize_resume_from("PHASE_3") == "phase_3"
    assert normalize_resume_from(" phase_0 ") == "phase_0"


def test_normalize_resume_from_done_advances_to_next() -> None:
    assert normalize_resume_from("phase_2_done") == "phase_3"


def test_normalize_resume_from_terminal_done_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_resume_from("phase_5_done")


def test_normalize_resume_from_unknown_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_resume_from("phase_99")
    with pytest.raises(ValueError):
        normalize_resume_from(None)
    with pytest.raises(ValueError):
        normalize_resume_from("")
