"""Agent 领域模型。"""


class Agent:
    """Agent 领域对象。"""

    def __init__(self, agent_id: str, name: str) -> None:
        """创建 Agent 对象。"""
        self.id = agent_id
        self.name = name
