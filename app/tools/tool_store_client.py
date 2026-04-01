"""外部工具存储 HTTP 客户端。

负责将工具变更同步到外部向量 DB，并代理语义搜索请求。
tool_store_base_url 为空时所有操作均为 no-op。

外部 DB 需实现以下三个端点：
  POST {base_url}/tools/upsert  — 写入 / 更新工具
  POST {base_url}/tools/delete  — 删除工具
  POST {base_url}/tools/search  — 语义搜索
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ToolSearchResult:
    name: str
    description: str
    score: float


class ToolStoreClient:
    """调用外部工具存储的 HTTP 客户端。"""

    def __init__(self, base_url: str, timeout_sec: int) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    # ── 写入 ──────────────────────────────────────────────────────────────

    def upsert(self, tools: list[dict], provider: str) -> None:
        """将工具列表 upsert 到外部 DB。失败只记 warning，不中断调用方。"""
        if not self.enabled:
            return
        records = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t.get("input_schema", {}),
                "provider": provider,
            }
            for t in tools
        ]
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/tools/upsert",
                    json={"tools": records},
                )
                resp.raise_for_status()
            logger.debug("ToolStoreClient: upserted %d tool(s) for provider '%s'", len(records), provider)
        except Exception:
            logger.warning("ToolStoreClient.upsert failed for provider '%s'", provider, exc_info=True)

    def delete(self, names: list[str]) -> None:
        """从外部 DB 删除指定工具。失败只记 warning。"""
        if not self.enabled or not names:
            return
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/tools/delete",
                    json={"names": names},
                )
                resp.raise_for_status()
            logger.debug("ToolStoreClient: deleted %d tool(s)", len(names))
        except Exception:
            logger.warning("ToolStoreClient.delete failed for %s", names, exc_info=True)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[ToolSearchResult]:
        """语义搜索工具；未启用或出错时返回空列表。"""
        if not self.enabled:
            return []
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/tools/search",
                    json={"query": query, "top_k": top_k},
                )
                resp.raise_for_status()
                data = resp.json()
            return [
                ToolSearchResult(
                    name=r["name"],
                    description=r.get("description", ""),
                    score=r.get("score", 0.0),
                )
                for r in data.get("results", [])
            ]
        except Exception:
            logger.warning("ToolStoreClient.search failed for query '%s'", query, exc_info=True)
            return []


# ── 全局单例 ──────────────────────────────────────────────────────────────

_client: ToolStoreClient | None = None


def get_tool_store_client() -> ToolStoreClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = ToolStoreClient(
            base_url=settings.store_base_url,
            timeout_sec=settings.store_timeout_sec,
        )
    return _client
