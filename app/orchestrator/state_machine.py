"""状态机抽象。"""


class StateMachine:
    """会话/任务状态机。"""

    def transition(self, entity_id: str, to_state: str) -> None:
        """状态迁移（TODO：校验合法转移并记录事件）。"""
        # TODO: 校验状态转移合法性。
        # TODO: 写入 session_events。
        raise NotImplementedError('StateMachine.transition 未实现')
