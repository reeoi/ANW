"""ANW 通知总线 (Phase 2)。

scheduler / publisher / cost_limits 等模块通过 :func:`bus.publish` 发出事件,
Web UI 通过 SSE (``/api/notifications/stream``) 订阅,托盘程序通过 SSE 客户端
监听并弹 Windows 桌面气泡。

设计要点：

- 三档严重级：``critical`` / ``warning`` / ``info``。
- 同步 ``publish`` —— 调用方不需要 ``await``,失败不打断主流程。
- 内存 ring buffer (默认 200 条) 用于"最近通知"列表。
- 订阅器是 ``asyncio.Queue``,FastAPI handler 用 ``async for`` 拉。
- 运行多个 event loop / 后台线程时安全：``publish`` 用 ``call_soon_threadsafe``
  把元素塞进每个订阅者所属的 loop。
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Callable, Iterable


class Severity(str, Enum):
    """通知严重等级。"""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Notification:
    """一条通知记录。"""

    severity: str
    title: str
    message: str
    source: str = "system"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    dismissed: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


class _Subscriber:
    """每个订阅者绑定到自己的 event loop,publish 用线程安全方式投递。"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Notification] = asyncio.Queue(maxsize=500)
        self.loop: asyncio.AbstractEventLoop | None = None

    def attach(self) -> None:
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def deliver(self, notification: Notification) -> None:
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, notification)
        except (asyncio.QueueFull, RuntimeError):
            # 慢消费者：丢弃最旧消息腾出位置
            try:
                self.loop.call_soon_threadsafe(_drain_one, self.queue)
                self.loop.call_soon_threadsafe(self.queue.put_nowait, notification)
            except Exception:
                pass


def _drain_one(queue: asyncio.Queue[Notification]) -> None:
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        pass


class NotificationBus:
    """线程安全的发布 / 订阅总线 + 历史 ring buffer。"""

    def __init__(self, capacity: int = 200) -> None:
        self._buffer: deque[Notification] = deque(maxlen=capacity)
        self._subscribers: list[_Subscriber] = []
        self._lock = threading.Lock()
        self._listeners: list[Callable[[Notification], None]] = []
        self._quiet_filter: Callable[[], bool] | None = None

    # -- publish --

    def publish(
        self,
        severity: str | Severity,
        title: str,
        message: str,
        source: str = "system",
        **extras: Any,
    ) -> Notification:
        """同步发布一条通知。可重入 / 任意线程调用。"""
        sev = severity.value if isinstance(severity, Severity) else str(severity)
        if sev not in {s.value for s in Severity}:
            sev = Severity.INFO.value
        notification = Notification(
            severity=sev,
            title=title.strip() or "(无标题)",
            message=message.strip(),
            source=source,
            extras=extras,
        )
        with self._lock:
            self._buffer.appendleft(notification)
            subscribers = list(self._subscribers)
            listeners = list(self._listeners)
            quiet_filter = self._quiet_filter
        # 不打扰时段：critical 永远投递; warning/info 只入 history,不广播
        deliver = True
        if quiet_filter is not None and sev != Severity.CRITICAL.value:
            try:
                if quiet_filter():
                    deliver = False
            except Exception:
                deliver = True
        if not deliver:
            return notification
        for sub in subscribers:
            sub.deliver(notification)
        for listener in listeners:
            try:
                listener(notification)
            except Exception:
                # 监听器异常不能传播；总线必须不阻塞调用方。
                pass
        return notification

    # -- 不打扰过滤器 (服务端判断,Phase 3) --

    def set_quiet_filter(self, predicate: "Callable[[], bool] | None") -> None:
        """注入一个 ``() -> bool`` 谓词;返回 True 时 warning / info 不广播。"""
        with self._lock:
            self._quiet_filter = predicate

    # -- subscribe (async generators) --

    def subscribe(self) -> AsyncIterator[Notification]:
        """同步注册一个订阅者并返回 async generator,逐条 yield 通知。

        必须在 ``async`` 函数内部 (有运行中的 event loop) 调用,因为内部需要
        通过 ``asyncio.get_running_loop()`` 把订阅绑到当前 loop —— 这样跨线程
        发布也能正确投递。
        """
        sub = _Subscriber()
        sub.attach()
        with self._lock:
            self._subscribers.append(sub)
        return self._iter(sub)

    async def _iter(self, sub: "_Subscriber") -> AsyncIterator[Notification]:
        try:
            while True:
                yield await sub.queue.get()
        finally:
            with self._lock:
                if sub in self._subscribers:
                    self._subscribers.remove(sub)

    # -- 同步监听器 (托盘进程在线程里跑时用) --

    def add_listener(self, callback: Callable[[Notification], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Notification], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    # -- 历史 / dismiss --

    def list_recent(self, limit: int = 50, only_undismissed: bool = False) -> list[Notification]:
        with self._lock:
            items = [n for n in self._buffer if (not only_undismissed or not n.dismissed)]
        return items[: max(0, limit)]

    def dismiss(self, notification_id: str) -> bool:
        with self._lock:
            for n in self._buffer:
                if n.id == notification_id:
                    n.dismissed = True
                    return True
        return False

    def dismiss_all(self) -> int:
        count = 0
        with self._lock:
            for n in self._buffer:
                if not n.dismissed:
                    n.dismissed = True
                    count += 1
        return count

    def clear(self) -> None:
        """主要用于测试隔离。"""
        with self._lock:
            self._buffer.clear()
            self._subscribers.clear()
            self._listeners.clear()


bus = NotificationBus()


def publish_many(items: Iterable[tuple[str, str, str]]) -> None:
    """便捷批量发布,主要给 scheduler 启动 / 关闭事件用。"""
    for severity, title, message in items:
        bus.publish(severity, title, message)


__all__ = [
    "NotificationBus",
    "Notification",
    "Severity",
    "bus",
    "publish_many",
]
