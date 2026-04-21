"""Per-session event log for SSE replay.

Persists display-worthy events to data/event_logs/{session_id}.jsonl so that
reconnecting clients can restore the full chat timeline.

Ephemeral events (streaming deltas, pings, lifecycle signals) are NOT stored.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.storage.file.base import append_jsonl, read_jsonl

logger = logging.getLogger(__name__)

_PERSIST_TYPES: frozenset[str] = frozenset({
    "message",
    "tool_call",
    "task_created",
    "task_updated",
    "text_done",
    "llm_prompt",
    "observer_text_done",
})

_DATA_DIR = Path("data/event_logs")


class EventStore:
    """Append-only JSONL event log per session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return _DATA_DIR / f"{session_id}.jsonl"

    def append(self, session_id: str, event: dict[str, Any]) -> None:
        if event.get("type") not in _PERSIST_TYPES:
            return
        if "created_at" not in event:
            event = {**event, "created_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            try:
                append_jsonl(self._path(session_id), event)
            except Exception as e:
                logger.warning("EventStore.append failed for session %s: %s", session_id, e)

    def load(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            try:
                return read_jsonl(self._path(session_id))
            except Exception as e:
                logger.warning("EventStore.load failed for session %s: %s", session_id, e)
                return []

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        with self._lock:
            try:
                if path.exists():
                    os.remove(path)
            except Exception as e:
                logger.warning("EventStore.delete failed for session %s: %s", session_id, e)


_event_store: EventStore | None = None


def get_event_store() -> EventStore:
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store
