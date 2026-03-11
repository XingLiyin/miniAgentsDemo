"""任务管理编排器。"""


class TaskManager:
    """任务管理编排。"""

    def add_task(self, session_id: str, task_id: str) -> None:
        """将任务加入会话（TODO：维护 task_queue 与依赖）。"""
        # TODO: 写入 session_task_queue。
        # TODO: 处理 Task DAG 依赖。
        raise NotImplementedError('TaskManager.add_task 未实现')
