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
    PHASE_ARTIFACTS,
    PHASES,
    compute_attempts,
    compute_overall_steps,
    compute_phase3_section_progress,
    compute_phase_progress,
    compute_phase_timeline,
    list_phase_artifacts,
    list_work_dir_files,
    normalize_resume_from,
    read_work_dir_file,
    split_attempts,
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
    assert info.percent == round(3 / 9 * 100, 1)  # phase_0..phase_2 done out of 9 (含 phase_5_5)
    assert [s.status for s in info.steps[:3]] == ["done", "done", "done"]
    assert info.steps[3].status == "in_progress"
    assert info.steps[3].phase == "phase_3"


def test_progress_phase_5_done_advances_to_review() -> None:
    """phase_5 完成 → 进入 phase_5_5 朱雀检测（不再是 phase_6 审核）。"""
    info = compute_phase_progress("phase_5_done")
    assert info.state == "running"
    assert info.steps[5].status == "done"
    assert info.steps[6].status == "in_progress"
    assert info.steps[6].phase == "phase_5_5"


def test_progress_phase_7_done_is_complete() -> None:
    """全流程完成（生成 + 朱雀 + 审核 + 发布）才算 100%。"""
    info = compute_phase_progress("phase_7_done")
    assert info.state == "done"
    assert info.percent == 100.0
    for step in info.steps:
        assert step.status == "done"


def test_progress_phase_6_done_advances_to_publish() -> None:
    """审核通过 → phase_6_done，等待人工触发 phase_7 发布。"""
    info = compute_phase_progress("phase_6_done")
    assert info.state == "running"
    # phase_6 at index 7 in the 9-phase tuple
    assert info.steps[7].status == "done"
    assert info.steps[8].status == "in_progress"
    assert info.steps[8].phase == "phase_7"


def test_progress_phase_6_needs_human() -> None:
    """AI 审核未通过 → 卡在 phase_6 等人工。"""
    info = compute_phase_progress("phase_6_needs_human")
    assert info.state == "needs_human"
    assert info.steps[5].status == "done"      # phase_5
    assert info.steps[7].status in ("in_progress", "needs_human")  # phase_6 at index 7
    assert info.steps[8].status == "pending"    # phase_7 at index 8


def test_progress_phase_6_rejected_marks_failed() -> None:
    """人工拒绝 → phase_6 失败终态。"""
    info = compute_phase_progress("phase_6_rejected")
    assert info.state == "failed"
    assert info.failed_at == "phase_6"


def test_progress_phase_7_failed_can_retry() -> None:
    """发布失败 → phase_7 失败，但 phase_0~6 已完成。"""
    info = compute_phase_progress("phase_7_failed")
    assert info.state == "failed"
    assert info.failed_at == "phase_7"
    assert info.steps[7].status == "done"       # phase_6 at index 7
    assert info.steps[8].status in ("failed", "in_progress")  # phase_7 at index 8


def test_progress_phase_7_paused_marks_paused() -> None:
    """发布暂停（风控/登录态）。"""
    info = compute_phase_progress("phase_7_paused")
    assert info.state == "paused"
    assert info.failed_at == "phase_7"


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


def test_progress_phases_constant_is_eight_entries() -> None:
    """生成 6 阶段 + 朱雀检测 + 审核 + 发布 = 9。"""
    assert len(PHASES) == 9
    assert PHASES[0] == "phase_0"
    assert PHASES[5] == "phase_5"
    assert PHASES[6] == "phase_5_5"
    assert PHASES[7] == "phase_6"
    assert PHASES[-1] == "phase_7"


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


# ============================================================ compute_phase_timeline


def test_timeline_aggregates_phase_running_and_done() -> None:
    transitions = [
        {"phase": "phase_0_running", "occurred_at": "2026-05-08 10:00:00"},
        {"phase": "phase_0_done",    "occurred_at": "2026-05-08 10:00:30"},
        {"phase": "phase_1_running", "occurred_at": "2026-05-08 10:00:31"},
        {"phase": "phase_1_done",    "occurred_at": "2026-05-08 10:02:01"},
    ]
    tl = compute_phase_timeline(transitions)
    assert [e.phase for e in tl] == ["phase_0", "phase_1"]
    assert tl[0].status == "done"
    assert tl[0].duration_seconds == 30.0
    assert tl[1].duration_seconds == 90.0


def test_timeline_in_flight_phase_uses_now_iso() -> None:
    transitions = [
        {"phase": "phase_0_running", "occurred_at": "2026-05-08 10:00:00"},
        {"phase": "phase_0_done",    "occurred_at": "2026-05-08 10:00:10"},
        {"phase": "phase_1_running", "occurred_at": "2026-05-08 10:00:15"},
    ]
    tl = compute_phase_timeline(transitions, now_iso="2026-05-08 10:00:45")
    assert tl[1].status == "in_progress"
    assert tl[1].completed_at is None
    assert tl[1].duration_seconds == 30.0


def test_timeline_failed_marker_marks_phase_failed() -> None:
    tl = compute_phase_timeline([
        {"phase": "phase_2_running", "occurred_at": "2026-05-08 10:00:00"},
        {"phase": "failed_at_phase_2", "occurred_at": "2026-05-08 10:00:05"},
    ])
    assert tl[0].status == "failed"
    assert tl[0].duration_seconds == 5.0


def test_timeline_section_markers_collapse_under_phase_3() -> None:
    tl = compute_phase_timeline([
        {"phase": "phase_3_running", "occurred_at": "2026-05-08 10:00:00"},
        {"phase": "phase_3_section_01_done", "occurred_at": "2026-05-08 10:00:30"},
        {"phase": "phase_3_section_02_done", "occurred_at": "2026-05-08 10:01:00"},
        {"phase": "phase_3_done", "occurred_at": "2026-05-08 10:02:00"},
    ])
    assert len(tl) == 1
    assert tl[0].phase == "phase_3"
    assert tl[0].duration_seconds == 120.0


def test_timeline_skips_unknown_markers() -> None:
    tl = compute_phase_timeline([
        {"phase": "garbage_marker", "occurred_at": "2026-05-08 10:00:00"},
    ])
    assert tl == []


# ============================================================ phase 3 sub-progress


def test_section_progress_returns_none_before_phase_3(tmp_path: Path) -> None:
    assert compute_phase3_section_progress([], work_dir=tmp_path) is None


def test_section_progress_reports_latest_completed_index(tmp_path: Path) -> None:
    progress = compute_phase3_section_progress(
        [
            {"phase": "phase_3_running", "occurred_at": "2026-05-08 10:00:00"},
            {"phase": "phase_3_section_01_done", "occurred_at": "2026-05-08 10:00:10"},
            {"phase": "phase_3_section_03_done", "occurred_at": "2026-05-08 10:00:30"},
        ],
        work_dir=tmp_path,
    )
    assert progress is not None
    assert progress.current == 3
    assert progress.completed == [1, 3]
    assert progress.total is None  # outline file absent


def test_section_progress_reads_total_from_outline(tmp_path: Path) -> None:
    (tmp_path / "2_小节大纲.md").write_text(
        "# Outline\n- section_count: 8\n",
        encoding="utf-8",
    )
    progress = compute_phase3_section_progress(
        [{"phase": "phase_3_section_05_done", "occurred_at": "2026-05-08 10:00:00"}],
        work_dir=tmp_path,
    )
    assert progress is not None
    assert progress.current == 5
    assert progress.total == 8


# ============================================================ artifact listing


def test_list_phase_artifacts_marks_existing_files(tmp_path: Path) -> None:
    (tmp_path / "0_选题.json").write_text("{}", encoding="utf-8")
    (tmp_path / "1_设定.md").write_text("# F", encoding="utf-8")
    out = list_phase_artifacts(tmp_path)
    assert out["phase_0"][0]["exists"] is True
    assert out["phase_0"][0]["size_bytes"] == 2
    assert out["phase_1"][0]["exists"] is True
    assert out["phase_2"][0]["exists"] is False
    assert out["phase_2"][0]["size_bytes"] is None


def test_list_phase_artifacts_with_no_work_dir_marks_all_missing() -> None:
    out = list_phase_artifacts(None)
    for phase, artifacts in PHASE_ARTIFACTS.items():
        if not artifacts:
            # phase_6/phase_7 不产出文件 — 列表为空即正确
            assert out[phase] == []
            continue
        assert out[phase][0]["exists"] is False


# ============================================================ retry / attempts


def _t(phase: str, ts: str) -> dict[str, str]:
    return {"phase": phase, "occurred_at": ts}


def test_split_attempts_no_retry_returns_single_bucket() -> None:
    rows = [
        _t("phase_0_running", "2026-05-08 10:00:00"),
        _t("phase_0_done",    "2026-05-08 10:00:10"),
    ]
    buckets = split_attempts(rows)
    assert len(buckets) == 1
    assert buckets[0] == rows


def test_split_attempts_resets_on_phase_0_re_emission() -> None:
    rows = [
        _t("phase_0_running", "2026-05-08 10:00:00"),
        _t("phase_0_done",    "2026-05-08 10:00:10"),
        _t("phase_4_running", "2026-05-08 10:01:00"),
        _t("failed_at_phase_4", "2026-05-08 10:02:00"),
        _t("phase_0",         "2026-05-08 10:02:01"),  # orchestrator reset
        _t("phase_0_running", "2026-05-08 10:02:02"),
        _t("phase_0_done",    "2026-05-08 10:02:12"),
    ]
    buckets = split_attempts(rows)
    assert len(buckets) == 2
    assert buckets[0][0]["phase"] == "phase_0_running"
    assert buckets[0][-1]["phase"] == "failed_at_phase_4"
    assert buckets[1][0]["phase"] == "phase_0"
    assert buckets[1][-1]["phase"] == "phase_0_done"


def test_compute_attempts_marks_failed_attempt_correctly() -> None:
    rows = [
        _t("phase_0_running", "2026-05-08 10:00:00"),
        _t("phase_0_done",    "2026-05-08 10:00:10"),
        _t("phase_4_running", "2026-05-08 10:01:00"),
        _t("failed_at_phase_4", "2026-05-08 10:02:00"),
        _t("phase_0_running", "2026-05-08 10:02:30"),
        _t("phase_0_done",    "2026-05-08 10:02:40"),
    ]
    attempts = compute_attempts(rows, now_iso="2026-05-08 10:03:00")
    assert len(attempts) == 2
    assert attempts[0].status == "failed"
    assert attempts[0].failed_at == "phase_4"
    # Attempt 2 is still in flight (no done/failed marker for the latest phase).
    assert attempts[1].status in {"in_progress", "done"}


def test_compute_overall_steps_marks_phase_failed_even_when_active_phase_lower() -> None:
    rows = [
        _t("phase_0_running", "2026-05-08 10:00:00"),
        _t("phase_0_done",    "2026-05-08 10:00:10"),
        _t("phase_4_running", "2026-05-08 10:01:00"),
        _t("failed_at_phase_4", "2026-05-08 10:02:00"),
        _t("phase_0",         "2026-05-08 10:02:01"),
        _t("phase_0_running", "2026-05-08 10:02:02"),
        _t("phase_0_done",    "2026-05-08 10:02:12"),
        _t("phase_3_running", "2026-05-08 10:03:00"),
    ]
    # current_phase is phase_3_running (retry attempt active on phase_3).
    steps = compute_overall_steps(rows, "phase_3_running")
    by_phase = {s.phase: s.status for s in steps}
    # phase 4 already failed historically — chip MUST show failed.
    assert by_phase["phase_4"] == "failed"
    # current attempt's active phase is phase_3.
    assert by_phase["phase_3"] == "in_progress"
    assert by_phase["phase_0"] == "done"
    assert by_phase["phase_5"] == "pending"


def test_section_progress_only_counts_current_attempt() -> None:
    # First attempt did sections 1..4; retry restarted and only finished section 1.
    rows = [
        _t("phase_3_running",        "2026-05-08 10:00:00"),
        _t("phase_3_section_01_done", "2026-05-08 10:00:10"),
        _t("phase_3_section_02_done", "2026-05-08 10:00:20"),
        _t("phase_3_section_03_done", "2026-05-08 10:00:30"),
        _t("phase_3_section_04_done", "2026-05-08 10:00:40"),
        _t("failed_at_phase_4",      "2026-05-08 10:01:00"),
        _t("phase_0",                "2026-05-08 10:01:01"),
        _t("phase_0_running",        "2026-05-08 10:01:02"),
        _t("phase_3_running",        "2026-05-08 10:02:00"),
        _t("phase_3_section_01_done", "2026-05-08 10:02:10"),
    ]
    progress = compute_phase3_section_progress(rows, work_dir=None)
    assert progress is not None
    assert progress.current == 1
    assert progress.completed == [1]
