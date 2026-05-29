"""Phase H.1 — coverage smoke tests for cli/* + scheduler.

Goal: exercise CLI argparse + main() bodies in dry-run/mock mode so the
full project coverage stays ≥ 80%. Each test stubs the orchestrator /
scan / review module so the CLI wrappers run without real LLM calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli import ai_review as ai_review_cli
from cli import batch_generate as batch_cli
from cli import continue_pipeline as continue_cli
from cli import generate as generate_cli
from cli import scan_now as scan_now_cli
from generator.c_pipeline.orchestrator import PipelineError, PipelineResult


def _setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "anp.sqlite3"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"""
deepseek:
  api_key: ""
  mock: true
runtime:
  mode: "semi-auto"
  dry_run: true
audit:
  approval_threshold: 90
publisher:
  default_platform: "fansq"
  daily_count_min: 0
  daily_count_max: 5
  operating_hours: ["09:00", "22:00"]
  slot_min_gap_minutes: 30
scheduler:
  enabled: false
  timezone: "Asia/Shanghai"
database:
  sqlite_path: "{str(db_path).replace(chr(92), '/')}"
logging:
  level: "INFO"
  file: "{str(tmp_path / 'anp.log').replace(chr(92), '/')}"
cost_limits:
  monthly_budget_cny: 100
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_CONFIG", str(cfg_path))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    return db_path


def _fake_pipeline_result(story_id: int = 7) -> PipelineResult:
    return PipelineResult(
        story_id=story_id,
        work_dir=Path("/tmp/works") / str(story_id),
        final_phase="phase_5_done",
        status="pending",
        final_content_path=Path("/tmp/works/7/5_最终稿.md"),
        used_fallback=False,
        needs_human=False,
        total_cost_cny=0.5,
        final_title="测试标题",
        summary="一句话简介。",
        char_count=10000,
        sections_needs_human=0,
        duration_seconds=1.23,
    )


# ============================================================ cli.generate


def test_cli_generate_main_returns_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        generate_cli, "run_pipeline", lambda **kwargs: _fake_pipeline_result()
    )
    rc = generate_cli.main(
        ["--theme", "abc", "--style", "番茄短篇", "--word-count", "9000",
         "--print-summary", "--print-ids"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "story_id=7" in out
    assert "测试标题" in out


def test_cli_generate_rejects_negative_word_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_env(tmp_path, monkeypatch)
    rc = generate_cli.main(["--word-count", "-5"])
    assert rc == 2


def test_cli_generate_handles_pipeline_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_env(tmp_path, monkeypatch)

    def boom(**kwargs):
        raise PipelineError("phase 0 boom")

    monkeypatch.setattr(generate_cli, "run_pipeline", boom)
    rc = generate_cli.main([])
    assert rc == 1


# ============================================================ cli.batch_generate


def test_cli_batch_generate_runs_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_env(tmp_path, monkeypatch)
    counter = {"n": 0}

    def fake_run(**kwargs):
        counter["n"] += 1
        return _fake_pipeline_result(story_id=counter["n"])

    monkeypatch.setattr(batch_cli, "run_pipeline", fake_run)
    rc = batch_cli.main(["--count", "3", "--print-ids"])
    assert rc == 0
    assert counter["n"] == 3


def test_cli_batch_generate_continues_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_env(tmp_path, monkeypatch)
    state = {"n": 0}

    def fake_run(**kwargs):
        state["n"] += 1
        if state["n"] == 2:
            raise PipelineError("middle boom")
        return _fake_pipeline_result(story_id=state["n"])

    monkeypatch.setattr(batch_cli, "run_pipeline", fake_run)
    rc = batch_cli.main(["--count", "3", "--continue-on-error"])
    assert rc == 0
    assert state["n"] == 3


def test_cli_batch_generate_rejects_zero_count(tmp_path: Path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)
    rc = batch_cli.main(["--count", "0"])
    assert rc == 2


def test_cli_batch_generate_rejects_negative_word_count(tmp_path: Path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)
    rc = batch_cli.main(["--count", "1", "--word-count", "-1"])
    assert rc == 2


def test_cli_batch_generate_stops_on_error_without_flag(tmp_path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)

    def boom(**kwargs):
        raise PipelineError("instant boom")

    monkeypatch.setattr(batch_cli, "run_pipeline", boom)
    rc = batch_cli.main(["--count", "3"])
    assert rc == 1


# ============================================================ cli.continue_pipeline


def test_cli_continue_pipeline_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        continue_cli, "run_pipeline", lambda **kwargs: _fake_pipeline_result()
    )
    rc = continue_cli.main(["--story-id", "1", "--resume-from", "phase_3"])
    assert rc == 0


def test_cli_continue_pipeline_handles_error(tmp_path: Path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)

    def boom(**kwargs):
        raise PipelineError("resume failed")

    monkeypatch.setattr(continue_cli, "run_pipeline", boom)
    rc = continue_cli.main(["--story-id", "1", "--resume-from", "phase_4"])
    assert rc == 1


# ============================================================ cli.scan_now


def test_cli_scan_now_dry_run_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_env(tmp_path, monkeypatch)

    fake_result = SimpleNamespace(
        iso_week="2026W19",
        item_count=100,
        used_fallback=False,
        pool_path=tmp_path / "data" / "theme_pool.json",
        backed_up_to=None,
        weekly_topics=["拆迁", "婆媳"],
        warnings=["one warning"],
    )

    monkeypatch.setattr(
        scan_now_cli, "run_weekly_scan", lambda *a, **k: fake_result
    )
    monkeypatch.setattr(scan_now_cli, "load_seeds", lambda *a, **k: {
        "emotion_types": [], "genres": [], "title_patterns": {},
        "opening_modes": [], "ending_modes": [], "reversal_types": [],
        "target_platform": {"primary": "番茄短篇", "comparator_platforms": {}},
        "diversity_constraints": {},
    })

    monkeypatch.setattr(sys, "argv", ["cli.scan_now", "--dry-run"])
    rc = scan_now_cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "iso_week=2026W19" in out
    assert "item_count=100" in out
    assert "warning: one warning" in out


def test_cli_scan_now_blocked_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_env(tmp_path, monkeypatch)

    def boom(*a, **k):
        from scan import WeeklyScanBlockedError
        raise WeeklyScanBlockedError("nothing on disk")

    monkeypatch.setattr(scan_now_cli, "run_weekly_scan", boom)
    monkeypatch.setattr(scan_now_cli, "load_seeds", lambda *a, **k: {
        "emotion_types": [], "genres": [], "title_patterns": {},
        "opening_modes": [], "ending_modes": [], "reversal_types": [],
        "target_platform": {"primary": "番茄短篇", "comparator_platforms": {}},
        "diversity_constraints": {},
    })
    monkeypatch.setattr(sys, "argv", ["cli.scan_now", "--dry-run"])
    rc = scan_now_cli.main()
    assert rc == 1


def test_dry_run_scan_client_emits_pool() -> None:
    from cli.scan_now import _DryRunScanClient

    seeds = {
        "emotion_types": [{"id": "a"}, {"id": "b"}],
        "genres": [{"id": "g1"}, {"id": "g2"}],
        "title_patterns": {"p1": {}, "p2": {}},
        "opening_modes": [{"id": "o1"}, {"id": "o2"}],
        "ending_modes": [{"id": "e1"}, {"id": "e2"}],
        "reversal_types": [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}, {"id": "r4"}],
        "target_platform": {"primary": "番茄短篇", "comparator_platforms": {"七猫短篇": {}}},
        "diversity_constraints": {},
    }
    client = _DryRunScanClient(seeds=seeds)
    completion = client.chat_completion([], thinking_mode=False, model="x")
    items = json.loads(completion.text)
    assert len(items) == 100
    assert client.is_mock() is True


# ============================================================ cli.ai_review


def test_cli_ai_review_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_env(tmp_path, monkeypatch)
    fake_result = SimpleNamespace(
        message="batch ok", reviewed=2, approved=1, needs_human=1, failed=0,
        failure_reasons=[],
    )
    monkeypatch.setattr(ai_review_cli, "run_review_batch", lambda *a, **k: fake_result)
    monkeypatch.setattr(sys, "argv", ["cli.ai_review", "--limit", "5"])
    rc = ai_review_cli.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "reviewed=2" in out


def test_cli_ai_review_rejects_bad_threshold(tmp_path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cli.ai_review", "--threshold", "200"])
    with pytest.raises(SystemExit):
        ai_review_cli.main()


def test_cli_ai_review_rejects_zero_limit(tmp_path, monkeypatch) -> None:
    _setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cli.ai_review", "--limit", "0"])
    with pytest.raises(SystemExit):
        ai_review_cli.main()
