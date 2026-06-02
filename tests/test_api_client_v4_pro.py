"""Tests for the v4-pro DeepSeek client (Phase A3).

Verifies mock-mode determinism, configuration parsing, response parsing
(including ``prompt_cache_hit_tokens`` and ``reasoning_content``), thinking-
mode override, and flash-model routing.
"""

from __future__ import annotations

import json
import sys
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config_loader import LoadedConfig
from generator.api_client import (
    ChatCompletion,
    ChatUsage,
    DeepSeekClient,
    DeepSeekSettings,
    _parse_completion,
    _parse_live_completion,
    _parse_usage,
    provider_defaults,
)


def _config(**deepseek_overrides) -> LoadedConfig:
    deepseek = {
        "api_key": "",
        "model": "deepseek-v4-pro",
        "flash_model": "deepseek-v4-flash",
        "thinking_mode": True,
        "prompt_cache_enabled": True,
        "timeout_seconds": 120,
        "max_retries": 3,
        "mock": True,
    }
    deepseek.update(deepseek_overrides)
    return LoadedConfig(
        data={"runtime": {"dry_run": True}, "deepseek": deepseek},
        path=Path("config.yaml"),
    )


def test_settings_default_to_v4_pro_with_thinking_and_cache() -> None:
    client = DeepSeekClient(_config(api_key="sk-test", mock=False))
    assert client.settings.model == "deepseek-v4-pro"
    assert client.settings.flash_model == "deepseek-v4-flash"
    assert client.settings.thinking_mode is True
    assert client.settings.prompt_cache_enabled is True
    assert client.settings.timeout_seconds == 120
    assert client.settings.max_retries == 3


def test_is_mock_true_when_no_api_key() -> None:
    assert DeepSeekClient(_config()).is_mock() is True


def test_is_mock_true_when_dry_run_even_with_key() -> None:
    config = LoadedConfig(
        data={
            "runtime": {"dry_run": True},
            "deepseek": {"api_key": "sk-real", "mock": False},
        },
        path=Path("config.yaml"),
    )
    assert DeepSeekClient(config).is_mock() is True


def test_chat_completion_mock_returns_deterministic_text_and_usage() -> None:
    client = DeepSeekClient(_config())
    result = client.chat_completion(
        messages=[
            {"role": "system", "content": "你是短篇小说作者。"},
            {"role": "user", "content": "请帮我写一个三千字的复仇短篇。"},
        ],
    )
    assert isinstance(result, ChatCompletion)
    assert "[mock]" in result.text
    assert "deepseek-v4-pro" in result.text
    assert result.model == "deepseek-v4-pro"
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.usage.cached_tokens == 0
    assert result.cached is False
    # thinking_mode default = True → mock should expose a reasoning trace
    assert result.reasoning is not None
    assert "思考模式已开启" in result.reasoning


def test_chat_completion_thinking_mode_override_off_drops_reasoning() -> None:
    client = DeepSeekClient(_config(thinking_mode=True))
    result = client.chat_completion(
        messages=[{"role": "user", "content": "请用简短一句话回答。"}],
        thinking_mode=False,
    )
    assert result.reasoning is None
    assert "thinking_mode=off" in result.text


def test_chat_completion_model_override_routes_to_flash() -> None:
    client = DeepSeekClient(_config())
    result = client.chat_completion(
        messages=[{"role": "user", "content": "降级路径触发的高流量 phase。"}],
        model=client.settings.flash_model,
    )
    assert result.model == "deepseek-v4-flash"
    assert "deepseek-v4-flash" in result.text


def test_parse_usage_extracts_prompt_cache_hit_tokens() -> None:
    usage = _parse_usage({
        "prompt_tokens": 12000,
        "completion_tokens": 1500,
        "prompt_cache_hit_tokens": 11500,
        "prompt_cache_miss_tokens": 500,
        "total_tokens": 13500,
    })
    assert usage.input_tokens == 12000
    assert usage.cached_tokens == 11500
    assert usage.output_tokens == 1500
    assert usage.cache_hit_ratio == round(11500 / 12000, 4)


def test_parse_usage_falls_back_to_nested_prompt_tokens_details() -> None:
    usage = _parse_usage({
        "prompt_tokens": 8000,
        "completion_tokens": 1200,
        "prompt_tokens_details": {"cached_tokens": 6500},
    })
    assert usage.cached_tokens == 6500


def test_parse_usage_handles_missing_cache_field_as_zero() -> None:
    usage = _parse_usage({"prompt_tokens": 100, "completion_tokens": 50})
    assert usage.cached_tokens == 0
    assert usage.cache_hit_ratio == 0.0


def test_parse_completion_extracts_text_reasoning_and_cached_flag() -> None:
    completion = _parse_completion(
        {
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "message": {
                        "content": "正文段落。",
                        "reasoning_content": "step 1: 思考 ...",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "prompt_cache_hit_tokens": 800,
            },
        },
        model="deepseek-v4-pro",
    )
    assert completion.text == "正文段落。"
    assert completion.reasoning == "step 1: 思考 ..."
    assert completion.finish_reason == "stop"
    assert completion.usage.cached_tokens == 800
    assert completion.cached is True


def test_settings_overrides_picked_up_from_config() -> None:
    client = DeepSeekClient(
        _config(
            api_key="sk-real",
            mock=False,
            model="deepseek-v4-pro-custom",
            flash_model="deepseek-v4-flash-custom",
            thinking_mode=False,
            prompt_cache_enabled=False,
            timeout_seconds=240,
            max_retries=5,
        )
    )
    assert isinstance(client.settings, DeepSeekSettings)
    assert client.settings.model == "deepseek-v4-pro-custom"
    assert client.settings.flash_model == "deepseek-v4-flash-custom"
    assert client.settings.thinking_mode is False
    assert client.settings.prompt_cache_enabled is False
    assert client.settings.timeout_seconds == 240
    assert client.settings.max_retries == 5


def test_live_completion_retries_incomplete_read() -> None:
    config = LoadedConfig(
        data={
            "runtime": {"dry_run": False},
            "deepseek": {
                "api_key": "sk-real",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro",
                "timeout_seconds": 120,
                "max_retries": 2,
                "mock": False,
            },
        },
        path=Path("config.yaml"),
    )
    client = DeepSeekClient(config)

    class BrokenResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _size=-1):
            raise IncompleteRead(b"x")

    class GoodResponse:
        def __init__(self):
            self.payload = json.dumps({
                "model": "deepseek-v4-pro",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }).encode("utf-8")
            self.offset = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            if size is None or size < 0:
                size = len(self.payload) - self.offset
            chunk = self.payload[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    with patch("generator.api_client.urlopen", side_effect=[BrokenResponse(), GoodResponse()]) as fake_urlopen:
        result = client.chat_completion([{"role": "user", "content": "hello"}])

    assert result.text == "ok"
    assert fake_urlopen.call_count == 2


def test_chat_usage_cache_hit_ratio_clamped_to_one() -> None:
    usage = ChatUsage(input_tokens=1000, cached_tokens=2000, output_tokens=10)
    assert usage.cache_hit_ratio == 1.0


def test_deepseek_live_request_uses_current_thinking_shape() -> None:
    client = DeepSeekClient(_config(api_key="sk-real", mock=False))
    url, headers, payload = client._build_live_request(
        [{"role": "user", "content": "hello"}],
        model=client.settings.model,
        thinking_mode=True,
        temperature=0.5,
        response_format=None,
    )

    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer sk-real"
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert "thinking_mode" not in payload
    assert "prompt_cache_enabled" not in payload


def test_anthropic_protocol_builds_messages_request_and_parses_response() -> None:
    client = DeepSeekClient(
        _config(
            provider="custom",
            protocol="anthropic",
            api_key="sk-ant",
            base_url="https://relay.example/v1",
            model="claude-sonnet-4-6",
            mock=False,
        )
    )
    url, headers, payload = client._build_live_request(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ],
        model=client.settings.model,
        thinking_mode=False,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    assert url == "https://relay.example/v1/messages"
    assert headers["x-api-key"] == "sk-ant"
    assert payload["system"] == "system"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["max_tokens"] == 16384
    assert payload["stream"] is True

    completion = _parse_live_completion(
        {
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 12, "cache_read_input_tokens": 7, "output_tokens": 3},
            "stop_reason": "end_turn",
        },
        model="claude-sonnet-4-6",
        protocol="anthropic",
    )
    assert completion.text == "ok"
    assert completion.usage.input_tokens == 12
    assert completion.usage.cached_tokens == 7
    assert completion.usage.output_tokens == 3


def test_live_completion_records_first_complete_sentence_latency() -> None:
    config = _config(api_key="sk-real", mock=False)
    config.data["runtime"]["dry_run"] = False
    client = DeepSeekClient(config)

    class StreamResponse:
        def __init__(self) -> None:
            self.lines = iter([
                b'data: {"model":"deepseek-v4-pro","choices":[{"delta":{"content":"\\u7b2c\\u4e00"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"\\u53e5\\u3002"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"\\u7b2c\\u4e8c\\u53e5"},"finish_reason":"stop"}]}\n',
                b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3}}\n',
                b'data: [DONE]\n',
            ])

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def readline(self):
            return next(self.lines, b"")

    with (
        patch("generator.api_client.urlopen", return_value=StreamResponse()),
        patch("generator.api_client.time.monotonic", side_effect=[0.0, 1.0, 3.5, 5.0]),
        patch.object(client, "_record_usage") as record_usage,
    ):
        result = client.chat_completion([{"role": "user", "content": "hello"}])

    assert result.text == "第一句。第二句"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 3
    assert record_usage.call_args.kwargs["first_sentence_seconds"] == 2.5
    assert record_usage.call_args.kwargs["duration_seconds"] == 4.0


def test_provider_presets_include_fast_model_and_protocol() -> None:
    assert provider_defaults("qwen")["flash_model"] == "qwen3.6-flash"
    assert provider_defaults("anthropic")["protocol"] == "anthropic"
    assert provider_defaults("custom")["protocol"] == "openai"
