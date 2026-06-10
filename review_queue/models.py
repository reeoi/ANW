"""Compatibility shim — 数据模型已迁至 ``storage.models``（改进清单 #11）。

旧 import 路径继续可用且与 storage 是同一类对象；新代码请直接 import storage。
"""

from __future__ import annotations

from storage.models import DailyPublishPlan, PipelineCostLogEntry, Story

__all__ = ["Story", "DailyPublishPlan", "PipelineCostLogEntry"]
