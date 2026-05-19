"""测试通知总线 (NotificationBus)。"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from review_queue.notification_bus import NotificationBus, Severity, bus


@pytest.fixture()
def fresh_bus() -> NotificationBus:
    b = NotificationBus(capacity=20)
    yield b
    b.clear()


def test_publish_appends_and_returns_notification(fresh_bus: NotificationBus) -> None:
    n = fresh_bus.publish(Severity.WARNING, "标题", "消息")
    assert n.severity == "warning"
    assert n.title == "标题"
    items = fresh_bus.list_recent()
    assert len(items) == 1
    assert items[0].id == n.id


def test_publish_normalizes_unknown_severity(fresh_bus: NotificationBus) -> None:
    n = fresh_bus.publish("hyper", "x", "y")
    assert n.severity == "info"


def test_publish_blank_title_uses_placeholder(fresh_bus: NotificationBus) -> None:
    n = fresh_bus.publish(Severity.INFO, "   ", "msg")
    assert n.title == "(无标题)"


def test_buffer_capacity(fresh_bus: NotificationBus) -> None:
    for i in range(30):
        fresh_bus.publish(Severity.INFO, f"t{i}", "m")
    items = fresh_bus.list_recent(limit=100)
    assert len(items) == 20
    # 最近的在前
    assert items[0].title == "t29"
    assert items[-1].title == "t10"


def test_dismiss_marks_notification(fresh_bus: NotificationBus) -> None:
    n = fresh_bus.publish(Severity.WARNING, "x", "y")
    assert fresh_bus.dismiss(n.id) is True
    assert fresh_bus.dismiss("not-real") is False
    items = fresh_bus.list_recent(only_undismissed=True)
    assert items == []
    items = fresh_bus.list_recent(only_undismissed=False)
    assert items[0].dismissed is True


def test_dismiss_all(fresh_bus: NotificationBus) -> None:
    fresh_bus.publish(Severity.INFO, "a", "b")
    fresh_bus.publish(Severity.INFO, "c", "d")
    n = fresh_bus.dismiss_all()
    assert n == 2
    assert fresh_bus.dismiss_all() == 0  # 第二次应该没什么可清


def test_listener_receives_publication(fresh_bus: NotificationBus) -> None:
    received: list[str] = []
    fresh_bus.add_listener(lambda n: received.append(n.title))
    fresh_bus.publish(Severity.INFO, "Hello", "World")
    assert received == ["Hello"]
    fresh_bus.remove_listener(received.append)  # 不存在的，应静默 (因为我们传的是 lambda)


def test_listener_exception_is_swallowed(fresh_bus: NotificationBus) -> None:
    def boom(_n) -> None:
        raise RuntimeError("listener crash")

    fresh_bus.add_listener(boom)
    # 不应抛
    n = fresh_bus.publish(Severity.WARNING, "x", "y")
    assert n.title == "x"


def test_async_subscribe_yields_notification(fresh_bus: NotificationBus) -> None:
    async def runner() -> str:
        gen = fresh_bus.subscribe()
        # 让订阅者先注册
        await asyncio.sleep(0)
        # 同一个 loop 内同步调用 publish
        fresh_bus.publish(Severity.WARNING, "推", "送")
        item = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await gen.aclose()
        return item.title

    assert asyncio.run(runner()) == "推"


def test_subscribe_from_other_thread_receives(fresh_bus: NotificationBus) -> None:
    """跨线程 publish 必须能送达 asyncio 订阅者。"""

    async def runner() -> str:
        gen = fresh_bus.subscribe()
        await asyncio.sleep(0)
        # 在另一个线程发布
        thread = threading.Thread(target=lambda: fresh_bus.publish(Severity.INFO, "跨", "线程"))
        thread.start()
        thread.join()
        item = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        await gen.aclose()
        return item.title

    assert asyncio.run(runner()) == "跨"


def test_to_sse_format(fresh_bus: NotificationBus) -> None:
    n = fresh_bus.publish(Severity.WARNING, "T", "M")
    sse = n.to_sse()
    assert sse.startswith("data: ")
    assert sse.endswith("\n\n")
    assert "warning" in sse


def test_global_bus_singleton_present() -> None:
    assert bus is not None
    assert hasattr(bus, "publish")


def test_quiet_filter_suppresses_warning_and_info(fresh_bus: NotificationBus) -> None:
    received: list[str] = []
    fresh_bus.add_listener(lambda n: received.append(n.severity))
    fresh_bus.set_quiet_filter(lambda: True)
    fresh_bus.publish(Severity.INFO, "i", "m")
    fresh_bus.publish(Severity.WARNING, "w", "m")
    # critical 永远投递
    fresh_bus.publish(Severity.CRITICAL, "c", "m")
    assert received == ["critical"]
    # buffer 仍然记录所有 (history 不丢)
    assert len(fresh_bus.list_recent()) == 3


def test_quiet_filter_when_off_delivers_all(fresh_bus: NotificationBus) -> None:
    received: list[str] = []
    fresh_bus.add_listener(lambda n: received.append(n.severity))
    fresh_bus.set_quiet_filter(lambda: False)
    fresh_bus.publish(Severity.INFO, "i", "m")
    fresh_bus.publish(Severity.CRITICAL, "c", "m")
    assert received == ["info", "critical"]


def test_quiet_filter_exception_falls_back_to_deliver(fresh_bus: NotificationBus) -> None:
    received: list[str] = []
    fresh_bus.add_listener(lambda n: received.append(n.severity))
    fresh_bus.set_quiet_filter(lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    fresh_bus.publish(Severity.INFO, "i", "m")
    assert received == ["info"]
