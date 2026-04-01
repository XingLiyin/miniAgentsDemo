"""外部 Skill 存储 HTTP 客户端。

与工具存储共用同一后端服务（store_base_url），使用 /skills/* 路径。
store_base_url 为空时所有操作均为 no-op。

外部服务需实现以下三个端点：
  POST {base_url}/skills/upsert  — 写入 / 更新 skill
  POST {base_url}/skills/delete  — 删除 skill
  POST {base_url}/skills/search  — 语义搜索
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class SkillSearchResult:
    name: str
    description: str
    score: float


class SkillStoreClient:
    """调用外部 Skill 存储的 HTTP 客户端。"""

    def __init__(self, base_url: str, timeout_sec: int) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    # ── 写入 ──────────────────────────────────────────────────────────────

    def upsert(self, skills: list[dict]) -> None:
        """将 skill 列表 upsert 到外部 DB。失败只记 warning，不中断调用方。"""
        if not self.enabled or not skills:
            return
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/skills/upsert",
                    json={"skills": skills},
                )
                resp.raise_for_status()
            logger.debug("SkillStoreClient: upserted %d skill(s)", len(skills))
        except Exception:
            logger.warning("SkillStoreClient.upsert failed", exc_info=True)

    def delete(self, names: list[str]) -> None:
        """从外部 DB 删除指定 skill。失败只记 warning。"""
        if not self.enabled or not names:
            return
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/skills/delete",
                    json={"names": names},
                )
                resp.raise_for_status()
            logger.debug("SkillStoreClient: deleted %d skill(s)", len(names))
        except Exception:
            logger.warning("SkillStoreClient.delete failed for %s", names, exc_info=True)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5) -> list[SkillSearchResult]:
        """语义搜索 skill；未启用或出错时返回空列表。"""
        if not self.enabled:
            return []
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base_url}/skills/search",
                    json={"query": query, "top_k": top_k},
                )
                resp.raise_for_status()
                data = resp.json()
            return [
                SkillSearchResult(
                    name=r["name"],
                    description=r.get("description", ""),
                    score=r.get("score", 0.0),
                )
                for r in data.get("results", [])
            ]
        except Exception:
            logger.warning("SkillStoreClient.search failed for query '%s'", query, exc_info=True)
            return []


# ── 全局单例 ──────────────────────────────────────────────────────────────

_client: SkillStoreClient | None = None


def get_skill_store_client() -> SkillStoreClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = SkillStoreClient(
            base_url=settings.store_base_url,
            timeout_sec=settings.store_timeout_sec,
        )
    return _client
