"""内存 EventBus（Phase 1）。

简单的同步发布/订阅，订阅者收到事件后同步执行回调。
Phase 2 可替换为消息队列（Kafka/Redis Streams）实现。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 回调类型：(event_type: str, payload: dict) -> None
EventHandler = Callable[[str, dict[str, Any]], None]


class EventBus:
    """内存同步事件总线。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """订阅指定事件类型。"""
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """订阅所有事件类型（通配符）。"""
        self._handlers["*"].append(handler)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """发布事件，同步调用所有订阅者。"""
        handlers = self._handlers.get(event_type, []) + self._handlers.get("*", [])
        for handler in handlers:
            try:
                handler(event_type, payload)
            except Exception:
                logger.exception("EventBus handler error for event=%s", event_type)


# 全局单例（Phase 1 单进程足够）
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
