"""Fanqie Novel publisher adapter with compliant safe-pause behavior.

The adapter only performs normal browser automation. It never attempts to bypass,
solve, or crack captchas, sliders, login checks, or risk-control pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config_loader import LoadedConfig
from publisher.base_publisher import BasePublisher, PublishResult, PublishStatus
from queue.models import Story


RISK_KEYWORDS = (
    "验证码",
    "captcha",
    "滑块",
    "拖动滑块",
    "安全验证",
    "风险",
    "风控",
    "异常访问",
    "登录",
    "请先登录",
)


@dataclass(frozen=True)
class FansqSettings:
    """Configuration for the Fanqie publisher adapter."""

    enabled: bool
    username: str
    login_state_path: Path | None
    draft_url: str
    pause_on_risk_control: bool = True


class FansqPublisher(BasePublisher):
    """Fanqie Novel publishing adapter.

    Account data and platform entry points come from ``config.yaml`` or environment
    overrides handled by ``config_loader``. Passwords are not needed by the adapter:
    the user prepares a Playwright storage-state file through manual login.
    """

    platform_name = "fansq"

    def __init__(self, config: LoadedConfig | None = None, headless: bool | None = None) -> None:
        super().__init__(config=config, platform_name=self.platform_name, headless=headless)
        self.settings = self.load_settings()

    def load_settings(self) -> FansqSettings:
        """Load Fanqie settings without hardcoding account credentials."""

        raw: dict[str, Any] = self.config.data.get("publisher", {}).get("fansq", {})
        login_state = raw.get("login_state_path")
        return FansqSettings(
            enabled=bool(raw.get("enabled", True)),
            username=str(raw.get("username") or ""),
            login_state_path=Path(str(login_state)) if login_state else None,
            draft_url=str(raw.get("draft_url") or "https://fanqienovel.com/"),
            pause_on_risk_control=bool(raw.get("pause_on_risk_control", True)),
        )

    def publish(self, title: str, content: str) -> PublishResult:
        """Backward-compatible title/content publish entry point."""

        return self.publish_story(Story(title=title, content=content, status="approved"))

    def publish_story(
        self,
        story: Story,
        dry_run: bool | None = None,
        dry_run_outcome: str = "success",
        wait_on_pause: bool = False,
    ) -> PublishResult:
        """Publish one approved story or return a safe pause result.

        Args:
            story: Queue story. Only ``status='approved'`` records are eligible.
            dry_run: Force simulation. Defaults to runtime dry-run setting.
            dry_run_outcome: In dry-run, choose ``success`` or ``paused`` to verify
                both status paths without opening a browser.
            wait_on_pause: If true, block for manual intervention after pausing.
        """

        if story.status != "approved":
            return self.result(
                PublishStatus.FAILED,
                f"仅允许发布 approved 作品，当前状态为 {story.status}",
                story_id=story.id,
            )

        effective_dry_run = self.dry_run_enabled if dry_run is None else dry_run
        if effective_dry_run:
            return self._dry_run_publish(story, dry_run_outcome)

        if not self.settings.enabled:
            return self.result(PublishStatus.FAILED, "番茄小说发布器未启用", story_id=story.id)

        if not self.settings.login_state_path or not self.settings.login_state_path.exists():
            return self.pause_for_human(
                "番茄登录态缺失：请先人工登录并保存 Playwright storage_state。",
                story_id=story.id,
                wait=wait_on_pause,
            )

        try:
            page = self.start_browser()
            page.goto(self.settings.draft_url, wait_until="domcontentloaded", timeout=60_000)
            risk_reason = self.detect_risk_control(page)
            if risk_reason:
                return self.pause_for_human(risk_reason, story_id=story.id, page=page, wait=wait_on_pause)

            # Conservative selectors: if the platform markup changes, pause for human
            # rather than guessing or clicking through unknown risk pages.
            title_box = self._first_visible(page, [
                "input[placeholder*='标题']",
                "textarea[placeholder*='标题']",
                "[contenteditable='true'][data-placeholder*='标题']",
            ])
            content_box = self._first_visible(page, [
                "textarea[placeholder*='正文']",
                "textarea[placeholder*='内容']",
                "[contenteditable='true']",
            ])
            if title_box is None or content_box is None:
                return self.pause_for_human(
                    "未识别到番茄发布编辑器，可能未登录、页面改版或进入风控页。",
                    story_id=story.id,
                    page=page,
                    wait=wait_on_pause,
                )

            title_box.fill(story.title)
            content_box.fill(story.content)
            risk_reason = self.detect_risk_control(page)
            if risk_reason:
                return self.pause_for_human(risk_reason, story_id=story.id, page=page, wait=wait_on_pause)

            # MVP stops at draft preparation to avoid accidental public posting when
            # button labels/flows are uncertain. Human can verify and submit.
            return self.pause_for_human(
                "作品已填入发布页草稿区域，请人工复核页面、章节设置和最终提交。",
                story_id=story.id,
                page=page,
                wait=wait_on_pause,
            )
        except Exception as exc:
            return self.pause_for_human(
                f"番茄发布过程中出现异常：{exc.__class__.__name__}: {exc}",
                story_id=story.id,
                wait=wait_on_pause,
            )
        finally:
            self.close()

    def detect_risk_control(self, page: Any) -> str | None:
        """Detect login/captcha/slider/risk-control signals and describe them."""

        try:
            url = str(getattr(page, "url", ""))
            text = page.locator("body").inner_text(timeout=3_000)
        except Exception:
            url = str(getattr(page, "url", ""))
            text = ""
        haystack = f"{url}\n{text}".lower()
        for keyword in RISK_KEYWORDS:
            if keyword.lower() in haystack:
                return f"检测到登录/验证码/滑块/风控信号：{keyword}"
        return None

    def _dry_run_publish(self, story: Story, outcome: str) -> PublishResult:
        normalized = outcome.strip().lower()
        if normalized in {"pause", "paused", "risk", "captcha", "slider", "login_missing"}:
            return self.pause_for_human(
                "dry-run 模拟：检测到验证码、滑块、登录态缺失或风控页面。",
                story_id=story.id,
                wait=False,
                dry_run=True,
            )
        return self.result(
            PublishStatus.PUBLISHED,
            f"dry-run 模拟发布成功：{story.title}（未打开浏览器，未提交到番茄）",
            story_id=story.id,
            dry_run=True,
        )

    @staticmethod
    def _first_visible(page: Any, selectors: list[str]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.is_visible(timeout=2_000):
                    return locator
            except Exception:
                continue
        return None


__all__ = ["FansqPublisher", "FansqSettings", "RISK_KEYWORDS"]
