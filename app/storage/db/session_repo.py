"""Session 持久化仓储。"""


class SessionRepo:
    """会话仓储。"""

    def get(self, session_id: str) -> dict:
        """按 id 获取会话（TODO：查询数据库）。"""
        raise NotImplementedError('SessionRepo.get 未实现')
