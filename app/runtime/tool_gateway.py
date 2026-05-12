"""Tool Gateway：统一工具调用入口（外部工具 + 控制工具）。

执行流程：
  call(tool_name, arguments, agent, task_id, ctx)
    ① PolicyEngine.authorize  —— agent 存在时校验白名单
    ② 写审计 RUNNING
    ③ ToolRegistry.get(tool_name).handler(arguments, ctx)
    ④ 写审计 SUCCEEDED / FAILED
    ⑤ 返回 ToolResult
"""

from __future__ import annotations

import logging
from typing import Any

from app.common.errors import AppError
from app.common.utils import new_tool_call_id, now_iso
from app.domain.models.agent import AgentCapability
from app.domain.models.tool_call import ToolCall
from app.runtime.policy_engine import PolicyEngine
from app.storage.file.tool_call_store import ToolCallStore
from app.tools.types import CallContext, ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolGateway:
    """统一工具调用网关：外部工具与控制工具共走同一路径。"""

    def __init__(
        self,
        policy: PolicyEngine,
        tool_registry: ToolRegistry,
        tool_call_store: ToolCallStore,
    ) -> None:
        self._policy = policy
        self._registry = tool_registry
        self._store = tool_call_store

    def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        capability: AgentCapability | None,
        task_id: str,
        ctx: CallContext | None = None,
    ) -> ToolResult:
        """统一入口：授权 → 审计 RUNNING → 执行 → 审计完成 → 返回结果。

        capability 为 None 时跳过授权。
        """
        session_id = ctx.session_id if ctx else ""
        agent_id   = ctx.agent_id   if ctx else ""

        # ① 授权（capability 存在时）
        if capability is not None:
            self._policy.authorize(capability, tool_name, arguments, ctx)

        # ② 写 RUNNING 审计
        call_id    = new_tool_call_id()
        started_at = now_iso()
        self._store.append(session_id, ToolCall(
            id=call_id,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            tool_name=tool_name,
            status="RUNNING",
            arguments=_redact(arguments),
            started_at=started_at,
        ).to_dict())

        # ③ 执行
        call_ctx = ctx if ctx is not None else CallContext(
            session_id=session_id, agent_id=agent_id,
        )
        try:
            tool_def = (
                self._registry.get_from_capability(tool_name, capability)
                if capability is not None
                else self._registry.get(tool_name)
            )
            result: ToolResult = tool_def.handler(arguments, call_ctx)
            status = "SUCCEEDED"
            error  = None
        except AppError as e:
            result = ToolResult(content=e.message or e.code, is_error=True, error_code=e.code)
            status = "FAILED"
            error  = e.message
        except Exception as e:
            result = ToolResult(content=str(e), is_error=True, error_code="TOOL_EXEC_ERROR")
            status = "FAILED"
            error  = str(e)
            logger.exception("ToolGateway unexpected error: tool=%s", tool_name)

        # ④ 写完成审计
        self._store.append(session_id, ToolCall(
            id=call_id,
            session_id=session_id,
            task_id=task_id,
            agent_id=agent_id,
            tool_name=tool_name,
            status=status,
            arguments=_redact(arguments),
            result=result.content[:200] if result.content else None,
            error=error,
            started_at=started_at,
            finished_at=now_iso(),
        ).to_dict())

        return result


def _redact(arguments: dict[str, Any]) -> dict[str, Any]:
    """脱敏敏感头字段。"""
    redacted = dict(arguments)
    if isinstance(redacted.get("headers"), dict):
        redacted["headers"] = {
            k: "***" if k.lower() in ("authorization", "x-api-key", "cookie") else v
            for k, v in redacted["headers"].items()
        }
    return redacted
