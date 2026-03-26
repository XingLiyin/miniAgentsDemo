"""LLM API Schema 定义。"""

from typing import Optional

from pydantic import BaseModel


class LLMRegisterRequest(BaseModel):
    """LLM 注册请求。"""

    name: str
    style: str  # openai / anthropic
    api_key: str
    base_url: str
    model: str
    timeout_sec: Optional[int] = 60


class LLMRegisterResponse(BaseModel):
    """LLM 注册响应（不返回敏感字段）。"""

    name: str
    style: str
    base_url: str
    model: str
    timeout_sec: int
