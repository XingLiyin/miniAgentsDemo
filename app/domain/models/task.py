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
    trackers: list[str] = field(default_factory=list)  # 订阅该任务 blackboard 的 agent id 列表
    conversation_turns: list[dict[str, Any]] = field(default_factory=list) # 任务相关的对话历史（agent 内部维护，非必需）

    title: str = ""                    # 简短描述，供 Agent 识别和展示用, 可为空，等待 llm 补全
    description: str = ""              # 详细描述，供 Agent 识别和展示用, 可为空，等待 llm 补全
    settings: dict[str, Any] = field(default_factory=dict) # 任务配置项（如 skill_name、use_subagent 等，Agent 执行时参考）
    user_prompt_in_memory: bool = False  # user_prompt 是否已写入 memory（防 resume 重复写）
    process_report: str | None = None    # observer 观察到的该任务执行过程的文本报告（如工具调用记录、子 Agent 执行记录等）
    outputs: str | list = ""             # actor 最终输出（文本或含图片的 multimodal list）
    error: str | None = None           # 若任务失败，存储错误信息
    pending_user_answer: str | None = None  # HITL 确认回答，暂存待 write_execution_memory 时作为 user 消息追加（保证排在 LLM 判断之后）

    # DAG & spawn 字段（仅 sub-task 填充）
    dag_deps: list[str] = field(default_factory=list)   # 依赖的 task_id 列表
    parent_task_id: str | None = None                   # 所属 SUSPENDED 祖先 task
    retry_count: int = 0

    created_at: str = ""
    updated_at: str = ""

    # ── 运行时临时字段（不持久化，handler 直接写，loop 直接读）──────────────────
    actor_done: bool = field(default=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "creator_agent_id": self.creator_agent_id,
            "assigned_agent_id": self.assigned_agent_id,
            "trackers": self.trackers,
            "user_prompt": self.user_prompt,
            "title": self.title,
            "status": self.status,
            "description": self.description,
            "settings": self.settings,
            "user_prompt_in_memory": self.user_prompt_in_memory,
            "process_report": self.process_report,
            "outputs": self.outputs,
            "error": self.error,
            "pending_user_answer": self.pending_user_answer,
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
            trackers=d.get("trackers", []),
            user_prompt=d["user_prompt"],
            title=d["title"],
            status=d["status"],
            description=d.get("description", ""),
            settings=d.get("settings", {}),
            user_prompt_in_memory=d.get("user_prompt_in_memory", False),
            process_report=d.get("process_report"),
            outputs=d.get("outputs", ""),
            error=d.get("error"),
            pending_user_answer=d.get("pending_user_answer"),
            dag_deps=d.get("dag_deps", []),
            parent_task_id=d.get("parent_task_id"),
            retry_count=d.get("retry_count", 0),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )
