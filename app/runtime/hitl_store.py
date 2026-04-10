"""HitlStore：基于 threading.Event 的 HITL 阻塞机制（无 task 创建）。

每个处于 WAITING_INPUT 状态的 session 在此持有一个 HitlEntry。
工作线程调用 wait() 阻塞；答案通过 submit() 由 API 线程注入并唤醒。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class HitlEntry:
    event: threading.Event
    agent_id: str
    prompt: str
    input_type: str
    answer: str = field(default="")


class HitlStore:
    """单例：管理所有活跃 HITL 等待。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, HitlEntry] = {}

    def wait(
        self,
        session_id: str,
        agent_id: str,
        prompt: str,
        input_type: str,
        timeout: float = 3600.0,
    ) -> str:
        """注册等待并阻塞，直到 submit() 注入答案或超时（返回空字符串）。

        线程安全：entry 注册在锁内，Event.wait() 在锁外阻塞。
        """
        entry = HitlEntry(
            event=threading.Event(),
            agent_id=agent_id,
            prompt=prompt,
            input_type=input_type,
        )
        with self._lock:
            self._store[session_id] = entry

        entry.event.wait(timeout=timeout)

        with self._lock:
            self._store.pop(session_id, None)

        return entry.answer

    def submit(self, session_id: str, answer: str) -> "HitlEntry | None":
        """注入答案并唤醒等待线程。返回对应的 entry（含 agent_id），None 表示无等待。"""
        with self._lock:
            entry = self._store.get(session_id)
        if entry is not None:
            entry.answer = answer
            entry.event.set()
        return entry

    def get_pending(self, session_id: str) -> "HitlEntry | None":
        """查询当前是否有活跃的 HITL 等待。"""
        with self._lock:
            return self._store.get(session_id)


# ── 全局单例 ───────────────────────────────────────────────────────────────────

_hitl_store = HitlStore()


def get_hitl_store() -> HitlStore:
    return _hitl_store
