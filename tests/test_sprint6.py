"""Tests for Sprint 6 scheduler, unified entrypoint, and dry-run pipeline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "queue" in sys.modules and not hasattr(sys.modules["queue"], "__path__"):
    del sys.modules["queue"]

from config_loader import LoadedConfig
from queue.db import initialize_database
from scheduler import (
    backup_sqlite_database,
    create_scheduler,
    get_monthly_api_limit,
    get_publish_delay_range,
    run_dry_run_pipeline,
    schedule_publish_with_random_delay,
)


def _config(tmp_path: Path) -> LoadedConfig:
    return LoadedConfig(
        data={
            "runtime": {"mode": "auto", "dry_run": True},
            "deepseek": {"mock": True, "api_key": ""},
            "database": {
                "sqlite_path": str(tmp_path / "anp.sqlite3"),
                "backup_dir": str(tmp_path / "backups"),
                "daily_backup": True,
            },
            "logging": {"file": str(tmp_path / "logs" / "anp.log"), "level": "INFO"},
            "audit": {"approval_threshold": 80, "max_rewrite_attempts": 3},
            "generation": {"theme": "雨夜归人", "word_count": 800, "style": "温情"},
            "publisher": {
                "fansq": {
                    "min_publish_interval_minutes": 5,
                    "max_publish_interval_minutes": 15,
                    "login_state_path": str(tmp_path / "state.json"),
                }
            },
            "scheduler": {
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "generate_cron": "0 9 * * *",
                "review_cron": "30 9 * * *",
                "publish_cron": "0 10 * * *",
                "backup_cron": "0 3 * * *",
            },
            "cost_limits": {"monthly_budget_cny": 100},
        },
        path=tmp_path / "config.yaml",
    )


def test_scheduler_defines_generate_review_publish_and_backup_jobs(tmp_path: Path) -> None:
    config = _config(tmp_path)

    scheduler = create_scheduler(config)

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert {"generate_story", "ai_review", "publish_window", "sqlite_backup"} <= job_ids


def test_random_publish_delay_and_cost_limit_are_configurable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scheduler = create_scheduler(config)

    delay_seconds = schedule_publish_with_random_delay(scheduler, config)

    assert 5 * 60 <= delay_seconds <= 15 * 60
    assert get_publish_delay_range(config) == (5, 15)
    assert get_monthly_api_limit(config) == 100


def test_backup_sqlite_database_creates_timestamped_copy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    initialize_database(config)

    backup_path = backup_sqlite_database(config)

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.parent == tmp_path / "backups"


def test_dry_run_pipeline_completes_without_external_credentials(tmp_path: Path) -> None:
    config = _config(tmp_path)

    result = run_dry_run_pipeline(config)

    assert result.generated_story_id is not None
    assert result.reviewed >= 1
    assert result.approved >= 1
    assert result.published is True
    assert (tmp_path / "logs" / "anp.log").exists()


def test_main_semi_auto_prints_review_service_instruction(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    db_path = tmp_path / "main.sqlite3"
    log_path = tmp_path / "logs" / "anp.log"
    config_path.write_text(
        f"""
runtime:
  dry_run: true
  mode: semi-auto
deepseek:
  api_key: ""
  mock: true
database:
  sqlite_path: "{db_path.as_posix()}"
  backup_dir: "{(tmp_path / 'backups').as_posix()}"
logging:
  file: "{log_path.as_posix()}"
publisher:
  fansq:
    min_publish_interval_minutes: 5
    max_publish_interval_minutes: 15
cost_limits:
  monthly_budget_cny: 100
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_CONFIG", str(config_path))

    completed = subprocess.run(
        [sys.executable, "main.py", "--mode", "semi-auto"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "python -m queue.human_review" in completed.stdout
    assert "Recent logs" in completed.stdout


def test_main_auto_dry_run_runs_local_e2e(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    db_path = tmp_path / "main_auto.sqlite3"
    log_path = tmp_path / "logs" / "anp.log"
    config_path.write_text(
        f"""
runtime:
  dry_run: true
  mode: auto
deepseek:
  api_key: ""
  mock: true
database:
  sqlite_path: "{db_path.as_posix()}"
  backup_dir: "{(tmp_path / 'backups').as_posix()}"
logging:
  file: "{log_path.as_posix()}"
audit:
  approval_threshold: 80
publisher:
  fansq:
    min_publish_interval_minutes: 5
    max_publish_interval_minutes: 15
cost_limits:
  monthly_budget_cny: 100
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANP_CONFIG", str(config_path))

    completed = subprocess.run(
        [sys.executable, "main.py", "--mode", "auto", "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "dry-run pipeline completed" in completed.stdout
    assert log_path.exists()
