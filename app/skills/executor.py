"""MCPSkillExecutor：通过私有 SkillMCPConn 执行远端 skill 的指令与资源调用。"""

from __future__ import annotations

from app.skills.definition import SkillMetadata
from app.skills.skill_mcp_conn import SkillMCPConn
from app.tools.types import CallContext, ToolResult


class MCPSkillExecutor:
    """通过私有 SkillMCPConn 向远端 skill server 发起调用，对 LLM 完全透明。"""

    def __init__(self, conn: SkillMCPConn) -> None:
        self._conn = conn

    def load_instructions(self, meta: SkillMetadata, ctx: CallContext | None = None) -> str:
        return self._conn.load_skill_md(meta.name, ctx)

    def list_files(self, meta: SkillMetadata, ctx: CallContext | None = None) -> str:
        return self._conn.get_skill_files(meta.name, ctx)

    def load_resource(self, meta: SkillMetadata, path: str, ctx: CallContext | None = None) -> str:
        return self._conn.load_skill_reference(meta.name, path, ctx)

    def exec_script(
        self,
        meta: SkillMetadata,
        script_name: str,
        args: str,
        ctx: CallContext | None = None,
    ) -> ToolResult:
        return self._conn.exec_skill_script(meta.name, script_name, args, ctx)
