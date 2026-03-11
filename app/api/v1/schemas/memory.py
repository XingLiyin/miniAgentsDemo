"""Memory API Schema 定义。"""

from typing import List, Optional

from pydantic import BaseModel


class MessageObject(BaseModel):
    """上下文中的消息对象。"""

    role: str
    content: str


class ContextObject(BaseModel):
    """拼装后的上下文对象。"""

    system_prompt: str
    messages: List[MessageObject]
    token_estimate: Optional[int] = None
