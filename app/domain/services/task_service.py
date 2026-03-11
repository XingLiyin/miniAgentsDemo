"""Task 领域服务。"""


class TaskService:
    """任务领域服务。"""

    def create_task(self, title: str) -> str:
        """创建任务并返回 task_id（TODO：接入 TaskRepo 与校验）。"""
        # TODO: 校验 title/priority/type。
        # TODO: 写入 tasks 表，返回 task_id。
        raise NotImplementedError('TaskService.create_task 未实现')
