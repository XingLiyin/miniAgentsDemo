"""Session API Schema 定义。"""

from typing import Optional

from pydantic import BaseModel


class SessionObject(BaseModel):
    """会话对象的 API Schema。"""

    id: str
    task_id: str
    status: str
    goal: Optional[str] = None
    priority: Optional[int] = None
    deadline_at: Optional[str] = None
