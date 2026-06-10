"""ANW 统一存储层（改进清单 #11）。

generator 与 review_queue 此前互相 import（cost_tracker/budget/runtime →
review_queue.db，review_queue → generator.api_client / long_novel），构成
循环耦合。本包把 SQLite 数据访问抽到两者之下的独立一层：

- ``storage.connection`` — 统一连接入口与库路径解析；
- ``storage.schema``     — 全部建表 / 迁移 / schema_version；
- ``storage.models``     — Story / DailyPublishPlan / PipelineCostLogEntry；
- ``storage.stories``    — stories / daily_publish_plan / phase_transitions CRUD;
- ``storage.usage``      — pipeline_cost_log / api_usage / pipeline_events 遥测。

旧路径 ``review_queue.db`` / ``review_queue.models`` / ``review_queue.metrics``
保留为 re-export shim，行为与对象身份完全一致；新代码请直接 import storage。
"""

from __future__ import annotations

from storage.connection import connect, get_database_path
from storage.schema import initialize_database

__all__ = ["connect", "get_database_path", "initialize_database"]
