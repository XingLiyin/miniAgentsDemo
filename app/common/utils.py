"""通用工具函数：ULID 生成、时间工具、token 估算。"""

from __future__ import annotations

import os
import time
import random
import string
from datetime import datetime, timezone


# Crockford's Base32 字符表（ULID 规范）
_ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_time(t_ms: int, length: int) -> str:
    """将时间戳（毫秒）编码为 Base32 字符串。"""
    chars = []
    for _ in range(length):
        chars.append(_ENCODING[t_ms & 0x1F])
        t_ms >>= 5
    return "".join(reversed(chars))


def _encode_random(length: int) -> str:
    """生成随机部分（16 字节 Base32）。"""
    return "".join(random.choices(_ENCODING, k=length))


def new_ulid() -> str:
    """生成标准 ULID（26 字符，毫秒时间戳 + 随机）。"""
    t_ms = int(time.time() * 1000)
    return _encode_time(t_ms, 10) + _encode_random(16)


# ULID 前缀生成函数
def new_session_id() -> str:
    return f"ses_{new_ulid()}"


def new_task_id() -> str:
    return f"tsk_{new_ulid()}"


def new_agent_id() -> str:
    return f"agt_{new_ulid()}"


def new_template_id() -> str:
    return f"tpl_{new_ulid()}"


def new_memory_id() -> str:
    return f"mem_{new_ulid()}"


def new_blackboard_entry_id() -> str:
    return f"bbe_{new_ulid()}"


def new_tool_call_id() -> str:
    return f"tlc_{new_ulid()}"


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（约 4 字符/token）。"""
    return max(1, len(text) // 4)
