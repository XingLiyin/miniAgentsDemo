"""MCP Server 管理 API Schema。"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class MCPStdioRegisterRequest(BaseModel):
    name: str = Field(..., description="MCP server 唯一标识名")
    command: str = Field(..., description="可执行文件，如 'npx' 或 'python'")
    args: list[str] = Field(default_factory=list, description="命令参数列表")
    env: dict[str, str] = Field(default_factory=dict, description="额外环境变量")
    timeout: int = Field(default=30, ge=1, description="工具调用超时秒数")
    connect_timeout: int = Field(default=5, ge=1, description="连接握手超时秒数")


class MCPHttpRegisterRequest(BaseModel):
    name: str = Field(..., description="MCP server 唯一标识名")
    url: str = Field(..., description="MCP Server 的 HTTP 端点，如 'http://my-server/mcp'")
    timeout: int = Field(default=30, ge=1, description="工具调用超时秒数")
    connect_timeout: int = Field(default=5, ge=1, description="连接握手超时秒数")


class MCPToolResponse(BaseModel):
    name: str
    description: str


class MCPServerResponse(BaseModel):
    name: str
    type: str                           # "stdio" | "http"
    status: str = "DISCONNECTED"        # "CONNECTED" | "DISCONNECTED"
    tool_count: int = 0
    tools: list[MCPToolResponse] = Field(default_factory=list)
    # stdio 字段
    command: Optional[str] = None
    args: Optional[list[str]] = None
    # http 字段
    url: Optional[str] = None
    timeout: Optional[int] = None
    # 通用
    connect_timeout: Optional[int] = None
