"""Blackboard 领域服务。"""


class BlackboardService:
    """黑板领域服务。"""

    def publish(self, task_id: str, item_type: str, content: str) -> str:
        """发布黑板条目（TODO：写入 blackboard_items）。"""
        # TODO: 校验 item_type 与 scope。
        # TODO: 写入 blackboard_items 表。
        raise NotImplementedError('BlackboardService.publish 未实现')
