"""Task 领域模型（Phase 1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """任务领域对象。

    状态流转：PENDING → ACTIVE → FINISHED / FAILED / CANCELED
                       ↕
                    SUSPENDED  （agent 调用 spawn_agents 后挂起）
    """
    id: str
    session_id: str
    creator_agent_id: str              # 产生该任务的 agent（不可变）
    assigned_agent_id: str             # 被分配执行该任务的 agent（auto-spawn 时可更新）
    status: str                        # PENDING | ACTIVE | SUSPENDED | TO_BE_OBSERVED | FINISHED | FAILED | CANCELED

    user_prompt: str | list            # 用户输入，str 纯文本或 list[ContentPart dict] 多模态
    conversation_turns: list[dict[str, Any]] = field(default_factory=list) # 任务相关的对话历史（agent 内部维护，非必需）

    title: str = ""                    # 简短描述，供 Agent 识别和展示用, 可为空，等待 llm 补全
    description: str = ""              # 详细描述，供 Agent 识别和展示用, 可为空，等待 llm 补全
    settings: dict[str, Any] = field(default_factory=dict) # 任务配置项（如 skill_name、use_subagent 等，Agent 执行时参考）
    progress_text: str = ""            # 任务执行中的进度描述，供 Agent 内部维护和展示用
    result: str | None = None          # 结果文本
    outputs: dict[str, Any] = field(default_factory=dict) # 若需要输出结构化文本，可用此字段存储
    error: str | None = None           # 若任务失败，存储错误信息

    # DAG & spawn 字段（仅 sub-task 填充）
    dag_deps: list[str] = field(default_factory=list)   # 依赖的 task_id 列表
    parent_task_id: str | None = None                   # 所属 SUSPENDED 祖先 task
    retry_count: int = 0

    created_at: str = ""
    updated_at: str = ""

    # ── 运行时临时字段（不持久化，handler 直接写，loop 直接读）──────────────────
    actor_done: bool = field(default=False, compare=False)
    actor_outcome: str = field(default="", compare=False)
    actor_result: str = field(default="", compare=False)
    actor_summary: str = field(default="", compare=False)
    proceed_to_review: bool = field(default=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "creator_agent_id": self.creator_agent_id,
            "assigned_agent_id": self.assigned_agent_id,
            "user_prompt": self.user_prompt,
            "title": self.title,
            "status": self.status,
            "description": self.description,
            "settings": self.settings,
            "result": self.result,
            "outputs": self.outputs,
            "error": self.error,
            "dag_deps": self.dag_deps,
            "parent_task_id": self.parent_task_id,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            id=d["id"],
            session_id=d["session_id"],
            creator_agent_id=d["creator_agent_id"],
            assigned_agent_id=d["assigned_agent_id"],
            user_prompt=d["user_prompt"],
            title=d["title"],
            status=d["status"],
            description=d.get("description", ""),
            settings=d.get("settings", {}),
            result=d.get("result"),
            outputs=d.get("outputs", {}),
            error=d.get("error"),
            dag_deps=d.get("dag_deps", []),
            parent_task_id=d.get("parent_task_id"),
            retry_count=d.get("retry_count", 0),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
