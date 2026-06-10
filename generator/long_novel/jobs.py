"""长篇 API 的进程内任务注册表：取消令牌、全自动任务、章节步骤任务。

全部是 ``threading.Lock`` 保护的进程内状态，无持久化；FastAPI 在单进程
uvicorn 里服务本地 UI，跨请求共享这些注册表即可防止重复启动同一任务。
"""

from __future__ import annotations

import threading

_CHAPTER_STEP_STALE_SECONDS = 60 * 60 * 2

# ── Cancel tokens for book operations ──────────────────────────────────
_cancel_tokens: dict[int, bool] = {}
_cancel_lock = threading.Lock()
_autopilot_jobs: set[int] = set()
_autopilot_jobs_lock = threading.Lock()
_chapter_step_jobs: set[tuple[int, int, str]] = set()
_chapter_step_jobs_lock = threading.Lock()


def _is_cancelled(book_id: int) -> bool:
    with _cancel_lock:
        return _cancel_tokens.get(book_id, False)


def _set_cancel(book_id: int, value: bool) -> None:
    with _cancel_lock:
        _cancel_tokens[book_id] = value


def _autopilot_job_active(book_id: int) -> bool:
    with _autopilot_jobs_lock:
        return int(book_id) in _autopilot_jobs


def _autopilot_job_mark(book_id: int, active: bool) -> None:
    with _autopilot_jobs_lock:
        if active:
            _autopilot_jobs.add(int(book_id))
        else:
            _autopilot_jobs.discard(int(book_id))


def _step_job_key(book_id: int, chapter_number: int, step_name: str) -> tuple[int, int, str]:
    return (int(book_id), int(chapter_number), str(step_name))


def _step_job_active(book_id: int, chapter_number: int, step_name: str) -> bool:
    with _chapter_step_jobs_lock:
        return _step_job_key(book_id, chapter_number, step_name) in _chapter_step_jobs


def _step_job_mark(book_id: int, chapter_number: int, step_name: str, active: bool) -> None:
    key = _step_job_key(book_id, chapter_number, step_name)
    with _chapter_step_jobs_lock:
        if active:
            _chapter_step_jobs.add(key)
        else:
            _chapter_step_jobs.discard(key)
