"""Tests for the long-novel autopilot orchestrator (Phase 1: setup→outline stages).

The orchestrator is intentionally DB-free: it runs an ordered list of
``AutopilotStage`` objects, skipping ones already done, honouring a cancel
callback, and stopping on the first error. The DB-coupled ``finalize`` stage is
injected by the API layer, so these tests cover the pure orchestration plus the
file-based L0 stage wiring and idempotency checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generator.long_novel.autopilot import (
    AutopilotStage,
    build_l0_stages,
    l0_phase_done,
    read_autopilot_file,
    run_chapter_loop,
    run_stages,
    write_autopilot_file,
)

# ── helpers ───────────────────────────────────────────────────────────


_REVIEW_DIMS = ["continuity", "logic", "plot_progress", "character_integrity", "environment", "empathy"]


def _review_json(verdict: str) -> str:
    """Build a story-review LLM payload that normalizes to passed / not-passed."""
    if verdict == "APPROVE":
        dims = {k: {"verdict": "APPROVE", "score": 92, "strengths": [], "findings": [], "recommendations": []} for k in _REVIEW_DIMS}
        return json.dumps({"overall": "APPROVE", "score": 92, "pass_score": 80, "dimensions": dims, "recommendations": []})
    dims = {k: {"verdict": "APPROVE", "score": 90, "strengths": [], "findings": [], "recommendations": []} for k in _REVIEW_DIMS}
    dims["logic"] = {"verdict": "CONCERNS", "score": 70, "strengths": [], "findings": ["第二段存在逻辑矛盾问题"], "recommendations": ["必须补足因果"]}
    return json.dumps({"overall": "CONCERNS", "score": 70, "pass_score": 80, "dimensions": dims, "recommendations": []})


class _ReviewFakeClient:
    """Fake DeepSeek client: distinguishes review / tracking / prose calls.

    ``verdicts`` is consumed one entry per review call (the last entry repeats),
    so a test can make the first review fail and the next pass, or always fail.
    """

    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.review_calls = 0
        self.settings = SimpleNamespace(model="m", flash_model="f")

    def chat_completion(self, messages, **kwargs):  # noqa: ANN001, ANN003
        user = str(messages[-1]["content"])
        if "Review chapter" in user or "six dimensions" in user:
            verdict = self.verdicts[min(self.review_calls, len(self.verdicts) - 1)]
            self.review_calls += 1
            return SimpleNamespace(text=_review_json(verdict))
        if "summary_short" in user:  # tracking-memory extraction
            return SimpleNamespace(text='{"summary_short":"测试摘要","summary_long":"较长的测试章节摘要内容。"}')
        return SimpleNamespace(text="这是用于测试的章节正文内容。" * 6)


def _stage(name: str, calls: list[str], *, done: bool = False, boom: bool = False) -> AutopilotStage:
    def run() -> None:
        if boom:
            raise RuntimeError(f"{name} boom")
        calls.append(name)

    return AutopilotStage(phase=name, label=name.upper(), run=run, is_done=lambda: done)


class _FakeClient:
    """Mimics DeepSeekClient.chat_completion + .settings used by L0 functions."""

    def __init__(self) -> None:
        self.calls = 0
        self.settings = SimpleNamespace(model="m", flash_model="f")

    def chat_completion(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        prompt = str(messages[-1]["content"])
        if "JSON" in prompt:  # roster stages want a JSON array
            return SimpleNamespace(text='[{"name":"林晚","role":"主角","brief":"测试角色"}]')
        return SimpleNamespace(text="## 测试\n用于测试的正文内容。")


def _make_all_l0_artifacts(wd: Path) -> None:
    (wd / "设定" / "世界观").mkdir(parents=True)
    (wd / "设定" / "角色").mkdir(parents=True)
    (wd / "设定" / "势力").mkdir(parents=True)
    (wd / "大纲").mkdir(parents=True)
    (wd / "设定" / "题材定位.md").write_text("x", encoding="utf-8")
    (wd / "设定" / "世界观" / "背景设定.md").write_text("x", encoding="utf-8")
    (wd / "设定" / "角色" / "林晚.md").write_text("x", encoding="utf-8")
    (wd / "设定" / "势力" / "青云宗.md").write_text("x", encoding="utf-8")
    (wd / "设定" / "关系.md").write_text("x", encoding="utf-8")
    (wd / "大纲" / "大纲.md").write_text("x", encoding="utf-8")
    (wd / "大纲" / "卷纲_第一卷.md").write_text("x", encoding="utf-8")
    (wd / "大纲" / "细纲_第001章.md").write_text("x", encoding="utf-8")


# ── run_stages: pure orchestration ────────────────────────────────────


def test_run_stages_runs_all_in_order() -> None:
    calls: list[str] = []
    snaps: list[dict] = []
    stages = [_stage("a", calls), _stage("b", calls), _stage("c", calls)]

    result = run_stages(stages, write_progress=snaps.append)

    assert calls == ["a", "b", "c"]
    assert result["state"] == "done"
    assert result["total"] == 3
    assert {s["stage"] for s in snaps if s["stage"]} >= {"a", "b", "c"}


def test_run_stages_skips_done_stages() -> None:
    calls: list[str] = []
    stages = [_stage("a", calls, done=True), _stage("b", calls)]

    result = run_stages(stages, write_progress=lambda d: None)

    assert calls == ["b"]  # 'a' was already done → skipped
    assert result["state"] == "done"


def test_run_stages_stops_on_error() -> None:
    calls: list[str] = []
    stages = [_stage("a", calls), _stage("b", calls, boom=True), _stage("c", calls)]

    result = run_stages(stages, write_progress=lambda d: None)

    assert calls == ["a"]  # b raised, c never ran
    assert result["state"] == "error"
    assert result["failed_at"] == "b"
    assert "boom" in result["detail"]


def test_run_stages_honors_cancel_before_next_stage() -> None:
    calls: list[str] = []
    flag = {"cancelled": False}

    def cancelled() -> bool:
        return flag["cancelled"]

    def first_run() -> None:
        calls.append("a")
        flag["cancelled"] = True  # trip cancel after first stage

    stages = [
        AutopilotStage("a", "A", run=first_run, is_done=lambda: False),
        _stage("b", calls),
    ]

    result = run_stages(stages, write_progress=lambda d: None, is_cancelled=cancelled)

    assert calls == ["a"]  # b skipped because cancel tripped
    assert result["state"] == "cancelled"


# ── run_chapter_loop: pure chapter orchestration (Phase 2) ────────────


def test_run_chapter_loop_writes_all_in_order() -> None:
    calls: list[int] = []
    snaps: list[dict] = []

    def write_chapter(ch: int, report) -> dict:  # noqa: ANN001
        report("reviewing", "审核中")
        calls.append(ch)
        return {"chapter": ch, "status": "passed", "words": 100, "revisions": 0}

    result = run_chapter_loop([1, 2, 3], write_chapter=write_chapter, write_progress=snaps.append)

    assert calls == [1, 2, 3]
    assert result["state"] == "done"
    assert result["writing"]["total"] == 3
    assert result["writing"]["done"] == 3
    assert result["writing"]["needs_human"] == []


def test_run_chapter_loop_continues_after_needs_human() -> None:
    calls: list[int] = []

    def write_chapter(ch: int, report) -> dict:  # noqa: ANN001
        calls.append(ch)
        return {"chapter": ch, "status": "needs_human" if ch == 2 else "passed", "revisions": 3}

    result = run_chapter_loop([1, 2, 3], write_chapter=write_chapter, write_progress=lambda d: None)

    assert calls == [1, 2, 3]  # ch2 failing review did NOT stop the loop
    assert result["state"] == "done"
    assert result["writing"]["needs_human"] == [2]


def test_run_chapter_loop_stops_on_exception() -> None:
    calls: list[int] = []

    def write_chapter(ch: int, report) -> dict:  # noqa: ANN001
        calls.append(ch)
        if ch == 2:
            raise RuntimeError("draft boom")
        return {"chapter": ch, "status": "passed"}

    result = run_chapter_loop([1, 2, 3], write_chapter=write_chapter, write_progress=lambda d: None)

    assert calls == [1, 2]  # 3 never ran
    assert result["state"] == "error"
    assert result["failed_at"] == 2
    assert "boom" in result["detail"]


def test_run_chapter_loop_honors_cancel() -> None:
    calls: list[int] = []
    flag = {"cancelled": False}

    def write_chapter(ch: int, report) -> dict:  # noqa: ANN001
        calls.append(ch)
        flag["cancelled"] = True  # trip cancel after first chapter
        return {"chapter": ch, "status": "passed"}

    result = run_chapter_loop(
        [1, 2, 3],
        write_chapter=write_chapter,
        write_progress=lambda d: None,
        is_cancelled=lambda: flag["cancelled"],
    )

    assert calls == [1]
    assert result["state"] == "cancelled"


def test_run_chapter_loop_carries_setup_completed_for_monitor() -> None:
    snaps: list[dict] = []

    run_chapter_loop(
        [1],
        write_chapter=lambda ch, report: {"chapter": ch, "status": "passed"},
        write_progress=snaps.append,
        setup_completed=["premise", "world", "finalize"],
    )

    # Every snapshot keeps the finished setup phases so the 9 chips stay ticked.
    assert snaps
    assert all({"premise", "world", "finalize"}.issubset(set(s["completed"])) for s in snaps)


# ── revise_chapter_once: one review-driven rewrite + re-review ─────────


def test_revise_chapter_once_rewrites_and_rereviews(tmp_path: Path) -> None:
    from generator.long_novel.l2_chapter_write import revise_chapter_once

    client = _ReviewFakeClient(["APPROVE"])
    failing_review = {
        "overall": "CONCERNS",
        "passed": False,
        "dimensions": {"logic": {"findings": ["逻辑矛盾问题"], "recommendations": ["补足因果"]}},
        "recommendations": [],
    }

    text, new_review = revise_chapter_once(client, tmp_path, 2, failing_review, source_text="原始正文。", outline="本章细纲")

    assert text.strip()
    assert new_review["passed"] is True
    assert client.review_calls == 1  # exactly one re-review


# ── build_l0_stages: wiring + idempotency ─────────────────────────────


def test_build_l0_stages_has_eight_phases_in_order() -> None:
    stages = build_l0_stages(
        _FakeClient(),
        "/tmp/book",
        title="t",
        genre="都市",
        premise="p",
        target_chapters=3,
        words_per_chapter=3000,
    )
    assert [s.phase for s in stages] == [
        "premise",
        "world",
        "characters",
        "factions",
        "relations",
        "outline",
        "volume_outline",
        "chapter_outlines",
    ]


def test_build_l0_stages_premise_writes_file(tmp_path: Path) -> None:
    client = _FakeClient()
    stages = build_l0_stages(
        client,
        tmp_path,
        title="测试书",
        genre="都市",
        premise="一句话梗概",
        target_chapters=2,
        words_per_chapter=1000,
    )

    run_stages(stages[:1], write_progress=lambda d: None)  # run premise only

    assert (tmp_path / "设定" / "题材定位.md").exists()
    assert client.calls == 1


def test_build_l0_stages_skips_everything_when_artifacts_exist(tmp_path: Path) -> None:
    _make_all_l0_artifacts(tmp_path)

    class BoomClient:
        settings = SimpleNamespace(model="m", flash_model="f")

        def chat_completion(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("LLM must not be called when artifacts already exist")

    stages = build_l0_stages(
        BoomClient(),
        tmp_path,
        title="t",
        genre="都市",
        premise="p",
        target_chapters=2,
        words_per_chapter=1000,
    )

    result = run_stages(stages, write_progress=lambda d: None)

    assert result["state"] == "done"  # all 8 skipped, no LLM calls raised


# ── l0_phase_done: artifact detection ─────────────────────────────────


def test_l0_phase_done_detects_premise(tmp_path: Path) -> None:
    assert not l0_phase_done(tmp_path, "premise")
    (tmp_path / "设定").mkdir()
    (tmp_path / "设定" / "题材定位.md").write_text("x", encoding="utf-8")
    assert l0_phase_done(tmp_path, "premise")


def test_l0_phase_done_ignores_index_only_character_dir(tmp_path: Path) -> None:
    chars = tmp_path / "设定" / "角色"
    chars.mkdir(parents=True)
    (chars / "_角色索引.md").write_text("x", encoding="utf-8")
    # only the index file exists → not "done"
    assert not l0_phase_done(tmp_path, "characters")
    (chars / "林晚.md").write_text("x", encoding="utf-8")
    assert l0_phase_done(tmp_path, "characters")


# ── progress file IO ──────────────────────────────────────────────────


def test_autopilot_file_round_trip(tmp_path: Path) -> None:
    assert read_autopilot_file(tmp_path) is None

    write_autopilot_file(tmp_path, {"state": "running", "stage": "world"})

    data = read_autopilot_file(tmp_path)
    assert data is not None
    assert data["state"] == "running"
    assert data["stage"] == "world"
