"""Blackboard 领域模型。"""


class BlackboardItem:
    """黑板条目领域对象。"""

    def __init__(self, item_id: str, scope: str, item_type: str) -> None:
        """创建黑板条目对象。"""
        self.id = item_id
        self.scope = scope
        self.type = item_type
