"""LLM Provider 管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.schemas.llm import (
    AddModelRequest,
    LLMProviderResponse,
    RegisterLLMRequest,
    SetDefaultModelRequest,
)
from app.config.settings import get_settings
from app.llm.registry import LLMProvider, SUPPORTED_LLM_STYLES, get_llm_registry

router = APIRouter()


def _to_response(p: LLMProvider) -> LLMProviderResponse:
    return LLMProviderResponse(
        name=p.name,
        style=p.style,
        base_url=p.base_url,
        models=p.models,
        default_model=p.default_model,
        timeout_sec=p.timeout_sec
    )


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
        models=req.models,
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


# ── 模型管理 ──────────────────────────────────────────────────────────────────

@router.post("/{name}/models", response_model=LLMProviderResponse)
def add_model(name: str, req: AddModelRequest) -> LLMProviderResponse:
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(404, {"code": "LLM_NOT_FOUND", "message": f"Provider '{name}' not found"})
    provider = registry.add_model(name, req.model)
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
