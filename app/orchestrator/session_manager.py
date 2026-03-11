"""会话管理编排器。"""


class SessionManager:
    """会话生命周期编排。"""

    def enqueue(self, session_id: str) -> None:
        """将会话入队（TODO：写入队列并记录事件）。"""
        # TODO: 写入 session_task_queue 或队列系统。
        # TODO: 记录 SESSION_STATUS_CHANGED 事件。
        raise NotImplementedError('SessionManager.enqueue 未实现')
