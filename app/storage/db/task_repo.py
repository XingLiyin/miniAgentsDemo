"""Task 持久化仓储。"""


class TaskRepo:
    """任务仓储。"""

    def get(self, task_id: str) -> dict:
        """按 id 获取任务（TODO：查询数据库）。"""
        raise NotImplementedError('TaskRepo.get 未实现')
