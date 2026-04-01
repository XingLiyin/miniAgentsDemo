"""工具同步服务：将 ToolRegistry 事件推送到外部工具存储。

ToolRegistry 在注册/注销工具时调用此服务；
外部向量 DB 负责 embedding 生成与语义检索。
"""

from __future__ import annotations

import logging

from app.tools.definition import ToolDefinition
from app.tools.tool_store_client import get_tool_store_client

logger = logging.getLogger(__name__)


class ToolSyncService:
    """桥接 ToolRegistry 事件 → ToolStoreClient。"""

    def on_tools_added(self, tool_defs: list[ToolDefinition], provider: str) -> None:
        """工具注册后调用；推送到外部 DB。"""
        if not tool_defs:
            return
        records = [
            {
                "name": td.name,
                "description": td.description,
                "input_schema": td.input_schema.to_dict() if hasattr(td.input_schema, "to_dict") else {},
            }
            for td in tool_defs
        ]
        get_tool_store_client().upsert(records, provider=provider)

    def on_tools_removed(self, names: list[str]) -> None:
        """工具注销后调用；从外部 DB 删除。"""
        if not names:
            return
        get_tool_store_client().delete(names)


# ── 全局单例 ──────────────────────────────────────────────────────────────

_sync_service: ToolSyncService | None = None


def get_tool_sync_service() -> ToolSyncService:
    global _sync_service
    if _sync_service is None:
        _sync_service = ToolSyncService()
    return _sync_service
