"""DeepSeek client placeholder with dry-run support for Sprint 1."""

from __future__ import annotations

from config_loader import LoadedConfig


class DeepSeekClient:
    """Minimal client facade prepared for later live DeepSeek integration."""

    def __init__(self, config: LoadedConfig) -> None:
        self.config = config

    def is_mock(self) -> bool:
        """Return whether calls should be mocked instead of sent to DeepSeek."""
        deepseek = self.config.data.get("deepseek", {})
        return self.config.is_dry_run or bool(deepseek.get("mock"))

    def generate_story(self, prompt: str) -> str:
        """Return a mock story in dry-run mode.

        Live API calls are intentionally left for the generation sprint.
        """
        if self.is_mock():
            return f"【Mock短篇】基于提示词生成：{prompt[:120]}"
        raise RuntimeError("Live DeepSeek generation is not implemented in Sprint 1.")
