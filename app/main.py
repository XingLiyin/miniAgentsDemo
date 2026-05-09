"""应用入口：创建 FastAPI 实例并挂载路由（Phase 1）。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.common.errors import AppError
from app.config.settings import get_settings
from app.observability.logging import init_logging


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Capture the main event loop so worker threads can schedule AF coroutines
    # on it (instead of creating a new loop that breaks httpx async clients).
    import asyncio as _asyncio
    from app.common.async_utils import set_main_loop
    set_main_loop(_asyncio.get_running_loop())

    # 应用启动时从持久化配置恢复 MCP Server
    from app.api.v1.deps import get_mcp_service
    get_mcp_service().restore_all()
    # 恢复远端 skill 来源（须在 MCP restore 之后，依赖事件循环已就绪）
    from app.api.v1.deps import get_remote_skill_source_service
    get_remote_skill_source_service().restore_all()
    # 扫描 agents_dir，自动加载 Agent 定义文件
    from app.api.v1.deps import get_agent_template_registry
    get_agent_template_registry()
    yield
    # 应用关闭时停止所有 MCP Provider
    from app.api.v1.deps import get_tool_registry
    get_tool_registry().shutdown()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    settings = get_settings()
    init_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="miniAgents Phase 1 — Single Agent Loop with file-based storage",
        lifespan=_lifespan,
    )

    # 全局错误处理
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(NotImplementedError)
    async def not_implemented_handler(request: Request, exc: NotImplementedError) -> JSONResponse:
        return JSONResponse(
            status_code=501,
            content={"code": "NOT_IMPLEMENTED", "message": str(exc)},
        )

    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": settings.app_version}

    return app


app = create_app()
