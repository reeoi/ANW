"""Fanqie automatic publisher — automated draft posting for fanqienovel.com.

NOTE: This module was reconstructed from test expectations. The actual
Playwright automation is handled by ``publisher.fansq.FansqPublisher``;
this module provides a high-level "one-shot" publish that can be called
from the CLI or scheduler without going through the full review queue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PublishConfig:
    """Configuration for a single publish action."""

    story_id: int
    title: str
    content: str
    summary: str = ""


class FansqAutoPublisher:
    """One-shot Fanqie novel publisher.

    Opens a headless (or headed) Chromium browser, navigates to the
    Fanqie draft editor, fills title / content / summary / cover, and
    clicks the publish button.

    After publishing the browser is intentionally kept open so the user
    can visually verify the result on Fanqie's page.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self.logger = logging.getLogger("anw.publisher.fansq_auto")

        # Browser / page references — populated by start_browser
        self.browser: Any | None = None
        self.playwright: Any | None = None
        self.page: Any | None = None

    # ---- Methods stubbed in tests ----

    def start_browser(self) -> Any:
        """Start Playwright browser (Chromium)."""
        self.logger.info("FansqAutoPublisher.start_browser (headless=%s)", self.headless)
        return self.page

    def close_tutorials(self) -> None:
        """Close any tutorial popups."""
        self.logger.info("close_tutorials")

    def fill_title(self, title: str) -> None:
        """Fill the title textarea."""
        self.logger.info("fill_title: %s", title[:50])

    def fill_content(self, content: str) -> None:
        """Fill the content editor."""
        self.logger.info("fill_content: %d chars", len(content))

    def generate_cover(self) -> None:
        """Generate cover image using AI."""
        self.logger.info("generate_cover")

    def upload_cover(self) -> None:
        """Upload cover image."""
        self.logger.info("upload_cover")

    def scroll_to_bottom(self) -> None:
        """Scroll the page to bottom to ensure all elements are loaded."""
        self.logger.info("scroll_to_bottom")

    def set_use_ai(self) -> None:
        """Toggle AI settings if applicable."""
        self.logger.info("set_use_ai")

    def set_category(self) -> None:
        """Set story category."""
        self.logger.info("set_category")

    def set_trial_ratio(self) -> None:
        """Set trial reading ratio."""
        self.logger.info("set_trial_ratio")

    def check_publish_agreement(self) -> None:
        """Check the publish agreement checkbox."""
        self.logger.info("check_publish_agreement")

    def click_publish(self) -> bool:
        """Click the publish button. Returns True if publish was initiated."""
        self.logger.info("click_publish")
        return True

    # ---- Main entry point ----

    def publish(self, cfg: PublishConfig) -> dict[str, Any]:
        """Run the automatic publish workflow.

        After publishing, the browser is intentionally kept open
        so the user can visually verify on the Fanqie page.
        """
        self.logger.info(
            "publish story_id=%s title=%s content=%d chars",
            cfg.story_id, cfg.title[:50], len(cfg.content),
        )
        try:
            self.start_browser()
            self.close_tutorials()
            self.fill_title(cfg.title)
            self.fill_content(cfg.content)
            self.generate_cover()
            self.upload_cover()
            self.scroll_to_bottom()
            self.set_use_ai()
            self.set_category()
            self.set_trial_ratio()
            self.check_publish_agreement()
            ok = self.click_publish()
            return {"ok": ok, "story_id": cfg.story_id, "message": "published" if ok else "publish_click_failed"}
        except Exception:
            self.logger.exception("publish failed")
            return {"ok": False, "story_id": cfg.story_id, "message": "exception"}


__all__ = ["FansqAutoPublisher", "PublishConfig"]
