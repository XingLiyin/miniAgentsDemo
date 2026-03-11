"""任务执行器。"""


class TaskExecutor:
    """按类型执行单个任务。"""

    def execute(self, task_id: str) -> None:
        """执行任务（TODO：根据 type 调用 Tool/LLM/子 Agent）。"""
        # TODO: 读取 task。
        # TODO: 分派到 reasoning/tool-call/sub-agent/skill 执行器。
        # TODO: 持久化 outputs/result/error。
        raise NotImplementedError('TaskExecutor.execute 未实现')
