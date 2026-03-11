"""Memory 持久化仓储。"""


class MemoryRepo:
    """记忆仓储。"""

    def list_by_task(self, task_id: str) -> list:
        """按 task_id 列出记忆（TODO：查询数据库）。"""
        raise NotImplementedError('MemoryRepo.list_by_task 未实现')
