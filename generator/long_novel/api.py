"""Long novel REST API — 聚合门面。

实现按职能拆在同包模块中（改进清单 #12：4999 行 → 13 个文件）：

- 纯函数层：``deps`` / ``jobs`` / ``step_artifacts`` / ``review_gate`` /
  ``chapter_resets``
- 步骤引擎：``chapter_steps``（按 step 拆分的 L2 写作引擎，无路由）
- 路由层：``book_routes`` / ``prompt_registry`` / ``setup_routes`` /
  ``autopilot_routes`` / ``chapter_step_routes`` / ``chapter_routes``

本模块只负责把子 router 组装到 ``/api/long-novel`` 前缀下，并按旧导入
路径 re-export 测试与外部代码使用的名字。LLM 客户端的唯一 monkeypatch
缝是 ``deps._deepseek_client``。
"""

from __future__ import annotations

from fastapi import APIRouter

from generator.long_novel import (
    autopilot_routes,
    book_routes,
    chapter_routes,
    chapter_step_routes,
    deps,
    prompt_registry,
    setup_routes,
)
from generator.long_novel.autopilot_routes import (
    _autopilot_chapters_to_write as _autopilot_chapters_to_write,
)
from generator.long_novel.autopilot_routes import (
    _autopilot_chapters_to_write_range as _autopilot_chapters_to_write_range,
)
from generator.long_novel.autopilot_routes import (
    _autopilot_write_one_chapter as _autopilot_write_one_chapter,
)
from generator.long_novel.autopilot_routes import (
    _repair_invalid_autopilot_writing_snapshot as _repair_invalid_autopilot_writing_snapshot,
)
from generator.long_novel.autopilot_routes import (
    _sync_paused_autopilot_snapshot as _sync_paused_autopilot_snapshot,
)
from generator.long_novel.autopilot_routes import (
    api_autopilot_status as api_autopilot_status,
)
from generator.long_novel.chapter_routes import (
    api_reset_chapter_for_regeneration as api_reset_chapter_for_regeneration,
)
from generator.long_novel.chapter_routes import (
    api_reset_chapter_range_for_regeneration as api_reset_chapter_range_for_regeneration,
)
from generator.long_novel.chapter_routes import (
    api_rewrite_chapter as api_rewrite_chapter,
)
from generator.long_novel.chapter_routes import (
    api_rewrite_chapter_range as api_rewrite_chapter_range,
)
from generator.long_novel.chapter_step_routes import (
    api_list_step_history as api_list_step_history,
)
from generator.long_novel.chapter_step_routes import (
    api_revise_chapter_step_progress as api_revise_chapter_step_progress,
)
from generator.long_novel.chapter_step_routes import (
    api_start_revise_chapter_step as api_start_revise_chapter_step,
)
from generator.long_novel.chapter_step_routes import (
    api_write_chapter_step_output as api_write_chapter_step_output,
)
from generator.long_novel.chapter_step_routes import (
    api_write_chapter_step_status as api_write_chapter_step_status,
)
from generator.long_novel.chapter_steps import (
    _api_revise_chapter_step_blocking as _api_revise_chapter_step_blocking,
)
from generator.long_novel.chapter_steps import (
    _api_write_chapter_step_blocking as _api_write_chapter_step_blocking,
)
from generator.long_novel.jobs import (
    _autopilot_job_active as _autopilot_job_active,
)
from generator.long_novel.jobs import (
    _autopilot_job_mark as _autopilot_job_mark,
)
from generator.long_novel.l2_chapter_write import (
    chapter_final_path as chapter_final_path,
)
from generator.long_novel.setup_routes import (
    _finalize_book_setup as _finalize_book_setup,
)
from generator.long_novel.setup_routes import (
    _inferred_setup_phase_status as _inferred_setup_phase_status,
)

# 兼容旧引用：测试/外部以 deps._deepseek_client 为唯一 patch 缝。
_deepseek_client = deps._deepseek_client

router = APIRouter(prefix="/api/long-novel", tags=["long-novel"])
router.include_router(book_routes.router)
router.include_router(prompt_registry.router)
router.include_router(setup_routes.router)
router.include_router(autopilot_routes.router)
router.include_router(chapter_step_routes.router)
router.include_router(chapter_routes.router)

__all__ = ["router"]
