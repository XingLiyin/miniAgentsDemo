"""LLM API Schema 定义。"""

from typing import Optional
from pydantic import BaseModel


class RegisterLLMRequest(BaseModel):
    name: str
    style: str
    api_key: str
    base_url: str = ""
    models: list[str] = []
    default_model: str = ""
    timeout_sec: Optional[int] = None


class AddModelRequest(BaseModel):
    model: str


class SetDefaultModelRequest(BaseModel):
    model: str


class LLMProviderResponse(BaseModel):
    name: str
    style: str
    base_url: str
    models: list[str]
    default_model: str
    timeout_sec: int
