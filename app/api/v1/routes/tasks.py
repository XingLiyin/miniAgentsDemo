"""任务相关路由。"""

from fastapi import APIRouter

from app.api.v1.schemas.task import TaskObject

router = APIRouter()


@router.post('', response_model=TaskObject)
def create_task() -> TaskObject:
    """创建任务（TODO：接入 TaskService 并校验入参）。"""
    # TODO: 校验标题/优先级/类型/assigned_agent_id。
    # TODO: 调用 TaskService.create_task 并返回 TaskObject。
    raise NotImplementedError('create_task 未实现')


@router.get('/{task_id}', response_model=TaskObject)
def get_task(task_id: str) -> TaskObject:
    """获取任务详情（TODO：接入 TaskRepo）。"""
    # TODO: 校验 task_id 格式。
    # TODO: 调用 TaskRepo.get，未找到返回 404。
    raise NotImplementedError('get_task 未实现')
