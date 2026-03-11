"""Memory 领域服务。"""


class MemoryService:
    """记忆领域服务。"""

    def build_context(self, task_id: str, session_id: str) -> dict:
        """拼装上下文（TODO：读取消息/摘要/黑板并控制 token）。"""
        # TODO: 从 MessageRepo/MemoryRepo/BlackboardRepo 读取数据。
        # TODO: 预算 token 并组装返回。
        raise NotImplementedError('MemoryService.build_context 未实现')
