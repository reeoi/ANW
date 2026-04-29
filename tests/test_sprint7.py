"""Tests for Sprint 7 batch generation CLI."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "queue" in sys.modules and not hasattr(sys.modules["queue"], "__path__"):
    del sys.modules["queue"]


def test_batch_generate_dry_run_enqueues_requested_count(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite3"
    command = [
        sys.executable,
        "-m",
        "cli.batch_generate",
        "--count",
        "3",
        "--theme",
        "批量雨夜",
        "--word-count",
        "600",
        "--dry-run",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={"ANP_SQLITE_PATH": str(db_path), "ANP_DRY_RUN": "1", "ANP_MOCK_DEEPSEEK": "1"},
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "requested=3" in result.stdout
    assert "success=3" in result.stdout
    assert "failed=0" in result.stdout
    assert "dry_run=True" in result.stdout

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT title, status FROM stories ORDER BY id").fetchall()

    assert len(rows) == 3
    assert all(status == "pending" for _title, status in rows)
    assert all("批量雨夜" in title for title, _status in rows)


def test_batch_generate_rejects_non_positive_count() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cli.batch_generate", "--count", "0"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "--count must be positive" in result.stderr
