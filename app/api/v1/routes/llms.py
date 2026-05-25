"""LLM Provider 管理路由。"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from app.api.v1.schemas.llm import (
    AddModelRequest,
    AvailableModelsResponse,
    ListModelsRequest,
    LLMProviderResponse,
    ModelConfigResponse,
    PingLLMRequest,
    PingLLMResponse,
    RegisterLLMRequest,
    SetDefaultModelRequest,
)
from app.config.settings import get_settings
from app.llm.registry import LLMProvider, ModelConfig, SUPPORTED_LLM_STYLES, get_llm_registry

router = APIRouter()


def _to_response(p: LLMProvider) -> LLMProviderResponse:
    return LLMProviderResponse(
        name=p.name,
        style=p.style,
        base_url=p.base_url,
        models=[ModelConfigResponse(name=m.name, context_limit=m.context_limit) for m in p.models],
        default_model=p.default_model,
        timeout_sec=p.timeout_sec,
    )


# ── Ping 内部辅助 ─────────────────────────────────────────────────────────────

def _ping_via_stream(adapter, ping_req) -> None:
    """使用流式接口发送 ping 请求；遇到错误 chunk 立即抛出 RuntimeError。"""
    from app.llm.base import BaseAdapter  # noqa: F401
    for chunk in adapter.stream(ping_req):
        if chunk.error:
            raise RuntimeError(str(chunk.error))
        if chunk.is_done:
            break


# ── Ping（连通性验证）────────────────────────────────────────────────────────

@router.post("/ping", response_model=PingLLMResponse)
def ping_provider(req: PingLLMRequest) -> PingLLMResponse:
    from app.llm.anthropic_adapter import AnthropicAdapter
    from app.llm.openai_adapter import OpenAIAdapter
    from app.llm.transport_httpx import HttpxTransport
    from app.llm.types import LLMMessage, LLMRequest

    if req.style not in SUPPORTED_LLM_STYLES:
        raise HTTPException(400, {"code": "INVALID_LLM_STYLE", "message": f"Unsupported style: {req.style}"})

    transport = HttpxTransport(timeout=15)
    base_url = req.base_url or (
        "https://api.anthropic.com" if req.style == "anthropic" else "https://api.openai.com"
    )
    model = req.model or (
        "claude-haiku-4-5-20251001" if req.style == "anthropic" else "gpt-4o-mini"
    )

    if req.style == "openai":
        adapter = OpenAIAdapter(req.api_key, base_url, transport, timeout_sec=15)
    else:
        adapter = AnthropicAdapter(req.api_key, base_url, transport, timeout_sec=15)

    ping_req = LLMRequest(
        model=model,
        messages=[LLMMessage(role="user", content="hi")],
        max_tokens=1,
    )

    t0 = time.monotonic()
    try:
        _ping_via_stream(adapter, ping_req)
    except RuntimeError as exc:
        raise HTTPException(422, {"code": "PING_FAILED", "message": str(exc)})
    latency_ms = int((time.monotonic() - t0) * 1000)

    return PingLLMResponse(ok=True, latency_ms=latency_ms)


# ── 可用模型列举 ──────────────────────────────────────────────────────────────

def _fetch_available_models(style: str, api_key: str, base_url: str) -> list[str]:
    """调用 provider 的 /v1/models 接口，返回模型 ID 列表。"""
    import httpx

    base = base_url.rstrip("/") or (
        "https://api.anthropic.com" if style == "anthropic" else "https://api.openai.com"
    )
    url = f"{base}/v1/models"
    headers = (
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        if style == "anthropic"
        else {"Authorization": f"Bearer {api_key}"}
    )
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"HTTP {exc.response.status_code}: {exc.response.text[:300]}")
    except Exception as exc:
        raise RuntimeError(str(exc))

    items = data.get("data") or []
    return sorted(item.get("id", "") for item in items if item.get("id"))


@router.post("/available-models", response_model=AvailableModelsResponse)
def list_available_models_unregistered(req: ListModelsRequest) -> AvailableModelsResponse:
    if req.style not in SUPPORTED_LLM_STYLES:
        raise HTTPException(400, {"code": "INVALID_LLM_STYLE", "message": f"Unsupported style: {req.style}"})
    try:
        models = _fetch_available_models(req.style, req.api_key, req.base_url)
    except RuntimeError as exc:
        raise HTTPException(422, {"code": "LIST_MODELS_FAILED", "message": str(exc)})
    return AvailableModelsResponse(models=models)


# ── Provider CRUD ─────────────────────────────────────────────────────────────

@router.post("", response_model=LLMProviderResponse, status_code=201)
def register_provider(req: RegisterLLMRequest) -> LLMProviderResponse:
    registry = get_llm_registry()
    if registry.is_registered(req.name):
        raise HTTPException(409, {"code": "LLM_ALREADY_EXISTS", "message": f"Provider '{req.name}' already registered"})
    if req.style not in SUPPORTED_LLM_STYLES:
        raise HTTPException(400, {"code": "INVALID_LLM_STYLE", "message": f"Unsupported style: {req.style}"})

    settings = get_settings()
    provider = LLMProvider(
        name=req.name,
        style=req.style,
        api_key=req.api_key,
        base_url=req.base_url,
        models=[
            ModelConfig(
                name=m.name,
                context_limit=m.context_limit if m.context_limit is not None else settings.default_context_limit,
            )
            for m in req.models
        ],
        default_model=req.default_model,
        timeout_sec=req.timeout_sec or settings.default_llm_timeout_sec,
    )
    try:
        registry.register(provider)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, {"code": "REGISTER_FAILED", "message": str(e)})
    return _to_response(provider)


@router.get("", response_model=list[LLMProviderResponse])
def list_providers() -> list[LLMProviderResponse]:
    return [_to_response(p) for p in get_llm_registry().list_providers()]


@router.get("/{name}", response_model=LLMProviderResponse)
def get_provider(name: str) -> LLMProviderResponse:
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})
    return _to_response(registry.get_provider(name))


@router.delete("/{name}", status_code=204)
def delete_provider(name: str) -> None:
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})
    registry.delete(name)


# ── 已注册 Provider 的连通性验证 ─────────────────────────────────────────────

@router.post("/{name}/ping", response_model=PingLLMResponse)
def ping_registered_provider(name: str, model: str = "") -> PingLLMResponse:
    from app.llm.anthropic_adapter import AnthropicAdapter
    from app.llm.openai_adapter import OpenAIAdapter
    from app.llm.transport_httpx import HttpxTransport
    from app.llm.types import LLMMessage, LLMRequest

    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})

    provider = registry.get_provider(name)
    transport = HttpxTransport(timeout=15)
    base_url = provider.base_url or (
        "https://api.anthropic.com" if provider.style == "anthropic" else "https://api.openai.com"
    )
    model = model or provider.default_model or (provider.models[0].name if provider.models else (
        "claude-haiku-4-5-20251001" if provider.style == "anthropic" else "gpt-4o-mini"
    ))

    if provider.style == "openai":
        adapter = OpenAIAdapter(provider.api_key, base_url, transport, timeout_sec=15)
    else:
        adapter = AnthropicAdapter(provider.api_key, base_url, transport, timeout_sec=15)

    ping_req = LLMRequest(
        model=model,
        messages=[LLMMessage(role="user", content="hi")],
        max_tokens=1,
    )

    t0 = time.monotonic()
    try:
        _ping_via_stream(adapter, ping_req)
    except RuntimeError as exc:
        raise HTTPException(422, {"code": "PING_FAILED", "message": str(exc)})
    latency_ms = int((time.monotonic() - t0) * 1000)

    return PingLLMResponse(ok=True, latency_ms=latency_ms)


# ── 已注册 Provider 的可用模型列举 ───────────────────────────────────────────

@router.get("/{name}/available-models", response_model=AvailableModelsResponse)
def list_available_models_registered(name: str) -> AvailableModelsResponse:
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})
    provider = registry.get_provider(name)
    try:
        models = _fetch_available_models(provider.style, provider.api_key, provider.base_url)
    except RuntimeError as exc:
        raise HTTPException(422, {"code": "LIST_MODELS_FAILED", "message": str(exc)})
    return AvailableModelsResponse(models=models)


# ── 模型管理 ──────────────────────────────────────────────────────────────────

@router.post("/{name}/models", response_model=LLMProviderResponse)
def add_model(name: str, req: AddModelRequest) -> LLMProviderResponse:
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})
    provider = registry.add_model(name, req.model, context_limit=req.context_limit)
    return _to_response(provider)


@router.delete("/{name}/models", response_model=LLMProviderResponse)
def remove_model(name: str, req: AddModelRequest) -> LLMProviderResponse:
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})
    provider = registry.remove_model(name, req.model)
    return _to_response(provider)


@router.put("/{name}/default_model", response_model=LLMProviderResponse)
def set_default_model(name: str, req: SetDefaultModelRequest) -> LLMProviderResponse:
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})
    try:
        provider = registry.set_default_model(name, req.model)
    except ValueError as e:
        raise HTTPException(400, {"code": "INVALID_MODEL", "message": str(e)})
    return _to_response(provider)
