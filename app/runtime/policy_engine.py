"""PolicyEngine：构造时按 Global/Tool 分类索引，运行时零遍历多余规则。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.runtime.policy_rule import GlobalRule, PolicyRule, ToolRule, WhitelistRule

if TYPE_CHECKING:
    from app.domain.models.agent import Agent
    from app.tools.definition import CallContext
    from app.tools.registry import ToolRegistry


class PolicyEngine:
    def __init__(self, rules: list[PolicyRule]) -> None:
        self._global_rules: list[PolicyRule] = []
        self._tool_rules: dict[str, list[PolicyRule]] = {}

        for rule in rules:
            if isinstance(rule, GlobalRule):
                self._global_rules.append(rule)
            elif isinstance(rule, ToolRule):
                for tool in rule._tools:
                    self._tool_rules.setdefault(tool, []).append(rule)

    @classmethod
    def default(cls, tool_registry: "ToolRegistry") -> "PolicyEngine":
        """默认配置：仅白名单校验。"""
        return cls([WhitelistRule(tool_registry)])

    def authorize(
        self,
        agent: "Agent",
        tool_name: str,
        arguments: dict | None = None,
        ctx: "CallContext | None" = None,
    ) -> None:
        args = arguments or {}
        for rule in self._global_rules:
            rule.check(agent, tool_name, args, ctx)
        for rule in self._tool_rules.get(tool_name, ()):
            rule.check(agent, tool_name, args, ctx)
