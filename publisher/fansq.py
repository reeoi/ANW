"""Fanqie Novel publisher skeleton.

This adapter will only use normal browser automation in later sprints. It must
pause on captcha, slider, missing login state, or risk-control pages rather than
attempting bypasses.
"""

from __future__ import annotations

from publisher.base_publisher import BasePublisher, PublishResult


class FansqPublisher(BasePublisher):
    """Dry-run Fanqie publisher placeholder."""

    platform_name = "fansq"

    def publish(self, title: str, content: str) -> PublishResult:
        """Return a dry-run result until Playwright integration is implemented."""
        return PublishResult(
            status="dry_run",
            message=f"Dry-run publish prepared for {self.platform_name}: {title}",
        )
