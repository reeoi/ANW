"""Phase F endpoint tests for the dashboard's U2 upgrades (decision #27).

Covers four new APIs in ``review_queue.human_review``:

- GET  /api/stories/{id}/phases       -> phase progress strip
- GET  /api/stories/{id}/files        -> work_dir top-level listing
- GET  /api/stories/{id}/files/{name} -> single text file content
- POST /api/stories/{id}/resume       -> orchestrator run_pipeline(resume_from=...)

The orchestrator call is wired through ``_invoke_resume_pipeline`` so
tests can stub it without spawning the multi-phase pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from review_queue import human_review as human_review_module
from review_queue.db import initialize_database, insert_story
from review_queue.human_review import app
from review_queue.models import Story

# ============================================================ helpers


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import anyio

    async def run() -> dict[str, Any]:
        sent: list[dict[str, object]] = []
        if body is None:
            payload = b""
        else:
            payload = json.dumps(body).encode("utf-8")
        messages = [{"type": "http.request", "body": payload, "more_body": False}]

        async def receive() -> dict[str, object]:
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(m: dict[str, object]) -> None:
            sent.append(m)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [
                (b"host", b"t"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
            "client": ("t", 1),
            "server": ("t", 80),
        }
        await app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        body_bytes = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )
        return {"status": status, "body": body_bytes.decode()}

    return anyio.run(run)


@pytest.fixture()
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "anp.sqlite3"
    cfg_path.write_text(
        f"""
deepseek:
  api_key: ""
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
  sqlite_path: "{str(db_path).replace('\\', '/')}"
logging:
  file: "{str(tmp_path / 'anp.log').replace('\\', '/')}"
cost_limits:
  monthly_budget_cny: 100
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_CONFIG", str(cfg_path))
    monkeypatch.setenv("ANP_SQLITE_PATH", str(db_path))
    return {"cfg": cfg_path, "db": db_path, "tmp": tmp_path}


def _seed_story(
    db_path: Path,
    *,
    work_dir: Path | None = None,
    current_phase: str = "phase_3_section_05_done",
) -> int:
    return insert_story(
        db_path,
        Story(
            title="待审核故事",
            status="pending",
            current_phase=current_phase,
            work_dir=str(work_dir) if work_dir else "(pending)",
            target_length=10000,
        ),
    )


# ============================================================ /phases


def test_phases_returns_404_for_missing_story(env: dict[str, Path]) -> None:
    r = _request("GET", "/api/stories/9999/phases")
    assert r["status"] == 404


def test_phases_returns_progress_strip(env: dict[str, Path]) -> None:
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    sid = _seed_story(env["db"], current_phase="phase_3_section_05_done")
    r = _request("GET", f"/api/stories/{sid}/phases")
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["story_id"] == sid
    assert body["current_phase"] == "phase_3_section_05_done"
    assert body["state"] == "running"
    assert body["section_index"] == 5
    assert len(body["steps"]) == 9  # 6 generation + zhuque + review + publish
    assert body["steps"][2]["status"] == "done"
    assert body["steps"][3]["status"] == "in_progress"


def test_phases_phase_7_done_reports_complete(env: dict[str, Path]) -> None:
    """全流程完成（审核+发布）才算 100%。"""
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    sid = _seed_story(env["db"], current_phase="phase_7_done")
    body = json.loads(_request("GET", f"/api/stories/{sid}/phases")["body"])
    assert body["state"] == "done"
    assert body["percent"] == 100.0


def test_phases_phase_5_done_advances_to_review(env: dict[str, Path]) -> None:
    """phase_5_done 不再是终态，应该在 phase_6（审核）位置 in_progress。"""
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    sid = _seed_story(env["db"], current_phase="phase_5_done")
    body = json.loads(_request("GET", f"/api/stories/{sid}/phases")["body"])
    assert body["state"] == "running"
    assert body["steps"][5]["status"] == "done"
    assert body["steps"][6]["status"] == "in_progress"


def test_phases_includes_timeline_after_transitions(env: dict[str, Path]) -> None:
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    sid = _seed_story(env["db"], current_phase="phase_0")
    from review_queue.db import update_story_phase

    update_story_phase(env["db"], sid, "phase_0_running")
    update_story_phase(env["db"], sid, "phase_0_done")
    update_story_phase(env["db"], sid, "phase_1_running")
    body = json.loads(_request("GET", f"/api/stories/{sid}/phases")["body"])
    timeline = body["timeline"]
    assert [e["phase"] for e in timeline] == ["phase_0", "phase_1"]
    assert timeline[0]["status"] == "done"
    assert timeline[0]["completed_at"] is not None
    assert timeline[1]["status"] == "in_progress"
    assert timeline[1]["completed_at"] is None


def test_phases_section_progress_reads_outline_total(
    env: dict[str, Path], tmp_path: Path
) -> None:
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    work_dir = tmp_path / "work_section"
    work_dir.mkdir()
    (work_dir / "2_小节大纲.md").write_text("- section_count: 8\n", encoding="utf-8")
    sid = _seed_story(env["db"], work_dir=work_dir, current_phase="phase_3_section_03_done")
    from review_queue.db import update_story_phase

    update_story_phase(env["db"], sid, "phase_3_running")
    update_story_phase(env["db"], sid, "phase_3_section_01_done")
    update_story_phase(env["db"], sid, "phase_3_section_03_done")
    body = json.loads(_request("GET", f"/api/stories/{sid}/phases")["body"])
    assert body["phase_3_section"] == {"current": 3, "total": 8, "completed": [1, 3]}


def test_phases_artifacts_payload_marks_existing_files(
    env: dict[str, Path], tmp_path: Path
) -> None:
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    work_dir = tmp_path / "work_artifacts"
    work_dir.mkdir()
    (work_dir / "0_选题.json").write_text("{}", encoding="utf-8")
    sid = _seed_story(env["db"], work_dir=work_dir, current_phase="phase_1_running")
    body = json.loads(_request("GET", f"/api/stories/{sid}/phases")["body"])
    artifacts = body["artifacts"]
    assert artifacts["phase_0"][0]["exists"] is True
    assert artifacts["phase_1"][0]["exists"] is False


def test_phases_payload_surfaces_retry_state_after_failure(env: dict[str, Path]) -> None:
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    sid = _seed_story(env["db"], current_phase="phase_3_running")
    from review_queue.db import update_story_phase

    # Attempt 1 — gets to phase_4 then fails.
    update_story_phase(env["db"], sid, "phase_0_running")
    update_story_phase(env["db"], sid, "phase_0_done")
    update_story_phase(env["db"], sid, "phase_4_running")
    update_story_phase(env["db"], sid, "failed_at_phase_4")
    # Orchestrator reset + retry.
    update_story_phase(env["db"], sid, "phase_0")
    update_story_phase(env["db"], sid, "phase_0_running")
    update_story_phase(env["db"], sid, "phase_3_running")
    body = json.loads(_request("GET", f"/api/stories/{sid}/phases")["body"])

    # Retry banner surfaces previous failure phase.
    assert body["retry"] == {"attempt": 2, "previous_failed_at": "phase_4"}
    # Two attempt buckets.
    assert len(body["attempts"]) == 2
    assert body["attempts"][0]["status"] == "failed"
    assert body["attempts"][0]["failed_at"] == "phase_4"
    assert body["attempts"][1]["status"] == "in_progress"
    # Chip strip still flags phase_4 as failed even though current_phase regressed.
    by_phase = {s["phase"]: s["status"] for s in body["steps"]}
    assert by_phase["phase_4"] == "failed"
    assert by_phase["phase_3"] == "in_progress"


# ============================================================ /files


def test_files_returns_404_when_work_dir_missing(env: dict[str, Path]) -> None:
    initialize_database(LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"]))
    sid = _seed_story(env["db"])  # work_dir=(pending)
    r = _request("GET", f"/api/stories/{sid}/files")
    assert r["status"] == 404


def test_files_returns_listing(env: dict[str, Path]) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    work_dir = env["tmp"] / "data" / "works" / "1"
    work_dir.mkdir(parents=True)
    (work_dir / "0_选题.json").write_text("{\"a\":1}", encoding="utf-8")
    (work_dir / "1_设定.md").write_text("# 框架", encoding="utf-8")
    sid = _seed_story(env["db"], work_dir=work_dir, current_phase="phase_5_done")

    body = json.loads(_request("GET", f"/api/stories/{sid}/files")["body"])
    assert body["ok"] is True
    assert body["story_id"] == sid
    names = [f["name"] for f in body["files"]]
    assert names == ["0_选题.json", "1_设定.md"]
    for entry in body["files"]:
        assert entry["size_bytes"] >= 0
        assert entry["modified_at"].endswith("Z")
        assert entry["is_text"] is True


# ============================================================ /files/{name}


def test_file_content_returns_text(env: dict[str, Path]) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    work_dir = env["tmp"] / "data" / "works" / "2"
    work_dir.mkdir(parents=True)
    (work_dir / "1_设定.md").write_text("# 框架\n\n正文段落。", encoding="utf-8")
    sid = _seed_story(env["db"], work_dir=work_dir, current_phase="phase_5_done")

    body = json.loads(
        _request("GET", f"/api/stories/{sid}/files/1_设定.md")["body"]
    )
    assert body["ok"] is True
    assert body["name"] == "1_设定.md"
    assert "# 框架" in body["content"]


def test_file_content_path_traversal_returns_400(env: dict[str, Path]) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    work_dir = env["tmp"] / "data" / "works" / "3"
    work_dir.mkdir(parents=True)
    (env["tmp"] / "secret.md").write_text("pwd", encoding="utf-8")
    sid = _seed_story(env["db"], work_dir=work_dir, current_phase="phase_5_done")

    r = _request("GET", f"/api/stories/{sid}/files/../../secret.md")
    # FastAPI normalizes some traversals at the path layer; either 400 or 404 is acceptable
    assert r["status"] in {400, 404}


def test_file_content_missing_file_returns_404(env: dict[str, Path]) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    work_dir = env["tmp"] / "data" / "works" / "4"
    work_dir.mkdir(parents=True)
    sid = _seed_story(env["db"], work_dir=work_dir, current_phase="phase_5_done")

    r = _request("GET", f"/api/stories/{sid}/files/missing.md")
    assert r["status"] == 404


# ============================================================ /resume


def test_resume_calls_orchestrator_with_normalized_phase(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    sid = _seed_story(env["db"], current_phase="failed_at_phase_3")

    captured: dict[str, Any] = {}

    def fake_run(*, story_id, config, resume_from):
        captured.update(story_id=story_id, resume_from=resume_from)
        return SimpleNamespace(
            story_id=story_id,
            final_phase="phase_5_done",
            status="pending",
            char_count=10500,
            duration_seconds=12.34,
        )

    monkeypatch.setattr(human_review_module, "_invoke_resume_pipeline", fake_run)

    r = _request("POST", f"/api/stories/{sid}/resume", body={"resume_from": "phase_3"})
    assert r["status"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["resume_from"] == "phase_3"
    assert captured["story_id"] == sid
    assert captured["resume_from"] == "phase_3"
    assert body["final_phase"] == "phase_5_done"
    assert body["char_count"] == 10500


def test_resume_normalizes_phase_done_form(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    sid = _seed_story(env["db"])

    captured: dict[str, Any] = {}

    def fake_run(*, story_id, config, resume_from):
        captured["resume_from"] = resume_from
        return SimpleNamespace(final_phase="phase_5_done")

    monkeypatch.setattr(human_review_module, "_invoke_resume_pipeline", fake_run)

    r = _request(
        "POST",
        f"/api/stories/{sid}/resume",
        body={"resume_from": "phase_2_done"},
    )
    assert r["status"] == 200
    assert captured["resume_from"] == "phase_3"


def test_resume_rejects_invalid_phase_with_400(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    sid = _seed_story(env["db"])
    monkeypatch.setattr(
        human_review_module,
        "_invoke_resume_pipeline",
        lambda **kw: pytest.fail("orchestrator must not run"),
    )

    r = _request("POST", f"/api/stories/{sid}/resume", body={"resume_from": "nope"})
    assert r["status"] == 400


def test_resume_returns_500_when_orchestrator_raises(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = LoadedConfig(data={"database": {"sqlite_path": str(env["db"])}}, path=env["cfg"])
    initialize_database(cfg)
    sid = _seed_story(env["db"])

    def boom(*, story_id, config, resume_from):
        raise RuntimeError("simulated phase 4 crash")

    monkeypatch.setattr(human_review_module, "_invoke_resume_pipeline", boom)

    r = _request("POST", f"/api/stories/{sid}/resume", body={"resume_from": "phase_4"})
    assert r["status"] == 500
    body = json.loads(r["body"])
    assert "simulated phase 4 crash" in body.get("detail", "")


def test_resume_returns_404_for_missing_story(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        human_review_module,
        "_invoke_resume_pipeline",
        lambda **kw: pytest.fail("orchestrator must not run"),
    )
    r = _request("POST", "/api/stories/9999/resume", body={"resume_from": "phase_3"})
    assert r["status"] == 404
