"""会话任务队列抽象。"""


class TaskQueue:
    """会话任务队列。"""

    def enqueue(self, session_id: str, task_id: str) -> None:
        """入队任务（TODO：持久化与幂等处理）。"""
        # TODO: 幂等插入 session_task_queue。
        raise NotImplementedError('TaskQueue.enqueue 未实现')
