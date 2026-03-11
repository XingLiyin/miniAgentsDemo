"""Memory 领域模型。"""


class MemoryItem:
    """记忆条目领域对象。"""

    def __init__(self, memory_id: str, memory_type: str, content: str) -> None:
        """创建记忆条目对象。"""
        self.id = memory_id
        self.type = memory_type
        self.content = content
