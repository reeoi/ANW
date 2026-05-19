"""ANP configuration loading utilities.

Configuration values are loaded from config.yaml and sensitive values may be
overridden by environment variables. Missing external credentials never block
process startup; the loader switches effective runtime to dry-run/mock mode and
records a clear warning for callers to display or log.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
DEFAULT_DOTENV_PATH = Path(__file__).resolve().parent / ".env"


class ConfigError(Exception):
    """Raised when configuration cannot be parsed or is structurally invalid."""


@dataclass(frozen=True)
class LoadedConfig:
    """Container for loaded application configuration."""

    data: dict[str, Any]
    path: Path
    warnings: list[str] = field(default_factory=list)

    @property
    def is_dry_run(self) -> bool:
        """Return whether the effective runtime is dry-run/mock mode."""
        runtime = self.data.get("runtime", {})
        deepseek = self.data.get("deepseek", {})
        return bool(runtime.get("dry_run") or deepseek.get("mock"))

    @property
    def deepseek_api_key(self) -> str:
        """Return the configured DeepSeek API key, or an empty string."""
        return str(self.data.get("deepseek", {}).get("api_key") or "")


SENSITIVE_ENV_OVERRIDES: dict[tuple[str, ...], str] = {
    ("deepseek", "api_key"): "DEEPSEEK_API_KEY",
    ("deepseek", "base_url"): "DEEPSEEK_BASE_URL",
    ("deepseek", "model"): "DEEPSEEK_MODEL",
    ("deepseek", "flash_model"): "DEEPSEEK_FLASH_MODEL",
}

GENERAL_ENV_OVERRIDES: dict[tuple[str, ...], str] = {
    ("runtime", "mode"): "ANP_MODE",
    ("runtime", "dry_run"): "ANP_DRY_RUN",
    ("deepseek", "mock"): "ANP_MOCK_DEEPSEEK",
    ("deepseek", "thinking_mode"): "ANP_DEEPSEEK_THINKING_MODE",
    ("deepseek", "prompt_cache_enabled"): "ANP_DEEPSEEK_PROMPT_CACHE_ENABLED",
    ("logging", "level"): "ANP_LOG_LEVEL",
    ("database", "sqlite_path"): "ANP_SQLITE_PATH",
    ("audit", "approval_threshold"): "ANP_AI_REVIEW_THRESHOLD",
    ("audit", "max_rewrite_attempts"): "ANP_MAX_REWRITE_ATTEMPTS",
    ("audit", "rewrite_strategy"): "ANP_AI_REVIEW_REWRITE_STRATEGY",
    ("audit", "model"): "ANP_AI_REVIEW_MODEL",
    ("audit", "temperature"): "ANP_AI_REVIEW_TEMPERATURE",
    ("audit", "timeout_seconds"): "ANP_AI_REVIEW_TIMEOUT_SECONDS",
    ("cost_limits", "monthly_budget_cny"): "ANP_MONTHLY_BUDGET_CNY",
    ("cost_limits", "daily_token_limit"): "ANP_DAILY_TOKEN_LIMIT",
    ("cost_limits", "on_budget_exceeded"): "ANP_ON_BUDGET_EXCEEDED",
    ("scheduler", "enabled"): "ANP_SCHEDULER_ENABLED",
    ("scheduler", "weekly_scan_cron"): "ANP_WEEKLY_SCAN_CRON",
    ("scheduler", "plan_today_cron"): "ANP_PLAN_TODAY_CRON",
    ("scan", "pool_size"): "ANP_SCAN_POOL_SIZE",
    ("scan", "seed_file"): "ANP_SCAN_SEED_FILE",
    ("c_pipeline", "max_concurrent_pipelines"): "ANP_MAX_CONCURRENT_PIPELINES",
    ("c_pipeline", "phase_2_max_retries"): "ANP_PHASE_2_MAX_RETRIES",
    ("c_pipeline", "phase_3_section_max_retries"): "ANP_PHASE_3_SECTION_MAX_RETRIES",
}

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def load_config(path: str | Path | None = None) -> LoadedConfig:
    """Load application configuration from YAML plus environment overrides.

    Args:
        path: Optional path to a YAML configuration file. Defaults to the
            project-level config.yaml.

    Returns:
        LoadedConfig containing normalized config data and non-fatal warnings.

    Raises:
        ConfigError: If the YAML file is missing, invalid, or not a mapping.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"Configuration file not found: {config_path}. "
            "Create config.yaml or set ANP_CONFIG to a valid file."
        )

    dotenv_path = Path(os.getenv("ANP_DOTENV") or DEFAULT_DOTENV_PATH)
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=True)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML configuration in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Configuration root must be a mapping: {config_path}")

    data = copy.deepcopy(raw)
    _apply_environment_overrides(data, SENSITIVE_ENV_OVERRIDES)
    _apply_environment_overrides(data, GENERAL_ENV_OVERRIDES)

    warnings = _ensure_safe_runtime(data)
    return LoadedConfig(data=data, path=config_path, warnings=warnings)


def load_from_environment() -> LoadedConfig:
    """Load config from ANP_CONFIG or the default config.yaml path."""
    return load_config(os.getenv("ANP_CONFIG") or DEFAULT_CONFIG_PATH)


def is_wf_next() -> bool:
    """Check if ANP_WF_NEXT=1 feature flag is set (phased rollout for workflow v2)."""
    return os.getenv("ANP_WF_NEXT", "").strip() in ("1", "true", "yes", "on")


def _apply_environment_overrides(
    data: dict[str, Any], overrides: dict[tuple[str, ...], str]
) -> None:
    for keys, env_name in overrides.items():
        value = os.getenv(env_name)
        if value is None:
            continue
        value = _strip_env_quotes(value)
        if value == "":
            continue
        existing_value = _get_nested(data, keys)
        _set_nested(data, keys, _coerce_env_value(value, existing_value))


def _strip_env_quotes(value: str) -> str:
    """Strip a single matched pair of surrounding quotes from an env value.

    Some shells / GUIs persist values like ``KEY=""`` literally including the
    quote characters. Treat such values as empty so we fall back to YAML
    defaults instead of producing URLs like ``""/chat/completions``.
    """
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1]
    return s


def _ensure_safe_runtime(data: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    runtime = _ensure_mapping(data, "runtime")
    deepseek = _ensure_mapping(data, "deepseek")

    api_key = str(deepseek.get("api_key") or "").strip()
    if not api_key:
        deepseek["mock"] = True
        runtime["dry_run"] = True
        warnings.append(
            "DeepSeek API key is missing; running in mock/dry-run mode. "
            "Set deepseek.api_key in config.yaml or DEEPSEEK_API_KEY to enable live calls."
        )

    mode = str(runtime.get("mode") or "semi-auto")
    if mode not in {"auto", "semi-auto"}:
        warnings.append(
            f"Unknown runtime.mode '{mode}', falling back to 'semi-auto'."
        )
        runtime["mode"] = "semi-auto"

    return warnings


def _ensure_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if isinstance(value, dict):
        return value
    value = {}
    data[key] = value
    return value


def _get_nested(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_nested(data: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    current = data
    for key in keys[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[keys[-1]] = value


def _coerce_env_value(value: str, existing_value: Any) -> Any:
    if isinstance(existing_value, bool):
        lowered = value.strip().lower()
        if lowered in TRUE_VALUES:
            return True
        if lowered in FALSE_VALUES:
            return False
    if isinstance(existing_value, int) and not isinstance(existing_value, bool):
        try:
            return int(value)
        except ValueError:
            return value
    if isinstance(existing_value, float):
        try:
            return float(value)
        except ValueError:
            return value
    return value


__all__ = ["ConfigError", "LoadedConfig", "is_wf_next", "load_config", "load_from_environment"]
