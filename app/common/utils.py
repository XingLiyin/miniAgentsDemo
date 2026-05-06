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
    """估算 token 数：CJK 字符约 1.5 token/字，其余约 0.25 token/字符。"""
    cjk = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿' or '豈' <= c <= '﫿')
    other = len(text) - cjk
    return max(1, int(cjk * 1.5 + other * 0.25))


def extract_text(content: str | list) -> str:
    """从 str 或 list[ContentPart dict] 中提取纯文本，用于 str-only 字段。"""
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
