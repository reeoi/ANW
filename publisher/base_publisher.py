"""Base publisher abstractions for future Playwright adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublishResult:
    """Result returned by publisher adapters."""

    status: str
    message: str
    screenshot_path: str | None = None


class BasePublisher:
    """Base class for platform publishers."""

    platform_name = "base"

    def publish(self, title: str, content: str) -> PublishResult:
        """Publish content or simulate publishing in subclasses."""
        raise NotImplementedError
