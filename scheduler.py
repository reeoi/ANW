"""APScheduler-based pipeline orchestration for ANP."""

from __future__ import annotations

import logging
import random
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config_loader import LoadedConfig
from generator.api_client import DeepSeekClient
from generator.prompt_builder import DEFAULT_STYLE, build_short_story_prompt
from publisher.base_publisher import PublishStatus
from queue.ai_review import run_review_batch
from queue.db import initialize_database, insert_story
from queue.models import Story

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineRunResult:
    """Small summary returned by dry-run/self-check pipeline execution."""

    generated_story_id: int | None = None
    reviewed: int = 0
    approved: int = 0
    needs_human: int = 0
    published: bool = False
    message: str = ""


def configure_logging(config: LoadedConfig) -> Path:
    """Configure file + console logging for scheduled and CLI entrypoints."""

    logging_config = config.data.get("logging", {}) if isinstance(config.data.get("logging"), dict) else {}
    log_file = Path(str(logging_config.get("file") or "logs/anp.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level_name = str(logging_config.get("level") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_file.resolve() for handler in root_logger.handlers):
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

    if not any(getattr(handler, "_anp_console", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        console_handler._anp_console = True  # type: ignore[attr-defined]
        root_logger.addHandler(console_handler)

    logger.info("Logging initialized: file=%s level=%s", log_file, level_name)
    return log_file


def scheduler_enabled(config: LoadedConfig) -> bool:
    """Return whether scheduled jobs should run."""

    return bool(config.data.get("scheduler", {}).get("enabled", False))


def create_scheduler(config: LoadedConfig) -> BackgroundScheduler:
    """Create and populate the APScheduler instance from config.yaml."""

    scheduler_config = _mapping(config.data.get("scheduler"))
    timezone = str(scheduler_config.get("timezone") or "Asia/Shanghai")
    scheduler = BackgroundScheduler(timezone=timezone)

    if scheduler_config.get("generate_cron"):
        scheduler.add_job(
            scheduled_generate,
            _cron_trigger(str(scheduler_config["generate_cron"]), timezone),
            args=[config],
            id="generate_story",
            name="ANP generate story",
            replace_existing=True,
        )
    if scheduler_config.get("review_cron"):
        scheduler.add_job(
            scheduled_ai_review,
            _cron_trigger(str(scheduler_config["review_cron"]), timezone),
            args=[config],
            id="ai_review",
            name="ANP AI review",
            replace_existing=True,
        )
    if scheduler_config.get("publish_cron"):
        scheduler.add_job(
            schedule_publish_with_random_delay,
            _cron_trigger(str(scheduler_config["publish_cron"]), timezone),
            args=[scheduler, config],
            id="publish_window",
            name="ANP publish window with random delay",
            replace_existing=True,
        )

    backup_cron = str(scheduler_config.get("backup_cron") or "").strip()
    if not backup_cron and _mapping(config.data.get("database")).get("daily_backup", True):
        backup_cron = "0 3 * * *"
    if backup_cron:
        scheduler.add_job(
            backup_sqlite_database,
            _cron_trigger(backup_cron, timezone),
            args=[config],
            id="sqlite_backup",
            name="ANP SQLite backup",
            replace_existing=True,
        )

    return scheduler


def start_scheduler(config: LoadedConfig) -> BackgroundScheduler:
    """Start configured APScheduler jobs and return the running scheduler."""

    configure_logging(config)
    scheduler = create_scheduler(config)
    scheduler.start()
    logger.info("APScheduler started with jobs=%s", [job.id for job in scheduler.get_jobs()])
    return scheduler


def scheduled_generate(config: LoadedConfig) -> int:
    """Generate one story and enqueue it as pending."""

    logger.info("Pipeline stage started: generate")
    db_path = initialize_database(config)
    generation = _mapping(config.data.get("generation"))
    theme = str(generation.get("theme") or "雨夜归人")
    word_count = int(generation.get("word_count") or 3000)
    style = str(generation.get("style") or DEFAULT_STYLE)
    prompt = build_short_story_prompt(theme, word_count, style)
    story = DeepSeekClient(config).generate_story(prompt)
    story_id = insert_story(db_path, story)
    logger.info("Pipeline stage completed: generate story_id=%s db=%s", story_id, db_path)
    return story_id


def scheduled_ai_review(config: LoadedConfig) -> Any:
    """Run AI review over pending stories using configured thresholds."""

    logger.info("Pipeline stage started: ai_review")
    db_path = initialize_database(config)
    audit = _mapping(config.data.get("audit"))
    result = run_review_batch(
        db_path,
        threshold=int(audit.get("approval_threshold") or 80),
        limit=int(audit.get("batch_limit") or 20),
        config=config,
    )
    logger.info(
        "Pipeline stage completed: ai_review reviewed=%s approved=%s needs_human=%s failed=%s",
        result.reviewed,
        result.approved,
        result.needs_human,
        result.failed,
    )
    return result


def schedule_publish_with_random_delay(scheduler: BackgroundScheduler, config: LoadedConfig) -> int:
    """Schedule a publish job after the configured 5-15 minute randomized delay."""

    min_minutes, max_minutes = get_publish_delay_range(config)
    delay_seconds = random.randint(min_minutes * 60, max_minutes * 60)
    run_at = datetime.now() + timedelta(seconds=delay_seconds)
    scheduler.add_job(
        scheduled_publish,
        DateTrigger(run_date=run_at, timezone=scheduler.timezone),
        args=[config],
        id=f"publish_once_{int(run_at.timestamp())}",
        name="ANP randomized publish attempt",
        replace_existing=False,
    )
    logger.info(
        "Publish job scheduled with random delay: seconds=%s range_minutes=%s-%s run_at=%s",
        delay_seconds,
        min_minutes,
        max_minutes,
        run_at.isoformat(timespec="seconds"),
    )
    return delay_seconds


def scheduled_publish(config: LoadedConfig) -> bool:
    """Publish one approved story through the configured platform adapter."""

    from cli.publish import apply_publish_result, find_one_approved_story
    from publisher.fansq import FansqPublisher

    logger.info("Pipeline stage started: publish")
    db_path = initialize_database(config)
    story = find_one_approved_story(db_path)
    if story is None:
        logger.info("Pipeline stage completed: publish skipped=no_approved_story")
        return False

    dry_run = bool(_mapping(config.data.get("runtime")).get("dry_run"))
    result = FansqPublisher(config).publish_story(story, dry_run=dry_run, dry_run_outcome="success")
    changed = apply_publish_result(db_path, result, commit_dry_run=bool(_mapping(config.data.get("publisher")).get("commit_dry_run", False)))
    logger.info(
        "Pipeline stage completed: publish story_id=%s status=%s changed=%s message=%s",
        story.id,
        result.status,
        changed,
        result.message,
    )
    return result.status == PublishStatus.PUBLISHED


def backup_sqlite_database(config: LoadedConfig) -> Path | None:
    """Copy the configured SQLite database into the configured backup directory."""

    logger.info("Pipeline stage started: sqlite_backup")
    db_path = initialize_database(config)
    if not Path(db_path).exists():
        logger.warning("SQLite backup skipped because database does not exist: %s", db_path)
        return None
    database = _mapping(config.data.get("database"))
    backup_dir = Path(str(database.get("backup_dir") or "data/backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{Path(db_path).stem}_{timestamp}.sqlite3"
    shutil.copy2(db_path, target)
    logger.info("Pipeline stage completed: sqlite_backup source=%s target=%s", db_path, target)
    return target


def run_dry_run_pipeline(config: LoadedConfig) -> PipelineRunResult:
    """Run generate -> AI review -> publish locally once for end-to-end verification."""

    configure_logging(config)
    logger.info("Dry-run end-to-end pipeline started")
    story_id = scheduled_generate(config)
    review_result = scheduled_ai_review(config)
    published = scheduled_publish(config)
    message = (
        f"dry-run pipeline completed: generated_story_id={story_id} "
        f"reviewed={review_result.reviewed} approved={review_result.approved} published={published}"
    )
    logger.info(message)
    return PipelineRunResult(
        generated_story_id=story_id,
        reviewed=review_result.reviewed,
        approved=review_result.approved,
        needs_human=review_result.needs_human,
        published=published,
        message=message,
    )


def get_publish_delay_range(config: LoadedConfig) -> tuple[int, int]:
    """Return validated randomized publish delay range in minutes."""

    fansq = _mapping(_mapping(config.data.get("publisher")).get("fansq"))
    min_minutes = int(fansq.get("min_publish_interval_minutes") or 5)
    max_minutes = int(fansq.get("max_publish_interval_minutes") or 15)
    if min_minutes <= 0 or max_minutes <= 0:
        min_minutes, max_minutes = 5, 15
    if min_minutes > max_minutes:
        min_minutes, max_minutes = max_minutes, min_minutes
    return min_minutes, max_minutes


def get_monthly_api_limit(config: LoadedConfig) -> float | int | None:
    """Expose configured API monthly budget for risk-control checks/monitoring."""

    cost_limits = _mapping(config.data.get("cost_limits"))
    return cost_limits.get("monthly_budget_cny")


def recent_log_lines(config: LoadedConfig, max_lines: int = 80) -> tuple[Path, list[str]]:
    """Return the configured log file path and recent lines for the review page."""

    log_file = Path(str(_mapping(config.data.get("logging")).get("file") or "logs/anp.log"))
    if not log_file.exists():
        return log_file, []
    with log_file.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()[-max_lines:]
    return log_file, [line.rstrip("\n") for line in lines]


def count_stories_by_status(config: LoadedConfig) -> dict[str, int]:
    """Lightweight monitoring helper for local queue status counts."""

    db_path = initialize_database(config)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT status, COUNT(*) FROM stories GROUP BY status").fetchall()
    return {str(status): int(count) for status, count in rows}


def _cron_trigger(expression: str, timezone: str) -> CronTrigger | IntervalTrigger:
    expression = expression.strip()
    if expression.startswith("interval:"):
        minutes = int(expression.split(":", 1)[1].strip())
        return IntervalTrigger(minutes=minutes, timezone=timezone)
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"Cron expression must have 5 fields: {expression}")
    minute, hour, day, month, day_of_week = fields
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=timezone,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "PipelineRunResult",
    "backup_sqlite_database",
    "configure_logging",
    "count_stories_by_status",
    "create_scheduler",
    "get_monthly_api_limit",
    "get_publish_delay_range",
    "recent_log_lines",
    "run_dry_run_pipeline",
    "schedule_publish_with_random_delay",
    "scheduled_ai_review",
    "scheduled_generate",
    "scheduled_publish",
    "scheduler_enabled",
    "start_scheduler",
]
