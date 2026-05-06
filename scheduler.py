"""APScheduler-based pipeline orchestration for ANP (Phase D).

Cron / trigger map after Phase D (PLAN §3.1, §7 Phase D, decisions #17-#21):

- ``weekly_scan_cron``  Mon 03:00 -> ``scan.seed_evolver.run_weekly_scan``
- ``plan_today_cron``   daily 03:00 -> ``scheduler_planner.plan_today_publishes``
                                       then dynamically register a DateTrigger
                                       for every slot in today's
                                       ``daily_publish_plan``
- ``review_cron``       configured (default 09:30) -> AI review batch
- ``backup_cron``       daily 04:00 (or configured) -> SQLite copy
- per-slot DateTriggers fired at ``slot_time`` -> ``scheduled_slot_trigger``,
  which picks an approved story (FIFO + cross-day emotion balance), claims
  the slot, and schedules ``scheduled_publish`` 5-15 minutes later (decision
  #17 secondary jitter). Empty queue -> mark slot skipped (#18). Strict
  cap (#19) is enforced by only registering as many DateTriggers as
  ``daily_publish_plan`` contains.

Legacy paths removed (decision L1/L2): ``generate_cron``,
``scheduled_generate``, fixed ``publish_cron``.
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config_loader import LoadedConfig
from publisher.base_publisher import PublishStatus
from review_queue.ai_review import run_review_batch
from review_queue.db import get_daily_publish_plan, initialize_database
from review_queue.notification_bus import Severity, bus
from scan.seed_evolver import WeeklyScanBlockedError, WeeklyScanResult, run_weekly_scan
from scheduler_planner import (
    mark_slot_published,
    mark_slot_skipped,
    mark_slot_story,
    pick_story_for_slot,
    plan_today_publishes,
)

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


# ============================================================ logging / config


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


# ============================================================ create_scheduler


def create_scheduler(config: LoadedConfig) -> BackgroundScheduler:
    """Create and populate the APScheduler instance from config.yaml.

    Phase D registrations (decisions #17 / #21 / L1):
    - weekly_scan_cron  -> ``scheduled_weekly_scan``
    - plan_today_cron   -> ``scheduled_plan_today`` (also registers slot
                            DateTriggers for the freshly planned day)
    - review_cron       -> ``scheduled_ai_review``
    - backup_cron       -> ``backup_sqlite_database``
    Legacy ``generate_cron`` / ``publish_cron`` are silently ignored even
    if a config supplies them.
    """

    scheduler_config = _mapping(config.data.get("scheduler"))
    timezone = str(scheduler_config.get("timezone") or "Asia/Shanghai")
    scheduler = BackgroundScheduler(timezone=timezone)

    weekly_cron = str(scheduler_config.get("weekly_scan_cron") or "").strip()
    if weekly_cron:
        scheduler.add_job(
            scheduled_weekly_scan,
            _cron_trigger(weekly_cron, timezone),
            args=[config],
            id="weekly_scan",
            name="ANP weekly seed evolution",
            replace_existing=True,
        )

    plan_cron = str(scheduler_config.get("plan_today_cron") or "").strip()
    if plan_cron:
        scheduler.add_job(
            scheduled_plan_today,
            _cron_trigger(plan_cron, timezone),
            args=[scheduler, config],
            id="plan_today",
            name="ANP daily publish plan",
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

    backup_cron = str(scheduler_config.get("backup_cron") or "").strip()
    if not backup_cron and _mapping(config.data.get("database")).get("daily_backup", True):
        backup_cron = "0 4 * * *"
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
    """Start configured APScheduler jobs and return the running scheduler.

    After ``start()`` we re-register any unfired publish slots already
    persisted for *today* — covers the case where the daemon restarts
    between ``plan_today_cron`` and the day's last slot.
    """

    configure_logging(config)
    scheduler = create_scheduler(config)
    scheduler.start()
    try:
        registered = register_publish_slots(scheduler, config)
        if registered:
            logger.info(
                "register_publish_slots resumed %s pending slots for today", registered
            )
    except Exception as exc:  # pragma: no cover - defensive: do not block startup
        logger.warning("register_publish_slots on startup failed: %s", exc)
    logger.info("APScheduler started with jobs=%s", [job.id for job in scheduler.get_jobs()])
    return scheduler


# ============================================================ weekly scan


def scheduled_weekly_scan(config: LoadedConfig) -> WeeklyScanResult | None:
    """Cron callback for ``weekly_scan_cron`` (Mon 03:00)."""

    logger.info("Pipeline stage started: weekly_scan")
    try:
        result = run_weekly_scan(config)
    except WeeklyScanBlockedError as exc:
        logger.error("weekly_scan blocked: %s", exc)
        bus.publish(
            Severity.CRITICAL,
            "🚨 题材池演化被阻塞",
            f"weekly_scan 失败且无 fallback theme_pool: {exc}",
            source="scheduler.weekly_scan",
        )
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("weekly_scan crashed")
        bus.publish(
            Severity.WARNING,
            "⚠️ 题材池演化异常",
            f"weekly_scan 抛出异常: {exc}",
            source="scheduler.weekly_scan",
        )
        return None

    severity = Severity.WARNING if result.used_fallback else Severity.INFO
    title = "↩️ 题材池回退上周" if result.used_fallback else "✓ 题材池已演化"
    bus.publish(
        severity,
        title,
        f"iso_week={result.iso_week} items={result.item_count} "
        f"fallback={result.used_fallback}",
        source="scheduler.weekly_scan",
    )
    logger.info(
        "Pipeline stage completed: weekly_scan iso_week=%s item_count=%s fallback=%s",
        result.iso_week,
        result.item_count,
        result.used_fallback,
    )
    return result


# ============================================================ plan today


def scheduled_plan_today(
    scheduler: BackgroundScheduler,
    config: LoadedConfig,
    *,
    today: date | None = None,
) -> int:
    """Cron callback for ``plan_today_cron`` (daily 03:00).

    Samples today's slots, persists ``daily_publish_plan``, and dynamically
    registers a DateTrigger per slot. Returns the number of slot triggers
    registered (== ``planned_count``).
    """

    today = today or date.today()
    logger.info("Pipeline stage started: plan_today date=%s", today.isoformat())
    plan = plan_today_publishes(config, today=today)
    registered = register_publish_slots(scheduler, config, today=today)
    bus.publish(
        Severity.INFO,
        "✓ 当日发布计划已生成",
        f"date={plan.date} planned={plan.planned_count} registered={registered}",
        source="scheduler.plan_today",
    )
    logger.info(
        "Pipeline stage completed: plan_today date=%s planned=%s registered=%s",
        plan.date,
        plan.planned_count,
        registered,
    )
    return registered


def register_publish_slots(
    scheduler: BackgroundScheduler,
    config: LoadedConfig,
    *,
    today: date | None = None,
) -> int:
    """Register a DateTrigger for each unfired slot in today's plan."""

    today = today or date.today()
    today_str = today.isoformat()
    db_path = initialize_database(config)
    plan = get_daily_publish_plan(db_path, today_str)
    if plan is None:
        return 0
    try:
        slots = json.loads(plan.slots_json)
    except json.JSONDecodeError:
        return 0
    if not isinstance(slots, list):
        return 0

    timezone = getattr(scheduler, "timezone", None) or _scheduler_timezone(config)
    now = datetime.now()
    registered = 0
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict):
            continue
        # Skip slots that already fired (claimed/published/skipped) so a
        # restart never double-publishes.
        if slot.get("story_id") or slot.get("published_at") or slot.get("skipped_reason"):
            continue
        slot_time_raw = slot.get("slot_time")
        if not isinstance(slot_time_raw, str):
            continue
        try:
            slot_dt = datetime.fromisoformat(slot_time_raw)
        except ValueError:
            continue
        if slot_dt <= now:
            continue
        scheduler.add_job(
            scheduled_slot_trigger,
            DateTrigger(run_date=slot_dt, timezone=timezone),
            kwargs={"scheduler": scheduler, "config": config, "slot_index": index, "today": today_str},
            id=f"publish_slot_{today_str}_{index}",
            name=f"ANP publish slot {today_str} #{index}",
            replace_existing=True,
        )
        registered += 1
    return registered


# ============================================================ slot trigger


def scheduled_slot_trigger(
    scheduler: BackgroundScheduler | None,
    config: LoadedConfig,
    *,
    slot_index: int,
    today: str | None = None,
) -> bool:
    """At ``slot_time``, claim a story for the slot and schedule its publish.

    Decisions #18/#19/#20:
    - empty approved queue -> ``mark_slot_skipped(no_approved_story)`` and
      return False (no carryover).
    - non-empty -> ``pick_story_for_slot`` (FIFO + cross-day emotion
      balance), then ``mark_slot_story`` and schedule the actual publish
      via ``schedule_publish_with_random_delay`` (5-15 min secondary
      jitter, decision #17). Returns True.
    """

    today = today or date.today().isoformat()
    today_date = date.fromisoformat(today)
    db_path = initialize_database(config)
    logger.info(
        "Pipeline stage started: slot_trigger date=%s slot_index=%s",
        today,
        slot_index,
    )

    story = pick_story_for_slot(db_path, today=today_date, slot_index=slot_index)
    if story is None or story.id is None:
        mark_slot_skipped(
            db_path,
            today=today,
            slot_index=slot_index,
            reason="no_approved_story",
        )
        bus.publish(
            Severity.INFO,
            "⏭ 发布 slot 跳过",
            f"date={today} slot_index={slot_index} reason=no_approved_story",
            source="scheduler.slot_trigger",
        )
        logger.info(
            "Pipeline stage completed: slot_trigger date=%s slot_index=%s skipped=no_approved_story",
            today,
            slot_index,
        )
        return False

    mark_slot_story(
        db_path,
        today=today,
        slot_index=slot_index,
        story_id=int(story.id),
    )

    if scheduler is not None:
        schedule_publish_with_random_delay(
            scheduler,
            config,
            story=story,
            slot_index=slot_index,
            today=today,
        )

    logger.info(
        "Pipeline stage completed: slot_trigger date=%s slot_index=%s story_id=%s",
        today,
        slot_index,
        story.id,
    )
    return True


# ============================================================ ai review


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
    if result.failed > 0:
        bus.publish(
            Severity.WARNING,
            "⚠️ AI 审核部分失败",
            f"reviewed={result.reviewed} failed={result.failed}",
            source="scheduler.ai_review",
        )
    elif result.reviewed > 0 and result.needs_human == result.reviewed:
        bus.publish(
            Severity.WARNING,
            "📋 审核全部需人工",
            f"{result.reviewed} 篇全部需要人工复查",
            source="scheduler.ai_review",
        )
    elif result.reviewed > 0:
        bus.publish(
            Severity.INFO,
            "✓ AI 审核完成",
            f"reviewed={result.reviewed} approved={result.approved} needs_human={result.needs_human}",
            source="scheduler.ai_review",
        )
    return result


# ============================================================ publish (slot-driven)


def schedule_publish_with_random_delay(
    scheduler: BackgroundScheduler,
    config: LoadedConfig,
    *,
    story: Any | None = None,
    slot_index: int | None = None,
    today: str | None = None,
) -> int:
    """Schedule a publish job after the configured 5-15 minute randomized delay.

    Phase D: ``story``/``slot_index``/``today`` thread the slot identity
    through so ``scheduled_publish`` can mark ``slots_json[i].published_at``
    on success.
    """

    min_minutes, max_minutes = get_publish_delay_range(config)
    delay_seconds = random.randint(min_minutes * 60, max_minutes * 60)
    run_at = datetime.now() + timedelta(seconds=delay_seconds)
    scheduler.add_job(
        scheduled_publish,
        DateTrigger(run_date=run_at, timezone=scheduler.timezone),
        kwargs={
            "config": config,
            "story": story,
            "slot_index": slot_index,
            "today": today,
        },
        id=f"publish_once_{int(run_at.timestamp())}_{slot_index if slot_index is not None else 'na'}",
        name="ANP randomized publish attempt",
        replace_existing=False,
    )
    logger.info(
        "Publish job scheduled with random delay: seconds=%s range_minutes=%s-%s run_at=%s slot_index=%s",
        delay_seconds,
        min_minutes,
        max_minutes,
        run_at.isoformat(timespec="seconds"),
        slot_index,
    )
    return delay_seconds


def scheduled_publish(
    config: LoadedConfig,
    *,
    story: Any | None = None,
    slot_index: int | None = None,
    today: str | None = None,
) -> bool:
    """Publish one approved story through the configured platform adapter.

    When ``story`` is ``None`` we fall back to the FIFO picker (used by
    ``run_dry_run_pipeline`` and ``cli/publish``). When ``slot_index``+
    ``today`` are supplied (Phase D slot trigger), record the publish
    timestamp in ``slots_json``.
    """

    from cli.publish import apply_publish_result, find_one_approved_story
    from publisher.fansq import FansqPublisher

    logger.info(
        "Pipeline stage started: publish slot_index=%s today=%s",
        slot_index,
        today,
    )
    db_path = initialize_database(config)
    if story is None:
        story = find_one_approved_story(db_path)
    if story is None:
        logger.info("Pipeline stage completed: publish skipped=no_approved_story")
        if slot_index is not None and today is not None:
            mark_slot_skipped(
                db_path,
                today=today,
                slot_index=slot_index,
                reason="no_approved_story",
            )
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
    if str(result.status) == str(PublishStatus.PAUSED):
        bus.publish(
            Severity.CRITICAL,
            "🚨 番茄发布暂停",
            f"作品 #{story.id} 触发暂停：{result.message}",
            source="scheduler.publish",
            story_id=story.id,
        )
    elif str(result.status) == str(PublishStatus.PUBLISHED):
        if slot_index is not None and today is not None:
            mark_slot_published(
                db_path,
                today=today,
                slot_index=slot_index,
                published_at=datetime.now().replace(microsecond=0).isoformat(),
            )
        bus.publish(
            Severity.INFO,
            "✓ 发布成功",
            f"作品 #{story.id} 已发布到番茄",
            source="scheduler.publish",
            story_id=story.id,
        )
    elif str(result.status) == str(PublishStatus.FAILED):
        if slot_index is not None and today is not None:
            mark_slot_skipped(
                db_path,
                today=today,
                slot_index=slot_index,
                reason=f"publish_failed:{result.message}",
            )
        bus.publish(
            Severity.WARNING,
            "⚠️ 发布失败",
            f"作品 #{story.id}：{result.message}",
            source="scheduler.publish",
            story_id=story.id,
        )
    return result.status == PublishStatus.PUBLISHED


# ============================================================ backup


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
    bus.publish(
        Severity.INFO,
        "✓ 备份完成",
        f"目标：{target.name}",
        source="scheduler.backup",
    )
    return target


# ============================================================ dry-run helper


def run_dry_run_pipeline(config: LoadedConfig) -> PipelineRunResult:
    """Run the available pipeline stages locally once for end-to-end verification.

    Phase D: the scheduler no longer owns the generate step (Phase E will
    reconnect c_pipeline.orchestrator into ``cli/generate``); the dry-run
    pipeline therefore only exercises the AI review + publish path against
    whatever stories already sit in the queue.
    """

    configure_logging(config)
    logger.info("Dry-run end-to-end pipeline started")
    review_result = scheduled_ai_review(config)
    published = scheduled_publish(config)
    message = (
        "dry-run pipeline completed: generate=skipped(c_pipeline owns it) "
        f"reviewed={review_result.reviewed} approved={review_result.approved} published={published}"
    )
    logger.info(message)
    return PipelineRunResult(
        generated_story_id=None,
        reviewed=review_result.reviewed,
        approved=review_result.approved,
        needs_human=review_result.needs_human,
        published=published,
        message=message,
    )


# ============================================================ small utilities


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


def _scheduler_timezone(config: LoadedConfig) -> str:
    return str(_mapping(config.data.get("scheduler")).get("timezone") or "Asia/Shanghai")


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
    "register_publish_slots",
    "run_dry_run_pipeline",
    "schedule_publish_with_random_delay",
    "scheduled_ai_review",
    "scheduled_plan_today",
    "scheduled_publish",
    "scheduled_slot_trigger",
    "scheduled_weekly_scan",
    "scheduler_enabled",
    "start_scheduler",
]
