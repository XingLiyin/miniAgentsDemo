"""会话相关路由。"""

from fastapi import APIRouter

from app.api.v1.schemas.session import SessionObject

router = APIRouter()


@router.post('', response_model=SessionObject)
def create_session() -> SessionObject:
    """创建会话（TODO：接入 SessionService 并处理幂等）。"""
    # TODO: 读取 Idempotency-Key，避免重复入队。
    # TODO: 调用 SessionService.create_session 并入队。
    raise NotImplementedError('create_session 未实现')


@router.get('/{session_id}', response_model=SessionObject)
def get_session(session_id: str) -> SessionObject:
    """获取会话详情（TODO：接入 SessionRepo）。"""
    # TODO: 校验 session_id 格式。
    # TODO: 调用 SessionRepo.get，未找到返回 404。
    raise NotImplementedError('get_session 未实现')
