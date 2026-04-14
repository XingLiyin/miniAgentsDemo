"""Tool Gateway：MCP 标准工具调用 + 白名单授权 + 审计（Phase 1）。

执行流程：
  call(tool_name, arguments, agent, task_id)
    ① PolicyEngine.authorize  —— 两层白名单（registry 注册 + agent.act_tool_list）
    ② 写审计 RUNNING
    ③ ToolRegistry.get(tool_name).handler(arguments)  —— 实际执行
    ④ 写审计 SUCCEEDED / FAILED
    ⑤ 返回 ToolResult
"""

from __future__ import annotations

import logging
from typing import Any

from app.common.errors import AppError
from app.common.utils import new_tool_call_id, now_iso
from app.domain.models.agent import Agent
from app.domain.models.tool_call import ToolCall
from app.runtime.policy_engine import PolicyEngine
from app.storage.file.tool_call_store import ToolCallStore
from app.tools.definition import ToolResult
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolGateway:
    """MCP 工具调用网关。"""

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
        agent: Agent,
        task_id: str,
    ) -> ToolResult:
        """统一入口：授权 → 审计 RUNNING → 执行 → 审计完成 → 返回结果。"""
        # ① 授权
        self._policy.authorize(agent, tool_name)

        # ② 写 RUNNING 审计
        call_id = new_tool_call_id()
        started_at = now_iso()
        self._store.append(agent.session_id, ToolCall(
            id=call_id,
            session_id=agent.session_id,
            task_id=task_id,
            agent_id=agent.id,
            tool_name=tool_name,
            status="RUNNING",
            arguments=_redact(arguments),
            started_at=started_at,
        ).to_dict())

        # ③ 执行
        try:
            tool_def = self._registry.get(tool_name)
            result: ToolResult = tool_def.handler(arguments)
            status = "SUCCEEDED"
            error = None
        except AppError as e:
            result = ToolResult(content="", is_error=True, error_code=e.code)
            status = "FAILED"
            error = e.message
        except Exception as e:
            result = ToolResult(content="", is_error=True, error_code="TOOL_EXEC_ERROR")
            status = "FAILED"
            error = str(e)
            logger.exception("ToolGateway unexpected error: tool=%s", tool_name)

        # ④ 写完成审计
        self._store.append(agent.session_id, ToolCall(
            id=call_id,
            session_id=agent.session_id,
            task_id=task_id,
            agent_id=agent.id,
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
