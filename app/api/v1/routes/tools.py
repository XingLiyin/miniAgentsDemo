"""工具调用审计相关路由。"""

from fastapi import APIRouter

from app.api.v1.schemas.tool import ToolCallObject

router = APIRouter()


@router.get('/calls/{tool_call_id}', response_model=ToolCallObject)
def get_tool_call(tool_call_id: str) -> ToolCallObject:
    """获取工具调用详情（TODO：接入 ToolCallRepo）。"""
    # TODO: 校验 tool_call_id。
    # TODO: 调用 ToolCallRepo.get，未找到返回 404。
    raise NotImplementedError('get_tool_call 未实现')
