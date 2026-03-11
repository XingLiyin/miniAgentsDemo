"""Task 领域模型。"""

from typing import Any, Dict, Optional


class Task:
    """任务领域对象。"""

    def __init__(self, task_id: str, title: str, status: str) -> None:
        """创建任务对象，最小字段为 id/title/status。"""
        self.id = task_id
        self.title = title
        self.status = status
        self.description: Optional[str] = None
        self.type: Optional[str] = None
        self.assigned_agent_id: Optional[str] = None
        self.executor_type: Optional[str] = None
        self.inputs: Optional[Dict[str, Any]] = None
        self.outputs: Optional[Dict[str, Any]] = None
        self.result: Optional[Any] = None
        self.error: Optional[str] = None
