"""远端 Skill 来源管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.schemas.remote_skill_source import (
    RegisterHttpSourceRequest,
    RegisterStdioSourceRequest,
    RemoteSkillSourceResponse,
)
from app.common.errors import AppError
from app.skills.definition import RemoteSkillSourceConfig
from app.api.v1.deps import get_remote_skill_source_service

router = APIRouter()

_ERROR_STATUS = {
    "SKILL_SOURCE_ALREADY_EXISTS": 409,
    "SKILL_SOURCE_NOT_FOUND": 404,
    "SKILL_SOURCE_MISSING_TOOLS": 422,
    "SKILL_SOURCE_VALIDATION_FAILED": 502,
    "INVALID_MCP_TYPE": 400,
    "MCP_CONNECT_CANCELLED": 502,
    "MCP_CONNECT_TIMEOUT": 504,
}


def _dict_to_response(data: dict) -> RemoteSkillSourceResponse:
    return RemoteSkillSourceResponse(
        source_name=data["source_name"],
        mcp_type=data["mcp_type"],
        mcp_tool_list_skills=data.get("mcp_tool_list_skills", "listSkills"),
        mcp_tool_load_skill_md=data.get("mcp_tool_load_skill_md", "loadSkillMd"),
        mcp_tool_get_skill_files=data.get("mcp_tool_get_skill_files", "getSkillFiles"),
        mcp_tool_load_skill_reference=data.get("mcp_tool_load_skill_reference", "loadSkillReference"),
        mcp_tool_exec_skill_script=data.get("mcp_tool_exec_skill_script", "execSkillScript"),
        mcp_url=data.get("mcp_url"),
        mcp_timeout=data.get("mcp_timeout"),
        mcp_command=data.get("mcp_command"),
        mcp_args=data.get("mcp_args"),
        mcp_env=data.get("mcp_env"),
    )


def _base_config_kwargs(req: RegisterHttpSourceRequest | RegisterStdioSourceRequest) -> dict:
    return {
        "source_name": req.source_name,
        "mcp_tool_list_skills": req.mcp_tool_list_skills,
        "mcp_tool_load_skill_md": req.mcp_tool_load_skill_md,
        "mcp_tool_get_skill_files": req.mcp_tool_get_skill_files,
        "mcp_tool_load_skill_reference": req.mcp_tool_load_skill_reference,
        "mcp_tool_exec_skill_script": req.mcp_tool_exec_skill_script,
    }


@router.post("/http", response_model=RemoteSkillSourceResponse, status_code=201)
def register_http_source(req: RegisterHttpSourceRequest) -> RemoteSkillSourceResponse:
    """注册 HTTP 类型远端 skill 来源。注册时执行两步校验。"""
    config = RemoteSkillSourceConfig(
        mcp_type="http",
        mcp_url=req.mcp_url,
        mcp_timeout=req.mcp_timeout,
        **_base_config_kwargs(req),
    )
    try:
        data = get_remote_skill_source_service().register(config)
    except AppError as e:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(e.code, 500),
            detail={"code": e.code, "message": e.message},
        )
    return _dict_to_response(data)


@router.post("/stdio", response_model=RemoteSkillSourceResponse, status_code=201)
def register_stdio_source(req: RegisterStdioSourceRequest) -> RemoteSkillSourceResponse:
    """注册 stdio 类型远端 skill 来源。注册时执行两步校验。"""
    config = RemoteSkillSourceConfig(
        mcp_type="stdio",
        mcp_command=req.mcp_command,
        mcp_args=req.mcp_args,
        mcp_env=req.mcp_env,
        **_base_config_kwargs(req),
    )
    try:
        data = get_remote_skill_source_service().register(config)
    except AppError as e:
        raise HTTPException(
            status_code=_ERROR_STATUS.get(e.code, 500),
            detail={"code": e.code, "message": e.message},
        )
    return _dict_to_response(data)


@router.get("", response_model=list[RemoteSkillSourceResponse])
def list_sources() -> list[RemoteSkillSourceResponse]:
    """列出所有已注册的远端 skill 来源。"""
    return [_dict_to_response(d) for d in get_remote_skill_source_service().list_all()]


@router.get("/{source_name}", response_model=RemoteSkillSourceResponse)
def get_source(source_name: str) -> RemoteSkillSourceResponse:
    """获取指定远端 skill 来源信息。"""
    try:
        return _dict_to_response(get_remote_skill_source_service().get(source_name))
    except AppError as e:
        raise HTTPException(status_code=404, detail={"code": e.code, "message": e.message})


@router.delete("/{source_name}", status_code=204)
def delete_source(source_name: str) -> None:
    """注销远端 skill 来源，断开连接并删除持久化配置。"""
    try:
        get_remote_skill_source_service().delete(source_name)
    except AppError as e:
        raise HTTPException(status_code=404, detail={"code": e.code, "message": e.message})
