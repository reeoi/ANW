"""Local queue and review package for ANP.

The project package is named ``queue`` to match the product structure.  A few
third-party libraries import ``Queue`` from Python's standard-library ``queue``
module, so this package provides a tiny compatible fallback to avoid shadowing
breakage when running local ASGI tests from the repository root.
"""

from __future__ import annotations

from collections import deque
from threading import Condition
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


class Empty(Exception):
    """Raised when a non-blocking get() finds no item."""


class Full(Exception):
    """Raised when a non-blocking put() cannot enqueue an item."""


class Queue(Generic[T]):
    """Small thread-safe FIFO compatible with basic stdlib Queue usage."""

    def __init__(self, maxsize: int = 0) -> None:
        self.maxsize = maxsize
        self._items: deque[T] = deque()
        self._condition = Condition()
        self._unfinished_tasks = 0

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)

    def empty(self) -> bool:
        return self.qsize() == 0

    def full(self) -> bool:
        with self._condition:
            return self.maxsize > 0 and len(self._items) >= self.maxsize

    def put(self, item: T, block: bool = True, timeout: float | None = None) -> None:
        with self._condition:
            if self.maxsize > 0:
                if not block and len(self._items) >= self.maxsize:
                    raise Full
                end_time = None if timeout is None else monotonic() + timeout
                while len(self._items) >= self.maxsize:
                    if timeout is None:
                        self._condition.wait()
                    else:
                        remaining = end_time - monotonic() if end_time is not None else 0
                        if remaining <= 0:
                            raise Full
                        self._condition.wait(remaining)
            self._items.append(item)
            self._unfinished_tasks += 1
            self._condition.notify()

    def put_nowait(self, item: T) -> None:
        self.put(item, block=False)

    def get(self, block: bool = True, timeout: float | None = None) -> T:
        with self._condition:
            if not block and not self._items:
                raise Empty
            end_time = None if timeout is None else monotonic() + timeout
            while not self._items:
                if timeout is None:
                    self._condition.wait()
                else:
                    remaining = end_time - monotonic() if end_time is not None else 0
                    if remaining <= 0:
                        raise Empty
                    self._condition.wait(remaining)
            item = self._items.popleft()
            self._condition.notify()
            return item

    def get_nowait(self) -> T:
        return self.get(block=False)

    def task_done(self) -> None:
        with self._condition:
            if self._unfinished_tasks <= 0:
                raise ValueError("task_done() called too many times")
            self._unfinished_tasks -= 1
            if self._unfinished_tasks == 0:
                self._condition.notify_all()

    def join(self) -> None:
        with self._condition:
            while self._unfinished_tasks:
                self._condition.wait()


LifoQueue = Queue
PriorityQueue = Queue
SimpleQueue = Queue


__all__ = ["Empty", "Full", "LifoQueue", "PriorityQueue", "Queue", "SimpleQueue"]
