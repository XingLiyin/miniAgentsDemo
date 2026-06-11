"""AgentLoader：文件系统扫描与 Agent 定义文件解析。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.agent_template.definition import AgentCapabilityConfig, AgentDefDetails
from app.config.settings import get_settings, resolve_working_dir

if TYPE_CHECKING:
    from app.storage.file.agent_template_store import AgentTemplateStore

_LIST_DETAILS_TTL = 10.0  # seconds

logger = logging.getLogger(__name__)


@dataclass
class _ToolSpec:
    required: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)


class AgentLoader:
    """扫描 agents 目录并解析 SOUL.md / ROLE.md 文件。

    可选持有 AgentTemplateStore：注入后额外提供 get_details / list_details，
    通过 store 查 source_dir 再读文件，消费方只需持有 loader 一个对象。
    """

    def __init__(self, store: "AgentTemplateStore | None" = None) -> None:
        self._store = store
        self._list_cache: dict[str, tuple[float, list[AgentDefDetails]]] = {}  # workspace_dir → (ts, details)

    def scan(self, agents_dir: Path) -> list[AgentDefDetails]:
        """扫描 agents_dir，每个子目录至少有 SOUL.md 才加载。"""
        if not agents_dir.exists():
            logger.warning("AgentLoader: agents_dir '%s' not found, no agents loaded", agents_dir.resolve())
            return []
        result = []
        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            if not (agent_dir / "SOUL.md").exists():
                continue
            try:
                details = self.load(agent_dir)
                result.append(details)
                logger.debug("AgentLoader: loaded agent '%s' from '%s'", details.name, agent_dir)
            except Exception as e:
                logger.warning("AgentLoader: failed to load '%s': %s", agent_dir.name, e)
        return result

    def load(self, agent_dir: Path) -> AgentDefDetails:
        """解析 SOUL.md + ROLE.md → AgentDefDetails。"""
        soul_content = (agent_dir / "SOUL.md").read_text(encoding="utf-8")
        soul_fm, actor_soul = _parse_agent_md(soul_content)

        act_tool_spec = _parse_tool_spec(soul_fm)
        mcp_act_servers = _parse_str_list(soul_fm, "mcp_servers")
        subagents = _parse_str_list(soul_fm, "subagents")

        observer_role = ""
        observe_tool_spec = _ToolSpec()
        mcp_observe_servers: list[str] = []
        role_md_path = agent_dir / "ROLE.md"
        if role_md_path.exists():
            role_content = role_md_path.read_text(encoding="utf-8")
            role_fm, observer_role = _parse_agent_md(role_content)
            observe_tool_spec = _parse_tool_spec(role_fm)
            mcp_observe_servers = _parse_str_list(role_fm, "mcp_servers")

        return AgentDefDetails(
            name=soul_fm["name"],
            version=str(soul_fm.get("version", "1.0.0")),
            description=str(soul_fm.get("description", "")).strip(),
            source_dir=str(agent_dir),
            actor_soul=actor_soul,
            observer_role=observer_role,
            actor_capability=AgentCapabilityConfig(
                required_tools=act_tool_spec.required,
                forbidden_tools=act_tool_spec.forbidden,
                required_mcp_servers=mcp_act_servers,
                subagents=subagents,
            ),
            observer_capability=AgentCapabilityConfig(
                required_tools=observe_tool_spec.required,
                forbidden_tools=observe_tool_spec.forbidden,
                required_mcp_servers=mcp_observe_servers,
            ),
        )

    # ── store-backed 查询（需注入 store）─────────────────────────────────────

    def _resolve_dir(self, source_dir: str, workspace_dir: str) -> Path:
        """将 store 中存储的 source_dir 名称解析为绝对路径。
        workspace_dir 为空 → global 模板，从 settings.agents_dir 查找；
        否则 → workspace 模板，从 {workspace_dir}/.agents/ 查找。
        """
        if workspace_dir:
            return Path(workspace_dir) / ".agents" / source_dir
        return get_settings().agents_dir / source_dir

    def get_details(self, name: str, workspace_dir: str = "") -> tuple[AgentDefDetails | None, str | None]:
        """从 store 查 source_dir，再读文件返回完整 AgentDefDetails。"""
        assert self._store is not None, "AgentLoader.get_details requires store"
        workspace_dir = resolve_working_dir(workspace_dir) if workspace_dir else workspace_dir
        d = self._store.find_by_name(name, workspace_dir)
        if not d or not d.get("source_dir"):
            return None, None
        try:
            agent_dir = self._resolve_dir(d["source_dir"], d.get("workspace_dir", ""))
            return self.load(agent_dir), d.get("id", "")
        except Exception as e:
            logger.warning("AgentLoader.get_details: failed to load '%s': %s", name, e)
            return None, None

    def get_details_by_id(self, template_id: str) -> AgentDefDetails | None:
        """从 store 查 source_dir，再读文件返回完整 AgentDefDetails。"""
        assert self._store is not None, "AgentLoader.get_details_by_id requires store"
        d = self._store.get(template_id)
        if not d or not d.get("source_dir"):
            return None
        try:
            agent_dir = self._resolve_dir(d["source_dir"], d.get("workspace_dir", ""))
            return self.load(agent_dir)
        except Exception as e:
            logger.warning("AgentLoader.get_details_by_id: failed to load '%s': %s", template_id, e)
            return None

    def list_details(self, workspace_dir: str = "") -> list[AgentDefDetails]:
        """从 store 列出可见模板，逐条读文件返回 AgentDefDetails 列表。结果在 TTL 内缓存。"""
        assert self._store is not None, "AgentLoader.list_details requires store"
        workspace_dir = resolve_working_dir(workspace_dir) if workspace_dir else workspace_dir
        now = time.monotonic()
        cached = self._list_cache.get(workspace_dir)
        if cached and now - cached[0] < _LIST_DETAILS_TTL:
            return cached[1]
        result = []
        for d in self._store.list_for_workspace(workspace_dir):
            source_dir = d.get("source_dir", "")
            if not source_dir:
                continue
            try:
                result.append(self.load(self._resolve_dir(source_dir, d.get("workspace_dir", ""))))
            except Exception as e:
                logger.warning("AgentLoader.list_details: failed to load '%s': %s", d.get("name"), e)
        self._list_cache[workspace_dir] = (now, result)
        return result


# ── 私有解析工具 ───────────────────────────────────────────────────────────

def _parse_agent_md(content: str) -> tuple[dict, str]:
    if not content.startswith("---"):
        return {}, content.strip()
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content.strip()
    frontmatter_str = content[3:end].strip()
    body = content[end + 4:].strip()
    frontmatter = _parse_simple_yaml(frontmatter_str)
    return frontmatter, body


def _parse_str_list(fm: dict, key: str) -> list[str]:
    raw = fm.get(key)
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    return []


def _parse_tool_spec(fm: dict) -> _ToolSpec:
    raw = fm.get("tools")
    if not raw:
        return _ToolSpec()
    if isinstance(raw, list):
        return _ToolSpec(required=raw)
    if isinstance(raw, dict):
        required = raw.get("required") or []
        forbidden = raw.get("forbidden") or []
        if not isinstance(required, list):
            required = []
        if not isinstance(forbidden, list):
            forbidden = []
        return _ToolSpec(required=required, forbidden=forbidden)
    return _ToolSpec()


def _parse_simple_yaml(text: str) -> dict:
    result: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("#") or line.startswith(" "):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue

        key, _, raw_val = line.partition(":")
        key = key.strip()
        raw_val = raw_val.strip()

        if raw_val and raw_val[0] in (">", "|") and all(
            c in "-+0123456789" for c in raw_val[1:]
        ):
            # 块标量：folded(>) / literal(|)，可带 chomping/indent 指示符（如 >- |- >+）
            folded = raw_val[0] == ">"
            parts: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith("  "):
                parts.append(lines[i].strip())
                i += 1
            result[key] = (" " if folded else "\n").join(parts)
            continue

        if not raw_val:
            peek = i + 1
            while peek < len(lines) and not lines[peek].strip():
                peek += 1
            if peek < len(lines) and lines[peek].startswith("  ") and not lines[peek].strip().startswith("- "):
                nested_lines: list[str] = []
                i += 1
                while i < len(lines) and (not lines[i] or lines[i].startswith("  ")):
                    nested_lines.append(lines[i][2:] if lines[i].startswith("  ") else "")
                    i += 1
                result[key] = _parse_simple_yaml("\n".join(nested_lines))
            else:
                items: list[str] = []
                i += 1
                while i < len(lines) and lines[i].strip().startswith("- "):
                    items.append(lines[i].strip()[2:].strip())
                    i += 1
                result[key] = items
            continue

        if raw_val.startswith("[") and raw_val.endswith("]"):
            inner = raw_val[1:-1]
            result[key] = [
                item.strip().strip("'").strip('"')
                for item in inner.split(",")
                if item.strip().strip("'").strip('"')
            ]
            i += 1
            continue

        result[key] = raw_val.strip('"').strip("'")
        i += 1

    return result
