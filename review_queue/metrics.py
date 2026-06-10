"""Compatibility shim — 遥测实现已迁至 ``storage.usage`` / ``storage.schema``
（改进清单 #11）。

旧 import 路径继续可用且与 storage 是同一对象；新代码请直接 import storage。
"""

from __future__ import annotations

from storage.schema import METRICS_SCHEMA as METRICS_SCHEMA
from storage.schema import ensure_metrics_schema
from storage.usage import (
    DEFAULT_COMPLETION_PRICE_CNY_PER_1K,
    DEFAULT_PROMPT_PRICE_CNY_PER_1K,
    estimate_cost_cny,
    list_api_usage_logs,
    query_overview,
    record_api_usage,
    record_pipeline_event,
)

__all__ = [
    "DEFAULT_COMPLETION_PRICE_CNY_PER_1K",
    "DEFAULT_PROMPT_PRICE_CNY_PER_1K",
    "ensure_metrics_schema",
    "estimate_cost_cny",
    "list_api_usage_logs",
    "query_overview",
    "record_api_usage",
    "record_pipeline_event",
]
