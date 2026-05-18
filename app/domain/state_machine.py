"""状态机：校验 Session / Task / Agent 合法状态转换。"""

from __future__ import annotations

from app.common.errors import AppError

# 合法的状态转换表
_SESSION_TRANSITIONS: dict[str, set[str]] = {
    "QUEUED":         {"RUNNING", "CANCELED", "INTERRUPTED"},
    "RUNNING":        {"SUCCEEDED", "FAILED", "CANCELED", "WAITING_INPUT", "INTERRUPTED"},
    "WAITING_INPUT":  {"RUNNING", "QUEUED", "CANCELED", "INTERRUPTED"},
    "SUCCEEDED":      {"QUEUED"},   # user sends follow-up message → continue
    "FAILED":         {"QUEUED"},   # user retries after failure
    "INTERRUPTED":    {"QUEUED"},   # user sends new prompt → resume
    "CANCELED":       set(),
}

_TASK_TRANSITIONS: dict[str, set[str]] = {
    "PENDING":          {"ACTIVE", "CANCELED", "FINISHED", "TO_BE_OBSERVED"},
    "ACTIVE":           {"FINISHED", "FAILED", "CANCELED", "SUSPENDED", "TO_BE_OBSERVED"},
    "SUSPENDED":        {"ACTIVE", "PENDING", "FAILED", "CANCELED"},
    "TO_BE_OBSERVED":   {"SUSPENDED", "FINISHED", "FAILED", "PENDING"},
    "FINISHED":         {"PENDING"},   # Observer 复核不通过时 reopen
    "FAILED":           {"PENDING"},   # LifecycleManager 重试时 retry
    "CANCELED":         set(),
}

_AGENT_TRANSITIONS: dict[str, set[str]] = {
    "IDLE":     {"RUNNING"},
    "RUNNING":  {"WAITING", "FINISHED", "FAILED"},
    "WAITING":  {"RUNNING", "FAILED"},
    "FINISHED": set(),
    "FAILED":   set(),
}


class SessionStateMachine:
    def validate_session(self, from_status: str, to_status: str) -> None:
        allowed = _SESSION_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise AppError(
                "INVALID_STATE_TRANSITION",
                f"Session: {from_status} → {to_status} is not allowed",
            )


class TaskStateMachine:
    def validate_task(self, from_status: str, to_status: str) -> None:
        allowed = _TASK_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise AppError(
                "INVALID_STATE_TRANSITION",
                f"Task: {from_status} → {to_status} is not allowed",
            )


class AgentStateMachine:
    def validate_agent(self, from_status: str, to_status: str) -> None:
        allowed = _AGENT_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise AppError(
                "INVALID_STATE_TRANSITION",
                f"Agent: {from_status} → {to_status} is not allowed",
            )
