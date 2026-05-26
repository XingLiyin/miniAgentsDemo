"""PolicyRule 基类与内置规则。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.common.errors import AppError

if TYPE_CHECKING:
    from app.domain.models.agent import AgentCapability
    from app.tools.types import CallContext


class PolicyRule(ABC):
    @abstractmethod
    def check(
        self,
        capability: "AgentCapability",
        tool_name: str,
        arguments: dict,
        ctx: "CallContext | None",
    ) -> None:
        """校验失败时 raise AppError。"""
        ...


class GlobalRule(PolicyRule, ABC):
    """作用于所有工具。"""


class ToolRule(PolicyRule, ABC):
    """作用于指定工具集合。"""

    def __init__(self, *tools: str) -> None:
        self._tools: frozenset[str] = frozenset(tools)


# ── 内置规则 ──────────────────────────────────────────────────────────────


class WhitelistRule(GlobalRule):
    """白名单校验：工具必须已注册 + agent 有权限调用。"""

    def __init__(self, tool_registry: "ToolRegistry") -> None:  # type: ignore[name-defined]
        self._registry = tool_registry

    def check(self, capability: "AgentCapability", tool_name: str, arguments: dict, ctx: "CallContext | None") -> None:
        if not self._registry.is_registered(tool_name):
            raise AppError("TOOL_NOT_FOUND", f"Tool '{tool_name}' is not a registered tool")

        if tool_name in capability.tools:
            return
        for server_name in (capability.mcp_servers or []):
            if tool_name in self._registry.get_server_tool_names(server_name):
                return

        agent_id = ctx.agent_id if ctx else "unknown"
        raise AppError("TOOL_NOT_AUTHORIZED", f"Agent '{agent_id}' is not authorized to use tool '{tool_name}'")


class BashExecGuardRule(ToolRule):
    """bash_exec 专用守卫：HITL 用户审批。

    每次执行前通过 SSE 推送 bash_exec_confirm 事件，阻塞等待用户明确批准。
    """

    def __init__(self, session_svc: "SessionService") -> None:  # type: ignore[name-defined]
        super().__init__("bash_exec")
        self._session_svc = session_svc

    def check(self, capability: "AgentCapability", tool_name: str, arguments: dict, ctx: "CallContext | None") -> None:
        command = arguments.get("command", "").strip()

        # HITL 用户审批
        session_id = ctx.session_id if ctx else ""
        agent_id   = ctx.agent_id   if ctx else ""
        if not session_id:
            return  # 无 session 上下文时跳过（测试场景）

        from app.common.sse_bus import get_sse_bus
        from app.common.utils import now_iso
        from app.storage.file.hitl_store import get_hitl_store

        prompt = f"Agent 请求执行以下命令，是否允许？\n\n```\n{command}\n```"
        self._session_svc.transition(session_id, "WAITING_INPUT")
        try:
            get_sse_bus().push(session_id, {
                "type": "bash_exec_confirm",
                "command": command,
                "prompt": prompt,
                "task_title": "确认执行命令",
                "created_at": now_iso(),
            })
        except Exception:
            pass

        answer = get_hitl_store().wait(session_id, agent_id, prompt, "bash_exec_confirm")
        self._session_svc.transition(session_id, "RUNNING")

        if answer.strip().lower() != "approved":
            raise AppError(
                "TOOL_EXECUTION_REJECTED",
                f"User rejected bash_exec: {answer or 'no reason given'}",
            )
