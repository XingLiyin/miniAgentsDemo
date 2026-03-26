"""应用入口：创建 FastAPI 实例并挂载路由（Phase 1）。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.common.errors import AppError
from app.config.settings import get_settings
from app.observability.logging import init_logging


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    settings = get_settings()
    init_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="miniAgents Phase 1 — Single Agent Loop with file-based storage",
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
