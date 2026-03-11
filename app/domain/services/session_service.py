"""Session 领域服务。"""


class SessionService:
    """会话领域服务。"""

    def create_session(self, task_id: str) -> str:
        """创建会话并返回 session_id（TODO：接入 SessionRepo）。"""
        # TODO: 校验 task_id 存在。
        # TODO: 写入 sessions 表并入队。
        raise NotImplementedError('SessionService.create_session 未实现')
