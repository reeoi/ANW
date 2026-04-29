"""APScheduler entrypoint placeholder."""

from __future__ import annotations

from config_loader import LoadedConfig


def scheduler_enabled(config: LoadedConfig) -> bool:
    """Return whether scheduled jobs should run."""
    return bool(config.data.get("scheduler", {}).get("enabled", False))
