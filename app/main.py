"""应用入口：创建 FastAPI 实例并挂载路由。"""

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.config.settings import get_settings
from app.observability.logging import init_logging


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    settings = get_settings()
    init_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.include_router(api_router, prefix='/api/v1')
    return app


app = create_app()
