"""记忆与上下文相关路由。"""

from fastapi import APIRouter

from app.api.v1.schemas.memory import ContextObject

router = APIRouter()


@router.get('/context', response_model=ContextObject)
def get_context() -> ContextObject:
    """获取拼装上下文（TODO：接入 MemoryService）。"""
    # TODO: 读取 session_id/task_id 与 max_tokens。
    # TODO: 调用 MemoryService.build_context 并返回。
    raise NotImplementedError('get_context 未实现')
