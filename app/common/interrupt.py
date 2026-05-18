"""Interrupt registry：per-session threading.Event，用于向 worker thread 发送打断信号。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.types import ToolCallRecord


@dataclass
class InterruptContext:
    """打断时 Actor 已积累的执行上下文，随异常向上传递。"""
    tool_calls: list["ToolCallRecord"] = field(default_factory=list)
    partial_text: str = ""


class AgentInterruptedError(Exception):
    """Actor 阶段检测到打断信号时抛出，由 AgentLoop 捕获处理。"""

    def __init__(self, reason: str, context: "InterruptContext | None" = None) -> None:
        super().__init__(reason)
        self.context: InterruptContext | None = context


class InterruptRegistry:
    """进程级单例，管理所有活跃 session 的打断标志。"""

    _flags: dict[str, threading.Event] = {}
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_or_create(cls, session_id: str) -> threading.Event:
        """获取或新建 session 的打断 flag（新建时已清除）。"""
        with cls._lock:
            if session_id not in cls._flags:
                cls._flags[session_id] = threading.Event()
            return cls._flags[session_id]

    @classmethod
    def set(cls, session_id: str) -> None:
        """设置打断信号，通知对应 worker thread。"""
        with cls._lock:
            flag = cls._flags.get(session_id)
        if flag is not None:
            flag.set()

    @classmethod
    def clear(cls, session_id: str) -> None:
        """清除打断信号（resume 前调用）。删除旧 flag，下次 get_or_create 创建新实例。"""
        with cls._lock:
            cls._flags.pop(session_id, None)

    @classmethod
    def unregister(cls, session_id: str) -> None:
        """session 销毁时清理。"""
        cls.clear(session_id)
