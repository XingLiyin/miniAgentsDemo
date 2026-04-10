"""LLM Provider 管理路由（Phase 1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.schemas.llm import LLMRegisterRequest, LLMRegisterResponse
from app.llm.registry import SUPPORTED_LLM_STYLES
from app.llm.registry import LLMProviderConfig, get_llm_registry

router = APIRouter()


@router.post("", response_model=LLMRegisterResponse, status_code=201)
def register_llm(req: LLMRegisterRequest) -> LLMRegisterResponse:
    """注册 LLM Provider（持久化到 data/llm_configs/）。"""
    registry = get_llm_registry()
    if registry.is_registered(req.name):
        raise HTTPException(status_code=409, detail={"code": "LLM_ALREADY_EXISTS", "message": f"LLM '{req.name}' already registered"})
    if req.style not in SUPPORTED_LLM_STYLES:
        raise HTTPException(status_code=400, detail={"code": "INVALID_LLM_STYLE", "message": f"Unsupported LLM style: {req.style}"})
    from app.config.settings import get_settings
    timeout_sec = req.timeout_sec or get_settings().default_llm_timeout_sec
    config = LLMProviderConfig(
        name=req.name,
        style=req.style,
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
        timeout_sec=timeout_sec,
    )
    try:
        registry.register(config)
    except KeyError as e:
        raise HTTPException(status_code=409, detail={"code": "LLM_ALREADY_EXISTS", "message": str(e)})
    return LLMRegisterResponse(
        name=config.name,
        style=config.style,
        base_url=config.base_url,
        model=config.model,
        timeout_sec=config.timeout_sec,
    )


# 保持旧路径兼容
@router.post("/register", response_model=LLMRegisterResponse, include_in_schema=False)
def register_llm_compat(req: LLMRegisterRequest) -> LLMRegisterResponse:
    return register_llm(req)


@router.get("", response_model=list[LLMRegisterResponse])
def list_llms() -> list[LLMRegisterResponse]:
    """列出所有已注册的 LLM Provider（不含 api_key）。"""
    registry = get_llm_registry()
    return [
        LLMRegisterResponse(
            name=c.name,
            style=c.style,
            base_url=c.base_url,
            model=c.model,
            timeout_sec=c.timeout_sec,
        )
        for c in registry.list_configs()
    ]


@router.get("/{name}", response_model=LLMRegisterResponse)
def get_llm(name: str) -> LLMRegisterResponse:
    """获取指定 LLM Provider 信息（不含 api_key）。"""
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(status_code=404, detail={"code": "LLM_NOT_FOUND", "message": f"LLM '{name}' not found"})
    c = registry.get_config(name)
    return LLMRegisterResponse(name=c.name, style=c.style, base_url=c.base_url, model=c.model, timeout_sec=c.timeout_sec)


@router.delete("/{name}", status_code=204)
def delete_llm(name: str) -> None:
    """删除 LLM Provider（同时删除持久化文件）。"""
    registry = get_llm_registry()
    if not registry.is_registered(name):
        raise HTTPException(status_code=404, detail={"code": "LLM_NOT_FOUND", "message": f"LLM '{name}' not found"})
    registry.delete(name)
