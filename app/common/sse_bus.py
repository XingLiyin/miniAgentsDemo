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

_STREAM_BATCH_WINDOW_SEC = 0.05
_DROPPABLE_STREAM_TYPES: frozenset[str] = frozenset({
    "text_delta",
    "observer_text_delta",
    "reasoning_delta",
    "observer_reasoning_delta",
})


class SseBus:
    """Per-session SSE event distributor."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = threading.Lock()
        self._pending_stream_events: dict[asyncio.Queue, list[dict[str, Any]]] = {}
        self._pending_flush_handles: dict[asyncio.Queue, asyncio.Handle] = {}

    def create_subscription(self, session_id: str) -> "asyncio.Queue[dict]":
        """Create and register a new subscriber queue. Called from async context."""
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._queues[session_id].append(q)
        return q

    def remove_subscription(self, session_id: str, q: "asyncio.Queue") -> None:
        with self._lock:
            queues = self._queues.get(session_id)
            if queues and q in queues:
                queues.remove(q)
        handle = self._pending_flush_handles.pop(q, None)
        if handle is not None:
            handle.cancel()
        self._pending_stream_events.pop(q, None)

    def _can_merge_stream_event(self, prev: dict[str, Any], event: dict[str, Any]) -> bool:
        if prev.get("type") != event.get("type"):
            return False
        if "delta" not in prev or "delta" not in event:
            return False

        prev_meta = {k: v for k, v in prev.items() if k != "delta"}
        event_meta = {k: v for k, v in event.items() if k != "delta"}
        return prev_meta == event_meta

    def _put_event(self, q: "asyncio.Queue[dict[str, Any]]", session_id: str, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        try:
            q.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        if event_type in _DROPPABLE_STREAM_TYPES:
            logger.debug("SSE queue full; dropped stream event for session %s: %s", session_id, event_type)
            return

        try:
            dropped = q.get_nowait()
        except asyncio.QueueEmpty:
            logger.debug("SSE queue unexpectedly empty after QueueFull for session %s", session_id)
            return

        dropped_type = str((dropped or {}).get("type") or "")
        try:
            q.put_nowait(event)
            logger.warning(
                "SSE queue full; evicted oldest event for session %s: dropped=%s kept=%s",
                session_id,
                dropped_type,
                event_type,
            )
        except asyncio.QueueFull:
            logger.warning(
                "SSE queue remained full after eviction for session %s; dropped event=%s",
                session_id,
                event_type,
            )

    def _flush_pending_stream_events(self, q: "asyncio.Queue[dict[str, Any]]", session_id: str) -> None:
        handle = self._pending_flush_handles.pop(q, None)
        if handle is not None and not handle.cancelled():
            handle.cancel()

        pending = self._pending_stream_events.pop(q, [])
        for event in pending:
            self._put_event(q, session_id, event)

    def _buffer_stream_event(self, q: "asyncio.Queue[dict[str, Any]]", session_id: str, event: dict[str, Any]) -> None:
        pending = self._pending_stream_events.setdefault(q, [])
        if pending and self._can_merge_stream_event(pending[-1], event):
            pending[-1]["delta"] = f"{pending[-1].get('delta', '')}{event.get('delta', '')}"
        else:
            pending.append(dict(event))

        if q not in self._pending_flush_handles:
            loop = asyncio.get_running_loop()
            self._pending_flush_handles[q] = loop.call_later(
                _STREAM_BATCH_WINDOW_SEC,
                self._flush_pending_stream_events,
                q,
                session_id,
            )

    def _enqueue_event(self, q: "asyncio.Queue[dict[str, Any]]", session_id: str, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type in _DROPPABLE_STREAM_TYPES:
            self._buffer_stream_event(q, session_id, event)
            return

        self._flush_pending_stream_events(q, session_id)
        self._put_event(q, session_id, event)

    def push(self, session_id: str, event: dict[str, Any]) -> None:
        """Push an event to all subscribers of a session. Thread-safe."""
        try:
            from app.storage.file.event_store import get_event_store
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
                    loop.call_soon_threadsafe(self._enqueue_event, q, session_id, event)
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
