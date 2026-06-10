"""Compatibility shim — 实现已迁至顶层 ``storage`` 包（改进清单 #11）。

generator ↔ review_queue 的循环耦合根治后，数据访问层统一住在 storage/：
schema 建表迁移在 ``storage.schema``，stories CRUD 在 ``storage.stories``，
成本遥测在 ``storage.usage``，路径解析在 ``storage.connection``。

本模块按原有公开名 re-export，旧 import 路径继续可用且与 storage 是同一
对象（monkeypatch 任一侧均生效于共享函数对象）。新代码请直接 import storage。
"""

from __future__ import annotations

from storage.connection import get_database_path
from storage.schema import (
    SCHEMA,
    initialize_database,
)
from storage.schema import (
    _migrate_add_cancel_requested as _migrate_add_cancel_requested,
)
from storage.schema import (
    _migrate_add_cost_log_story_title as _migrate_add_cost_log_story_title,
)
from storage.stories import (
    _SELECT_STORY_SQL as _SELECT_STORY_SQL,
)
from storage.stories import (
    REVIEWABLE_STATUSES,
    TERMINAL_STATUSES,
    add_pipeline_cost,
    get_daily_publish_plan,
    get_story,
    insert_story,
    is_cancel_requested,
    list_phase_transitions,
    list_reviewable_stories,
    request_story_cancel,
    story_from_row,
    update_story_ai_review,
    update_story_metadata,
    update_story_phase,
    update_story_status,
    upsert_daily_publish_plan,
)
from storage.usage import insert_pipeline_cost_log, list_pipeline_cost_logs

__all__ = [
    "REVIEWABLE_STATUSES",
    "TERMINAL_STATUSES",
    "SCHEMA",
    "add_pipeline_cost",
    "get_daily_publish_plan",
    "get_database_path",
    "get_story",
    "initialize_database",
    "insert_pipeline_cost_log",
    "insert_story",
    "is_cancel_requested",
    "list_pipeline_cost_logs",
    "list_reviewable_stories",
    "request_story_cancel",
    "list_phase_transitions",
    "story_from_row",
    "update_story_ai_review",
    "update_story_metadata",
    "update_story_phase",
    "update_story_status",
    "upsert_daily_publish_plan",
]
