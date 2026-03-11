"""调度器抽象。"""


class Scheduler:
    """并发与优先级调度器。"""

    def schedule(self, session_id: str) -> None:
        """调度会话内就绪任务（TODO：并发限制与优先级）。"""
        # TODO: 查询就绪任务并按优先级入执行队列。
        raise NotImplementedError('Scheduler.schedule 未实现')
