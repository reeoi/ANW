"""Shared Playwright publisher foundation with safe human-pause primitives."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from config_loader import LoadedConfig, load_from_environment

try:  # Playwright is optional for dry-run/test paths.
    from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
except Exception:  # pragma: no cover - exercised only when dependency is absent.
    Browser = BrowserContext = Page = Any  # type: ignore[misc, assignment]
    sync_playwright = None  # type: ignore[assignment]


class PublishStatus(StrEnum):
    """Normalized publishing outcomes stored by CLI and adapters."""

    PUBLISHED = "published"
    PAUSED = "publish_paused"
    FAILED = "publish_failed"
    DRY_RUN = "dry_run"


@dataclass(frozen=True)
class PublishResult:
    """Result returned by publisher adapters."""

    status: str
    message: str
    story_id: int | None = None
    platform: str | None = None
    screenshot_path: str | None = None
    published_url: str | None = None
    dry_run: bool = False
    should_update_status: bool = True


class BasePublisher:
    """Base class for Playwright-backed platform publishers.

    The class centralizes browser/context startup, screenshot capture, file logging,
    safe pause semantics, and result creation. Subclasses should call
    :meth:`pause_for_human` whenever login state, captcha, slider, or risk-control
    conditions are detected; this project must not attempt to bypass those checks.
    """

    platform_name = "base"

    def __init__(
        self,
        config: LoadedConfig | None = None,
        platform_name: str | None = None,
        headless: bool | None = None,
    ) -> None:
        self.config = config or load_from_environment()
        if platform_name is not None:
            self.platform_name = platform_name
        runtime = self.config.data.get("runtime", {})
        self.headless = bool(runtime.get("headless", False) if headless is None else headless)
        self.playwright: Any | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.logger = self._build_logger()

    @property
    def dry_run_enabled(self) -> bool:
        """Return whether the effective runtime is dry-run."""

        return bool(self.config.data.get("runtime", {}).get("dry_run"))

    def publish(self, title: str, content: str) -> PublishResult:
        """Publish content or simulate publishing in subclasses."""

        raise NotImplementedError

    def start_browser(self) -> Page:
        """Start Chromium and return a page, loading storage state when configured."""

        if sync_playwright is None:
            return self.pause_for_human(
                "Playwright is not installed; run `pip install -r requirements.txt` and "
                "`python -m playwright install`. Cannot continue live publishing.",
                wait=False,
            )  # type: ignore[return-value]

        if self.page is not None:
            return self.page

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.load_context(self.browser)
        self.page = self.context.new_page()
        self.logger.info("browser_started platform=%s headless=%s", self.platform_name, self.headless)
        return self.page

    def load_context(self, browser: Browser) -> BrowserContext:
        """Create a BrowserContext using a configured storage-state file if present."""

        state_path = self.get_login_state_path()
        if state_path and state_path.exists():
            self.logger.info("loading_browser_context platform=%s storage_state=%s", self.platform_name, state_path)
            return browser.new_context(storage_state=str(state_path))
        self.logger.warning("browser_context_without_storage_state platform=%s", self.platform_name)
        return browser.new_context()

    def close(self) -> None:
        """Close Playwright resources best-effort."""

        for resource in (self.context, self.browser):
            try:
                if resource is not None:
                    resource.close()
            except Exception as exc:  # pragma: no cover - cleanup best effort.
                self.logger.debug("publisher_cleanup_error platform=%s error=%s", self.platform_name, exc)
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception as exc:  # pragma: no cover
                self.logger.debug("playwright_stop_error platform=%s error=%s", self.platform_name, exc)
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    def screenshot(self, reason: str, story_id: int | None = None, page: Page | None = None) -> str:
        """Save a screenshot or placeholder evidence file and return its path."""

        screenshot_dir = self.get_screenshot_dir()
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        safe_reason = "".join(ch if ch.isalnum() else "_" for ch in reason.lower()).strip("_")[:60]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        story_part = f"story_{story_id}_" if story_id is not None else ""
        path = screenshot_dir / f"{self.platform_name}_{story_part}{safe_reason or 'pause'}_{timestamp}.png"
        active_page = page or self.page
        if active_page is not None:
            active_page.screenshot(path=str(path), full_page=True)
        else:
            # Keep a deterministic artifact even before a page exists (e.g. missing login state).
            path.write_bytes(
                b"ANW safe pause evidence placeholder. No browser page was available for screenshot.\n"
            )
        self.logger.info("screenshot_saved platform=%s story_id=%s path=%s", self.platform_name, story_id, path)
        return str(path)

    def pause_for_human(
        self,
        reason: str,
        story_id: int | None = None,
        page: Page | None = None,
        wait: bool | None = None,
        dry_run: bool = False,
    ) -> PublishResult:
        """Pause publishing, capture evidence, and optionally wait for manual handling.

        This method intentionally does not solve captcha/slider/risk controls. The
        operator must resolve the browser state manually and re-run publishing.
        """

        screenshot_path = self.screenshot(reason, story_id=story_id, page=page)
        message = f"发布已安全暂停：{reason}；已截图并记录日志。不尝试绕过或自动破解风控。"
        self.logger.warning(
            "publish_paused platform=%s story_id=%s reason=%s screenshot=%s action=manual_required 不尝试绕过",
            self.platform_name,
            story_id,
            reason,
            screenshot_path,
        )
        if wait:
            input("发布已暂停，请人工处理登录/验证码/滑块后按 Enter 继续，或 Ctrl+C 退出...")
        return self.result(
            PublishStatus.PAUSED,
            message,
            story_id=story_id,
            screenshot_path=screenshot_path,
            dry_run=dry_run,
        )

    def result(
        self,
        status: str,
        message: str,
        story_id: int | None = None,
        screenshot_path: str | None = None,
        published_url: str | None = None,
        dry_run: bool = False,
        should_update_status: bool = True,
    ) -> PublishResult:
        """Build a normalized publishing result."""

        return PublishResult(
            status=str(status),
            message=message,
            story_id=story_id,
            platform=self.platform_name,
            screenshot_path=screenshot_path,
            published_url=published_url,
            dry_run=dry_run,
            should_update_status=should_update_status,
        )

    def get_login_state_path(self) -> Path | None:
        """Return configured platform login storage-state path, if any."""

        platform = self.config.data.get("publisher", {}).get(self.platform_name, {})
        configured = platform.get("login_state_path")
        if not configured:
            return None
        return Path(str(configured))

    def get_screenshot_dir(self) -> Path:
        """Return configured screenshot directory."""

        return Path(str(self.config.data.get("logging", {}).get("screenshot_dir", "logs/screenshots")))

    def notify(self, message: str) -> None:
        """Notification hook; currently logs locally and can be extended later."""

        self.logger.warning("publisher_notification platform=%s message=%s", self.platform_name, message)

    def _build_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"anw.publisher.{self.platform_name}")
        logger.setLevel(getattr(logging, str(self.config.data.get("logging", {}).get("level", "INFO")).upper(), logging.INFO))
        log_path = Path(str(self.config.data.get("logging", {}).get("file", "logs/anw.log")))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path.resolve() for handler in logger.handlers):
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            logger.addHandler(handler)
        logger.propagate = False
        return logger

    def sleep_before_publish(self, seconds: float) -> None:
        """Small wrapper to simplify testing future interval behavior."""

        time.sleep(seconds)


__all__ = ["BasePublisher", "PublishResult", "PublishStatus"]
