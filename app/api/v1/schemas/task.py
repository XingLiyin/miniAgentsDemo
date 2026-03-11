"""Task API Schema 定义。"""

from typing import Any, Dict, Optional

from pydantic import BaseModel


class TaskObject(BaseModel):
    """任务对象的 API Schema。"""

    id: str
    title: str
    status: str
    description: Optional[str] = None
    type: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    executor_type: Optional[str] = None
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    priority: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
