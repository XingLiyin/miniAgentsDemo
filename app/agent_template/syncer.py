"""AgentTemplateSyncer：同步文件系统与 store，并监控目录变化。"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.agent_template.loader import AgentLoader
from app.common.utils import now_iso
from app.storage.file.agent_template_store import AgentTemplateStore, make_template_id

logger = logging.getLogger(__name__)


class AgentTemplateSyncer:
    """扫描 agent 目录并将元数据持久化到 store。

    职责：
    - sync_global: 启动时同步全局目录
    - sync_workspace: session 创建时同步 workspace/.agents/
    - purge_workspace: session 删除时清除对应记录
    - start_watcher: 后台轮询，目录 mtime 变化时自动重新同步
    """

    def __init__(self, store: AgentTemplateStore, loader: AgentLoader) -> None:
        self._store = store
        self._loader = loader
        self._watched_workspaces: set[str] = set()
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── 同步 ─────────────────────────────────────────────────────────────────

    def sync_global(self, agents_dir: Path) -> None:
        """扫描全局目录，upsert 所有模板到 store，删除已消失的。"""
        details_list = self._loader.scan(agents_dir)
        synced_ids: set[str] = set()

        for details in details_list:
            tid = make_template_id("global", "", details.name)
            synced_ids.add(tid)
            existing = self._store.get(tid)
            now = now_iso()
            self._store.save({
                "id": tid,
                "name": details.name,
                "version": details.version,
                "description": details.description,
                "scope": "global",
                "source_dir": Path(details.source_dir).name,
                "workspace_dir": "",
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            })

        for d in self._store.list_global():
            if d["id"] not in synced_ids:
                self._store.delete(d["id"])
                logger.debug("AgentTemplateSyncer: removed stale global template '%s'", d.get("name"))

        logger.info("AgentTemplateSyncer: synced %d global template(s) from '%s'", len(details_list), agents_dir)

    @staticmethod
    def _resolve(workspace_dir: str) -> str:
        from app.config.settings import resolve_working_dir
        return resolve_working_dir(workspace_dir) or workspace_dir

    def sync_workspace(self, workspace_dir: str) -> None:
        """扫描 workspace/.agents/，upsert workspace 模板，删除已消失的。"""
        workspace_dir = self._resolve(workspace_dir)
        ws_agents_dir = Path(workspace_dir) / ".agents"
        logger.debug("AgentTemplateSyncer: scanning '%s'", ws_agents_dir.resolve())
        details_list = self._loader.scan(ws_agents_dir)
        synced_ids: set[str] = set()

        for details in details_list:
            tid = make_template_id("workspace", workspace_dir, details.name)
            synced_ids.add(tid)
            existing = self._store.get(tid)
            now = now_iso()
            self._store.save({
                "id": tid,
                "name": details.name,
                "version": details.version,
                "description": details.description,
                "scope": "workspace",
                "source_dir": Path(details.source_dir).name,
                "workspace_dir": workspace_dir,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            })

        for d in self._store.list_all_dicts():
            if d.get("scope") == "workspace" and d.get("workspace_dir") == workspace_dir:
                if d["id"] not in synced_ids:
                    self._store.delete(d["id"])
                    logger.debug("AgentTemplateSyncer: removed stale workspace template '%s'", d.get("name"))

        logger.info(
            "AgentTemplateSyncer: synced %d workspace template(s) for '%s'",
            len(details_list), workspace_dir,
        )

    def purge_workspace(self, workspace_dir: str) -> None:
        """删除该 workspace 的全部 store 记录。session delete 时调用。"""
        workspace_dir = self._resolve(workspace_dir)
        count = self._store.delete_workspace(workspace_dir)
        logger.info("AgentTemplateSyncer: purged %d template(s) for workspace '%s'", count, workspace_dir)

    # ── 监控 ─────────────────────────────────────────────────────────────────

    def register_workspace(self, workspace_dir: str) -> None:
        self._watched_workspaces.add(self._resolve(workspace_dir))

    def unregister_workspace(self, workspace_dir: str) -> None:
        self._watched_workspaces.discard(self._resolve(workspace_dir))

    def start_watcher(self, agents_dir: Path, poll_interval: float = 5.0) -> None:
        """启动后台轮询线程，检测目录 mtime 变化后自动重新同步。"""
        if self._watcher_thread and self._watcher_thread.is_alive():
            return
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            args=(agents_dir, poll_interval),
            daemon=True,
            name="agent-template-watcher",
        )
        self._watcher_thread.start()
        logger.info("AgentTemplateSyncer: watcher started (poll_interval=%.1fs)", poll_interval)

    def stop_watcher(self) -> None:
        self._stop_event.set()

    def _watch_loop(self, agents_dir: Path, poll_interval: float) -> None:
        last_mtimes: dict[str, int] = {}

        def mtime(path: Path) -> int:
            try:
                return path.stat().st_mtime_ns
            except OSError:
                return 0

        while not self._stop_event.is_set():
            try:
                # 检查全局目录
                key = str(agents_dir)
                current = mtime(agents_dir)
                if current != last_mtimes.get(key):
                    last_mtimes[key] = current
                    if current:
                        logger.debug("AgentTemplateSyncer: global dir changed, re-syncing")
                        self.sync_global(agents_dir)

                # 检查已注册的 workspace 目录
                for wd in list(self._watched_workspaces):
                    ws_dir = Path(wd) / ".agents"
                    wkey = str(ws_dir)
                    current = mtime(ws_dir)
                    if current != last_mtimes.get(wkey):
                        last_mtimes[wkey] = current
                        if current:
                            logger.debug("AgentTemplateSyncer: workspace dir changed '%s', re-syncing", wd)
                            self.sync_workspace(wd)
            except Exception:
                logger.exception("AgentTemplateSyncer: watcher error")

            self._stop_event.wait(poll_interval)
