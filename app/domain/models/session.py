"""Session 领域模型。"""


class Session:
    """会话领域对象。"""

    def __init__(self, session_id: str, task_id: str, status: str) -> None:
        """创建会话对象。"""
        self.id = session_id
        self.task_id = task_id
        self.status = status
