"""API 路由汇总模块。"""

from fastapi import APIRouter

from app.api.v1.routes import memories, sessions, tasks, tools

api_router = APIRouter()
api_router.include_router(tasks.router, prefix='/tasks', tags=['tasks'])
api_router.include_router(sessions.router, prefix='/sessions', tags=['sessions'])
api_router.include_router(memories.router, prefix='/memories', tags=['memories'])
api_router.include_router(tools.router, prefix='/tools', tags=['tools'])
