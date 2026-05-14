"""MCP Server 管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.schemas.mcp import MCPHttpRegisterRequest, MCPServerResponse, MCPStdioRegisterRequest, MCPToolResponse
from app.common.errors import AppError
from app.api.v1.deps import get_mcp_service
from app.domain.services.mcp_service import MCPServerInfo

router = APIRouter()


def _to_response(info: MCPServerInfo) -> MCPServerResponse:
    tools = [MCPToolResponse(name=t.name, description=t.description) for t in info.tools]
    return MCPServerResponse(
        name=info.name,
        type=info.type,
        status=info.status,
        tool_count=len(tools),
        tools=tools,
        command=info.command,
        args=info.args,
        url=info.url,
        timeout=info.timeout,
        connect_timeout=info.connect_timeout,
    )


@router.post("/stdio", response_model=MCPServerResponse, status_code=201)
def register_stdio(req: MCPStdioRegisterRequest) -> MCPServerResponse:
    """注册 stdio 类型 MCP Server。"""
    try:
        info = get_mcp_service().register_stdio(
            name=req.name,
            command=req.command,
            args=req.args or [],
            env=req.env or None,
            timeout=req.timeout,
            connect_timeout=req.connect_timeout,
        )
    except AppError as e:
        status = 409 if e.code == "MCP_ALREADY_EXISTS" else 500
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})
    return _to_response(info)


@router.post("/http", response_model=MCPServerResponse, status_code=201)
def register_http(req: MCPHttpRegisterRequest) -> MCPServerResponse:
    """注册 HTTP 类型 MCP Server。"""
    try:
        info = get_mcp_service().register_http(
            name=req.name,
            url=req.url,
            timeout=req.timeout,
            connect_timeout=req.connect_timeout,
        )
    except AppError as e:
        status = {
            "MCP_ALREADY_EXISTS": 409,
            "MCP_CONNECT_CANCELLED": 502,
            "MCP_CONNECT_TIMEOUT": 504,
        }.get(e.code, 500)
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})
    return _to_response(info)


@router.get("", response_model=list[MCPServerResponse])
def list_mcp_servers() -> list[MCPServerResponse]:
    """列出所有已注册的 MCP Server。"""
    return [_to_response(info) for info in get_mcp_service().list_all()]


@router.get("/{name}", response_model=MCPServerResponse)
def get_mcp_server(name: str) -> MCPServerResponse:
    """获取指定 MCP Server 信息。"""
    try:
        return _to_response(get_mcp_service().get(name))
    except AppError as e:
        raise HTTPException(status_code=404, detail={"code": e.code, "message": e.message})


@router.delete("/{name}", status_code=204)
def delete_mcp_server(name: str) -> None:
    """停止并删除 MCP Server（同时删除持久化配置）。"""
    try:
        get_mcp_service().delete(name)
    except AppError as e:
        raise HTTPException(status_code=404, detail={"code": e.code, "message": e.message})


@router.post("/{name}/refresh", response_model=MCPServerResponse)
def refresh_mcp_server(name: str) -> MCPServerResponse:
    """重新拉取 MCP Server 工具列表，同步变更到外部工具存储。"""
    try:
        return _to_response(get_mcp_service().refresh(name))
    except AppError as e:
        status = 404 if e.code in ("MCP_NOT_FOUND", "MCP_INVALID_CONFIG") else 500
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})
