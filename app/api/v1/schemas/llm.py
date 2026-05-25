"""LLM API Schema 定义。"""

from typing import Optional
from pydantic import BaseModel


class ModelConfigRequest(BaseModel):
    name: str
    context_limit: Optional[int] = None  # None → 使用 settings.default_context_limit


class ModelConfigResponse(BaseModel):
    name: str
    context_limit: int


class RegisterLLMRequest(BaseModel):
    name: str
    style: str
    api_key: str
    base_url: str = ""
    models: list[ModelConfigRequest] = []
    default_model: str = ""
    timeout_sec: Optional[int] = None


class AddModelRequest(BaseModel):
    model: str
    context_limit: Optional[int] = None  # None → 使用 settings.default_context_limit


class SetDefaultModelRequest(BaseModel):
    model: str


class LLMProviderResponse(BaseModel):
    name: str
    style: str
    base_url: str
    models: list[ModelConfigResponse]
    default_model: str
    timeout_sec: int


class PingLLMRequest(BaseModel):
    style: str
    api_key: str
    base_url: str = ""
    model: str = ""


class PingLLMResponse(BaseModel):
    ok: bool
    latency_ms: int


class ListModelsRequest(BaseModel):
    style: str
    api_key: str
    base_url: str = ""


class AvailableModelsResponse(BaseModel):
    models: list[str]
