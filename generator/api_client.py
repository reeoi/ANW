"""DeepSeek API client with deterministic mock/dry-run support."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config_loader import LoadedConfig
from queue.models import Story


class DeepSeekClientError(RuntimeError):
    """Raised when a live DeepSeek request fails or returns invalid data."""


@dataclass(frozen=True)
class DeepSeekSettings:
    """Runtime settings for DeepSeek chat completions."""

    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout_seconds: int = 60
    max_retries: int = 3
    mock: bool = False


class DeepSeekClient:
    """Small facade around DeepSeek chat completions.

    The API key is accepted only through ``LoadedConfig`` (which itself loads from
    config.yaml/environment variables). When no key is available, config_loader
    enables dry-run/mock mode and this client returns deterministic local content.
    """

    def __init__(self, config: LoadedConfig) -> None:
        self.config = config
        deepseek = config.data.get("deepseek", {})
        self.settings = DeepSeekSettings(
            api_key=str(deepseek.get("api_key") or ""),
            base_url=str(deepseek.get("base_url") or "https://api.deepseek.com").rstrip("/"),
            model=str(deepseek.get("model") or "deepseek-chat"),
            timeout_seconds=int(deepseek.get("timeout_seconds") or 60),
            max_retries=int(deepseek.get("max_retries") or 3),
            mock=bool(deepseek.get("mock")),
        )

    def is_mock(self) -> bool:
        """Return whether calls should be mocked instead of sent to DeepSeek."""
        return self.config.is_dry_run or self.settings.mock or not self.settings.api_key

    def generate_story(self, prompt: str) -> Story:
        """Generate a story from a prepared prompt.

        Returns a ``Story`` object so callers can directly insert it into the
        SQLite queue. In mock/dry-run mode, output is fixed and reasonable enough
        to exercise the full local flow without external credentials.
        """
        if self.is_mock():
            return self._mock_story(prompt)

        content = self._call_deepseek(prompt)
        title, body = _split_title_and_content(content)
        return Story(title=title, content=body)

    def _call_deepseek(self, prompt: str) -> str:
        """Call DeepSeek's OpenAI-compatible chat completions endpoint."""
        url = f"{self.settings.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": "你是专业中文短篇小说作者。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.8,
        }

        last_error: Exception | None = None
        for _attempt in range(max(self.settings.max_retries, 1)):
            try:
                request = Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                return str(response_data["choices"][0]["message"]["content"]).strip()
            except (KeyError, IndexError, ValueError, HTTPError, URLError, TimeoutError) as exc:
                last_error = exc

        raise DeepSeekClientError(f"DeepSeek generation failed: {last_error}")

    def _mock_story(self, prompt: str) -> Story:
        theme = _extract_theme(prompt)
        style = _extract_style(prompt)
        title = f"《{theme}》"
        content = (
            f"{title}\n\n"
            f"雨停在凌晨两点，{theme}像一盏被风护住的小灯，照亮了老城最后一班公交。"
            "林舟攥着一封没有寄出的信，回到阔别十年的街口。街边的旧书店还亮着，"
            "老板娘把热茶推到他面前，像早就知道他会在这个夜里回来。\n\n"
            f"按照{style}的气息，故事里的每个人都没有大声解释自己的遗憾。林舟在书架间"
            "找到母亲当年夹在书里的车票，才明白那句'等你回家'并不是责备，而是给他留下"
            "重新开始的路标。窗外又响起细雨，他把信放进抽屉，帮老板娘关上漏风的窗。\n\n"
            "天亮时，第一束光落在门牌上。林舟没有立刻离开，他把旧书店门口的积水扫开，"
            "也把心里那条回家的路扫得清清楚楚。"
        )
        return Story(title=title, content=content)


def _extract_theme(prompt: str) -> str:
    match = re.search(r"主题《([^》]+)》", prompt)
    if match:
        return match.group(1).strip()
    return "雨夜归人"


def _extract_style(prompt: str) -> str:
    match = re.search(r"(?:整体风格|风格为|风格)：?([^。\n]+)", prompt)
    if match:
        return match.group(1).strip()
    return "现实温情"


def _split_title_and_content(raw_content: str) -> tuple[str, str]:
    lines = [line.strip() for line in raw_content.strip().splitlines() if line.strip()]
    if not lines:
        raise DeepSeekClientError("DeepSeek returned empty story content")

    first_line = lines[0]
    if first_line.startswith("《") and "》" in first_line:
        title = first_line[: first_line.index("》") + 1]
        body = "\n\n".join(lines[1:]).strip() or raw_content.strip()
        return title, body

    return "《未命名短篇》", raw_content.strip()


__all__ = ["DeepSeekClient", "DeepSeekClientError", "DeepSeekSettings"]
