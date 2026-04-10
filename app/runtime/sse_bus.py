"""Per-session SSE event bus.

Worker threads (AgentLoop, Actor, LifecycleManager) call push() to enqueue events.
The SSE endpoint creates a subscription queue and async-reads from it.
Thread safety is achieved via loop.call_soon_threadsafe().
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class SseBus:
    """Per-session SSE event distributor."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = threading.Lock()

    def create_subscription(self, session_id: str) -> "asyncio.Queue[dict]":
        """Create and register a new subscriber queue. Called from async context."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._queues[session_id].append(q)
        return q

    def remove_subscription(self, session_id: str, q: "asyncio.Queue") -> None:
        """Deregister a subscriber queue."""
        with self._lock:
            queues = self._queues.get(session_id)
            if queues and q in queues:
                queues.remove(q)

    def push(self, session_id: str, event: dict[str, Any]) -> None:
        """Push an event to all subscribers of a session. Thread-safe."""
        # Persist display-worthy events for history replay on reconnect
        try:
            from app.runtime.event_store import get_event_store
            get_event_store().append(session_id, event)
        except Exception as e:
            logger.debug("EventStore.append failed: %s", e)

        from app.common.async_utils import get_main_loop
        loop = get_main_loop()
        with self._lock:
            queues = list(self._queues.get(session_id, []))
        if not queues:
            return
        for q in queues:
            if loop is not None and loop.is_running():
                try:
                    loop.call_soon_threadsafe(q.put_nowait, event)
                except Exception as e:
                    logger.debug("SSE push failed for session %s: %s", session_id, e)
            else:
                logger.debug("SSE: no running main loop for session %s", session_id)


_sse_bus: SseBus | None = None


def get_sse_bus() -> SseBus:
    global _sse_bus
    if _sse_bus is None:
        _sse_bus = SseBus()
    return _sse_bus
