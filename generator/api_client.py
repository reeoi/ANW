"""DeepSeek v4-pro / v4-flash chat client with prompt-cache + thinking-mode support.

Configuration is sourced from ``LoadedConfig.data['deepseek']``:

- ``model``               default ``deepseek-v4-pro``
- ``flash_model``         default ``deepseek-v4-flash`` (used by cost-driven downgrade)
- ``thinking_mode``       default ``True`` (pro reasoning mode on / off)
- ``prompt_cache_enabled``default ``True`` (1M context prompt cache)
- ``timeout_seconds``     default ``120`` (Phase 4 polish calls run long)
- ``max_retries``         default ``3``
- ``mock``                default ``False`` (forced True when api_key is empty
                          via config_loader._ensure_safe_runtime)

The legacy single-shot ``generate_story`` API is removed: c_pipeline composes
its own multi-turn prompts via ``chat_completion``. Mock mode returns a
deterministic structured response so unit tests run without network access.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from http.client import IncompleteRead, RemoteDisconnected
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config_loader import LoadedConfig

logger = logging.getLogger(__name__)


class DeepSeekClientError(RuntimeError):
    """Raised when a live DeepSeek request fails or returns invalid data."""


_RETRYABLE_ERRORS = (
    KeyError,
    IndexError,
    ValueError,
    HTTPError,
    URLError,
    TimeoutError,
    IncompleteRead,
    RemoteDisconnected,
    ConnectionError,
)


@dataclass(frozen=True)
class DeepSeekSettings:
    """Resolved DeepSeek runtime settings."""

    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    flash_model: str = "deepseek-v4-flash"
    thinking_mode: bool = True
    prompt_cache_enabled: bool = True
    timeout_seconds: int = 120
    max_retries: int = 3
    mock: bool = False


@dataclass(frozen=True)
class ChatUsage:
    """Token accounting for one chat call.

    ``cached_tokens`` is the number of input tokens served from prompt cache
    (``prompt_cache_hit_tokens`` in the DeepSeek response). ``input_tokens``
    is the total prompt tokens (cache hit + miss). ``output_tokens`` is the
    completion size including any reasoning trace.
    """

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def cache_hit_ratio(self) -> float:
        if self.input_tokens <= 0:
            return 0.0
        return round(min(1.0, self.cached_tokens / self.input_tokens), 4)


@dataclass(frozen=True)
class ChatCompletion:
    """Result of one ``chat_completion`` call."""

    text: str
    reasoning: str | None
    model: str
    usage: ChatUsage
    finish_reason: str | None = None
    cached: bool = False


class DeepSeekClient:
    """Small facade around DeepSeek chat completions tuned for c_pipeline.

    The client never reads credentials from disk directly: callers must build a
    ``LoadedConfig`` (which itself loads from config.yaml + environment via
    ``config_loader``). When mock mode is active (no API key, or
    ``deepseek.mock: true``), the client returns deterministic local text so
    unit tests and dry-run smoke checks work offline.
    """

    def __init__(self, config: LoadedConfig) -> None:
        self.config = config
        self.settings = self._resolve_settings(config)

    # ------------------------------------------------------------------ public

    def is_mock(self) -> bool:
        """Return whether calls should be mocked instead of sent to DeepSeek."""

        return self.config.is_dry_run or self.settings.mock or not self.settings.api_key

    def chat_completion(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        thinking_mode: bool | None = None,
        model: str | None = None,
        temperature: float = 0.8,
        response_format: Mapping[str, Any] | None = None,
        purpose: str = "chat",
    ) -> ChatCompletion:
        """Run one chat completion and return text + usage telemetry.

        Args:
            messages: OpenAI-style message list. Each entry must have ``role``
                and ``content`` keys.
            thinking_mode: Override ``deepseek.thinking_mode`` for this call.
                Phase 1 / 2 / 4 typically pass ``True``; Phase 0 / 3 / 5 pass
                ``False`` to keep cost predictable.
            model: Override the configured model — pass ``settings.flash_model``
                when ``cost_tracker`` flags a budget-driven downgrade.
            temperature: Sampling temperature.
            response_format: Optional ``{"type": "json_object"}`` style hint
                forwarded to DeepSeek for Phase 0/2 structured output.
            purpose: Free-form label recorded in cost telemetry.
        """

        chosen_model = model or self.settings.model
        if self.is_mock():
            return self._mock_completion(messages, chosen_model, thinking_mode)
        return self._live_completion(
            messages,
            model=chosen_model,
            thinking_mode=self._resolve_thinking_mode(thinking_mode),
            temperature=temperature,
            response_format=response_format,
            purpose=purpose,
        )

    # ---------------------------------------------------------------- internal

    def _resolve_thinking_mode(self, override: bool | None) -> bool:
        if override is not None:
            return bool(override)
        return bool(self.settings.thinking_mode)

    @staticmethod
    def _resolve_settings(config: LoadedConfig) -> DeepSeekSettings:
        deepseek = config.data.get("deepseek", {})
        api_key = str(deepseek.get("api_key") or "")
        return DeepSeekSettings(
            api_key=api_key,
            base_url=str(deepseek.get("base_url") or "https://api.deepseek.com").rstrip("/"),
            model=str(deepseek.get("model") or "deepseek-v4-pro"),
            flash_model=str(deepseek.get("flash_model") or "deepseek-v4-flash"),
            thinking_mode=bool(deepseek.get("thinking_mode", True)),
            prompt_cache_enabled=bool(deepseek.get("prompt_cache_enabled", True)),
            timeout_seconds=int(deepseek.get("timeout_seconds") or 120),
            max_retries=int(deepseek.get("max_retries") or 3),
            mock=bool(deepseek.get("mock") or not api_key),
        )

    def _live_completion(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str,
        thinking_mode: bool,
        temperature: float,
        response_format: Mapping[str, Any] | None,
        purpose: str,
    ) -> ChatCompletion:
        url = f"{self.settings.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": [dict(m) for m in messages],
            "temperature": temperature,
            "thinking_mode": thinking_mode,
        }
        if self.settings.prompt_cache_enabled:
            payload["prompt_cache_enabled"] = True
        if response_format is not None:
            payload["response_format"] = dict(response_format)

        last_error: Exception | None = None
        attempts = max(self.settings.max_retries, 1)
        for attempt in range(attempts):
            try:
                request = Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                completion = _parse_completion(response_data, model)
                self._record_usage(model, purpose, completion.usage, success=True)
                return completion
            except _RETRYABLE_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "DeepSeek chat_completion attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    attempts,
                    purpose,
                    exc,
                )
                if attempt < attempts - 1:
                    time.sleep(min(2.0, 0.25 * (2 ** attempt)))

        self._record_usage_failure(model, purpose, str(last_error))
        raise DeepSeekClientError(f"DeepSeek chat_completion failed: {last_error}")

    def _mock_completion(
        self,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        thinking_mode: bool | None,
    ) -> ChatCompletion:
        last_user = next(
            (m for m in reversed(list(messages)) if str(m.get("role")) == "user"),
            None,
        )
        prompt_text = str(last_user.get("content") if last_user else "")
        text = (
            "[mock] DeepSeek 客户端运行在 mock/dry-run 模式。"
            f" 模型={model} thinking_mode={'on' if (thinking_mode if thinking_mode is not None else self.settings.thinking_mode) else 'off'}。"
            f" 收到 {_count_chinese_chars(prompt_text)} 字提示。\n"
            "完整流水线产物会在 Phase B/C 实施后从真实模型获得。"
        )
        reasoning = (
            "[mock-reasoning] 思考模式已开启:在真实环境下,这里会包含 DeepSeek-V4-Pro 的链式推理痕迹。"
            if (thinking_mode if thinking_mode is not None else self.settings.thinking_mode)
            else None
        )
        usage = ChatUsage(
            input_tokens=max(1, len(prompt_text) // 4),
            cached_tokens=0,
            output_tokens=max(1, len(text) // 4),
            raw={"mock": True},
        )
        return ChatCompletion(text=text, reasoning=reasoning, model=model, usage=usage, finish_reason="stop", cached=False)

    def _record_usage(self, model: str, purpose: str, usage: ChatUsage, *, success: bool) -> None:
        try:
            from review_queue.db import get_database_path
            from review_queue.metrics import estimate_cost_cny, record_api_usage

            cost = estimate_cost_cny(usage.input_tokens, usage.output_tokens)
            record_api_usage(
                get_database_path(self.config),
                provider="deepseek",
                model=model,
                purpose=purpose,
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
                total_tokens=usage.input_tokens + usage.output_tokens,
                cost_cny=cost,
                success=success,
            )
        except Exception as exc:  # pragma: no cover - never break the pipeline
            logger.debug("token usage recording skipped: %s", exc)

    def _record_usage_failure(self, model: str, purpose: str, error: str) -> None:
        try:
            from review_queue.db import get_database_path
            from review_queue.metrics import record_api_usage

            record_api_usage(
                get_database_path(self.config),
                provider="deepseek",
                model=model,
                purpose=purpose,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cost_cny=0.0,
                success=False,
                error=error[:500],
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("usage failure recording skipped: %s", exc)


def _parse_completion(response_data: Mapping[str, Any], model: str) -> ChatCompletion:
    choices = response_data.get("choices") or []
    if not choices:
        raise DeepSeekClientError("DeepSeek response had no choices")
    first = choices[0] or {}
    message = first.get("message") or {}
    text = str(message.get("content") or "").strip()
    reasoning_raw = message.get("reasoning_content") or message.get("reasoning")
    reasoning = str(reasoning_raw).strip() if reasoning_raw else None
    finish_reason = first.get("finish_reason")
    usage = _parse_usage(response_data.get("usage") or {})
    cached = usage.cached_tokens > 0
    return ChatCompletion(
        text=text,
        reasoning=reasoning,
        model=str(response_data.get("model") or model),
        usage=usage,
        finish_reason=str(finish_reason) if finish_reason is not None else None,
        cached=cached,
    )


def _parse_usage(usage: Mapping[str, Any]) -> ChatUsage:
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)

    cached = (
        usage.get("prompt_cache_hit_tokens")
        or usage.get("cached_tokens")
        or _nested_int(usage, ("prompt_tokens_details", "cached_tokens"))
        or 0
    )
    return ChatUsage(
        input_tokens=prompt_tokens,
        cached_tokens=int(cached or 0),
        output_tokens=completion_tokens,
        raw=dict(usage),
    )


def _nested_int(usage: Mapping[str, Any], path: Iterable[str]) -> int:
    current: Any = usage
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return 0
        current = current[key]
    try:
        return int(current or 0)
    except (TypeError, ValueError):
        return 0


def _count_chinese_chars(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


__all__ = [
    "ChatCompletion",
    "ChatUsage",
    "DeepSeekClient",
    "DeepSeekClientError",
    "DeepSeekSettings",
]
