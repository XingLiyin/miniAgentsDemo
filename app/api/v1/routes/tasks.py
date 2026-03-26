"""Task 相关路由（Phase 1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.v1.deps import get_task_service
from app.api.v1.schemas.task import TaskResponse
from app.common.errors import AppError

router = APIRouter()


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str) -> TaskResponse:
    """获取任务详情。"""
    try:
        svc = get_task_service()
        task = svc.get(task_id)
        return TaskResponse(**task.to_dict())
    except AppError as e:
        status = 404 if e.code == "TASK_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail={"code": e.code, "message": e.message})
