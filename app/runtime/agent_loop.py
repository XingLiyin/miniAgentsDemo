"""Agent 运行循环。"""


class AgentLoop:
    """Agent Loop 执行器。"""

    def run_once(self, session_id: str, task_id: str, agent_id: str) -> None:
        """执行一次 Loop（TODO：Observe/Plan/CreateTask/Schedule/Execute/UpdateMemory）。"""
        # TODO: 构建上下文。
        # TODO: 生成计划并创建任务。
        # TODO: 调度与执行任务。
        # TODO: 更新记忆与事件。
        raise NotImplementedError('AgentLoop.run_once 未实现')
