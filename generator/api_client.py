"""Chat-completions client with DeepSeek defaults and multi-provider support.

Configuration is sourced from ``LoadedConfig.data['deepseek']``:

- ``provider``            default ``deepseek``; supports openai/google/anthropic/zhipu/qwen/custom
- ``protocol``            default ``openai``; custom relays may use ``anthropic``
- ``api_key``             provider API key
- ``base_url``            provider API base URL
- ``model``               default ``deepseek-v4-pro``
- ``flash_model``         default ``deepseek-v4-flash`` (used by cost-driven downgrade)
- ``thinking_mode``       default ``True`` (pro reasoning mode on / off)
- ``prompt_cache_enabled``default ``True`` (1M context prompt cache)
- ``max_output_tokens``   default ``16384`` (required by Anthropic Messages)
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
import re
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
    """Resolved LLM runtime settings."""

    api_key: str
    provider: str = "deepseek"
    protocol: str = "openai"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    flash_model: str = "deepseek-v4-flash"
    thinking_mode: bool = True
    prompt_cache_enabled: bool = True
    max_output_tokens: int = 16384
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
    """Small facade around chat completions tuned for c_pipeline.

    The client never reads credentials from disk directly: callers must build a
    ``LoadedConfig`` (which itself loads from config.yaml + environment via
    ``config_loader``). When mock mode is active (no API key, or
    ``deepseek.mock: true``), the client returns deterministic local text so
    unit tests and dry-run smoke checks work offline.
    """

    def __init__(self, config: LoadedConfig) -> None:
        self.config = config
        self.settings = self._resolve_settings(config)
        self._usage_context: dict[str, Any] = {}

    # ------------------------------------------------------------------ public

    def is_mock(self) -> bool:
        """Return whether calls should be mocked instead of sent to DeepSeek."""

        return self.config.is_dry_run or self.settings.mock or not self.settings.api_key

    def set_usage_context(
        self,
        *,
        work_type: str,
        work_id: int,
        work_title: str,
    ) -> "DeepSeekClient":
        """Attach a stable work snapshot to later token-usage records."""
        self._usage_context = {
            "work_type": str(work_type),
            "work_id": int(work_id),
            "work_title": str(work_title),
        }
        return self

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
        provider = _normalize_provider(str(deepseek.get("provider") or "deepseek"))
        defaults = provider_defaults(provider)
        protocol = _normalize_protocol(str(deepseek.get("protocol") or defaults["protocol"]))
        model = str(deepseek.get("model") or defaults["model"])
        flash_model = str(deepseek.get("flash_model") or (defaults.get("flash_model") or model))
        return DeepSeekSettings(
            api_key=api_key,
            provider=provider,
            protocol=protocol,
            base_url=str(deepseek.get("base_url") or defaults["base_url"]).rstrip("/"),
            model=model,
            flash_model=flash_model,
            thinking_mode=bool(deepseek.get("thinking_mode", True)),
            prompt_cache_enabled=bool(deepseek.get("prompt_cache_enabled", True)),
            max_output_tokens=int(deepseek.get("max_output_tokens") or 16384),
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
        url, headers, payload = self._build_live_request(
            messages,
            model=model,
            thinking_mode=thinking_mode,
            temperature=temperature,
            response_format=response_format,
        )

        last_error: Exception | None = None
        overall_started = time.monotonic()
        attempts = max(self.settings.max_retries, 1)
        for attempt in range(attempts):
            try:
                attempt_started = time.monotonic()
                request = Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    completion, first_sentence_seconds = _read_streaming_completion(
                        response,
                        model=model,
                        protocol=self.settings.protocol,
                        started_at=attempt_started,
                    )
                duration_seconds = time.monotonic() - attempt_started
                self._record_usage(
                    model,
                    purpose,
                    completion.usage,
                    success=True,
                    duration_seconds=duration_seconds,
                    first_sentence_seconds=first_sentence_seconds,
                )
                return completion
            except _RETRYABLE_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "LLM chat_completion attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    attempts,
                    purpose,
                    exc,
                )
                if attempt < attempts - 1:
                    time.sleep(min(2.0, 0.25 * (2 ** attempt)))

        self._record_usage_failure(
            model,
            purpose,
            str(last_error),
            duration_seconds=time.monotonic() - overall_started,
        )
        raise DeepSeekClientError(f"LLM chat_completion failed: {last_error}")

    def _build_live_request(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str,
        thinking_mode: bool,
        temperature: float,
        response_format: Mapping[str, Any] | None,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        provider = self.settings.provider
        if self.settings.protocol == "anthropic":
            system_parts: list[str] = []
            anth_messages: list[dict[str, str]] = []
            for message in messages:
                role = str(message.get("role") or "user")
                content = str(message.get("content") or "")
                if role == "system":
                    system_parts.append(content)
                elif role in {"assistant", "user"}:
                    anth_messages.append({"role": role, "content": content})
                else:
                    anth_messages.append({"role": "user", "content": content})
            if not anth_messages:
                anth_messages.append({"role": "user", "content": ""})
            payload: dict[str, Any] = {
                "model": model,
                "messages": anth_messages,
                "max_tokens": self.settings.max_output_tokens,
                "temperature": temperature,
                "stream": True,
            }
            if system_parts:
                payload["system"] = "\n\n".join(system_parts)
            return (
                _join_endpoint(self.settings.base_url, "messages"),
                {
                    "x-api-key": self.settings.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                payload,
            )

        payload = {
            "model": model,
            "messages": [dict(m) for m in messages],
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if provider == "deepseek" and thinking_mode:
            payload["thinking"] = {"type": "enabled"}
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        return (
            _join_endpoint(self.settings.base_url, "chat/completions"),
            {
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )

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

    def _record_usage(
        self,
        model: str,
        purpose: str,
        usage: ChatUsage,
        *,
        success: bool,
        duration_seconds: float | None = None,
        first_sentence_seconds: float | None = None,
    ) -> None:
        try:
            from review_queue.db import get_database_path
            from review_queue.metrics import estimate_cost_cny, record_api_usage

            cost = estimate_cost_cny(usage.input_tokens, usage.output_tokens)
            record_api_usage(
                get_database_path(self.config),
                provider=self.settings.provider,
                model=model,
                purpose=purpose,
                **self._usage_context,
                prompt_tokens=usage.input_tokens,
                completion_tokens=usage.output_tokens,
                cached_tokens=usage.cached_tokens,
                total_tokens=usage.input_tokens + usage.output_tokens,
                cost_cny=cost,
                duration_seconds=duration_seconds,
                first_sentence_seconds=first_sentence_seconds,
                success=success,
            )
        except Exception as exc:  # pragma: no cover - never break the pipeline
            logger.debug("token usage recording skipped: %s", exc)

    def _record_usage_failure(
        self,
        model: str,
        purpose: str,
        error: str,
        *,
        duration_seconds: float | None = None,
    ) -> None:
        try:
            from review_queue.db import get_database_path
            from review_queue.metrics import record_api_usage

            record_api_usage(
                get_database_path(self.config),
                provider=self.settings.provider,
                model=model,
                purpose=purpose,
                **self._usage_context,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cost_cny=0.0,
                duration_seconds=duration_seconds,
                success=False,
                error=error[:500],
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("usage failure recording skipped: %s", exc)


_SENTENCE_END_RE = re.compile(r"[。！？!?；;\n]|(?:\.(?:\s|$))")


def _read_streaming_completion(
    response: Any,
    *,
    model: str,
    protocol: str,
    started_at: float,
) -> tuple[ChatCompletion, float | None]:
    """Read SSE when available and measure when the first sentence is usable."""
    raw_body = bytearray()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, Any] = {}
    response_model = model
    finish_reason: str | None = None
    first_sentence_seconds: float | None = None
    saw_sse = False

    for raw_line in _iter_response_lines(response):
        raw_body.extend(raw_line)
        line = raw_line.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        saw_sse = True
        raw_data = line[5:].strip()
        if not raw_data or raw_data == "[DONE]":
            continue
        event = json.loads(raw_data)
        if protocol == "anthropic":
            event_type = str(event.get("type") or "")
            if event_type == "message_start":
                message = event.get("message") or {}
                response_model = str(message.get("model") or response_model)
                usage.update(message.get("usage") or {})
            elif event_type == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    first_sentence_seconds = _append_text_and_measure(
                        text_parts, str(delta.get("text") or ""), started_at, first_sentence_seconds,
                    )
            elif event_type == "message_delta":
                delta = event.get("delta") or {}
                finish_reason = str(delta.get("stop_reason") or "") or finish_reason
                usage.update(event.get("usage") or {})
            continue

        response_model = str(event.get("model") or response_model)
        if event.get("usage"):
            usage.update(event["usage"])
        choices = event.get("choices") or []
        if not choices:
            continue
        first = choices[0] or {}
        delta = first.get("delta") or {}
        first_sentence_seconds = _append_text_and_measure(
            text_parts, str(delta.get("content") or ""), started_at, first_sentence_seconds,
        )
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            reasoning_parts.append(str(reasoning))
        if first.get("finish_reason") is not None:
            finish_reason = str(first["finish_reason"])

    if not saw_sse:
        completion = _parse_live_completion(json.loads(bytes(raw_body).decode("utf-8")), model, protocol)
        latency = (time.monotonic() - started_at) if completion.text else None
        return completion, latency

    text = "".join(text_parts).strip()
    if text and first_sentence_seconds is None:
        first_sentence_seconds = time.monotonic() - started_at
    if protocol == "anthropic":
        parsed_usage = ChatUsage(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_tokens=int(usage.get("cache_read_input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            raw=dict(usage),
        )
    else:
        parsed_usage = _parse_usage(usage)
    return (
        ChatCompletion(
            text=text,
            reasoning="".join(reasoning_parts).strip() or None,
            model=response_model,
            usage=parsed_usage,
            finish_reason=finish_reason,
            cached=parsed_usage.cached_tokens > 0,
        ),
        first_sentence_seconds,
    )


def _iter_response_lines(response: Any) -> Iterable[bytes]:
    readline = getattr(response, "readline", None)
    if not callable(readline):
        yield response.read()
        return
    while True:
        line = readline()
        if not line:
            return
        yield line


def _append_text_and_measure(
    text_parts: list[str],
    piece: str,
    started_at: float,
    current: float | None,
) -> float | None:
    if piece:
        text_parts.append(piece)
    if current is None and text_parts and _SENTENCE_END_RE.search("".join(text_parts)):
        return time.monotonic() - started_at
    return current


def provider_defaults(provider: str) -> dict[str, str]:
    provider = _normalize_provider(provider)
    defaults = {
        "deepseek": {
            "label": "DeepSeek",
            "protocol": "openai",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "flash_model": "deepseek-v4-flash",
        },
        "openai": {
            "label": "OpenAI",
            "protocol": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4.1",
            "flash_model": "gpt-4.1-mini",
        },
        "google": {
            "label": "Google Gemini",
            "protocol": "openai",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-2.5-flash",
            "flash_model": "gemini-2.5-flash-lite",
        },
        "anthropic": {
            "label": "Anthropic Claude",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-sonnet-4-6",
            "flash_model": "claude-haiku-4-5",
        },
        "zhipu": {
            "label": "智谱 GLM",
            "protocol": "openai",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-5.1",
            "flash_model": "glm-4.7-flashx",
        },
        "qwen": {
            "label": "通义千问 Qwen",
            "protocol": "openai",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen3.6-plus",
            "flash_model": "qwen3.6-flash",
        },
        "custom": {
            "label": "自定义 / 第三方中转",
            "protocol": "openai",
            "base_url": "",
            "model": "",
            "flash_model": "",
        },
    }
    return defaults.get(provider, defaults["custom"])


def _normalize_provider(provider: str) -> str:
    value = (provider or "deepseek").strip().lower()
    aliases = {
        "gemini": "google",
        "google-gemini": "google",
        "claude": "anthropic",
        "zhipuai": "zhipu",
        "bigmodel": "zhipu",
        "dashscope": "qwen",
        "aliyun": "qwen",
        "openai-compatible": "custom",
    }
    return aliases.get(value, value if value in {"deepseek", "openai", "google", "anthropic", "zhipu", "qwen", "custom"} else "custom")


def _normalize_protocol(protocol: str) -> str:
    value = (protocol or "openai").strip().lower()
    aliases = {
        "chat-completions": "openai",
        "openai-compatible": "openai",
        "messages": "anthropic",
        "claude": "anthropic",
    }
    value = aliases.get(value, value)
    return value if value in {"openai", "anthropic"} else "openai"


def _join_endpoint(base_url: str, suffix: str) -> str:
    base = str(base_url or "").rstrip("/")
    suffix = suffix.strip("/")
    if base.endswith("/" + suffix):
        return base
    return f"{base}/{suffix}"


def _parse_live_completion(response_data: Mapping[str, Any], model: str, protocol: str) -> ChatCompletion:
    if protocol == "anthropic":
        content = response_data.get("content") or []
        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, Mapping):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
        usage = response_data.get("usage") or {}
        parsed_usage = ChatUsage(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_tokens=int(usage.get("cache_read_input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            raw=dict(usage),
        )
        return ChatCompletion(
            text="\n".join(parts).strip(),
            reasoning=None,
            model=str(response_data.get("model") or model),
            usage=parsed_usage,
            finish_reason=str(response_data.get("stop_reason") or "") or None,
            cached=False,
        )
    return _parse_completion(response_data, model)


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
    "provider_defaults",
]
