"""AgentLoader：文件系统扫描与 Agent 定义文件解析。

类比 app/skills/loader.py 的 SkillLoader。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.agent_def.definition import AgentDefContent, AgentDefMetadata

logger = logging.getLogger(__name__)


class AgentLoader:
    """扫描 agents 目录并解析 SOUL.md / ROLE.md / TOOLS.md / STYLE.md 文件。"""

    def scan(self, agents_dir: Path) -> list[AgentDefMetadata]:
        """扫描 agents_dir，每个子目录至少有 SOUL.md + ROLE.md 才加载。"""
        if not agents_dir.exists():
            logger.warning("AgentLoader: agents_dir '%s' not found, no agents loaded", agents_dir)
            return []
        result = []
        for agent_dir in sorted(agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            if not (agent_dir / "SOUL.md").exists():
                continue
            try:
                metadata = self.load_metadata(agent_dir)
                result.append(metadata)
                logger.debug("AgentLoader: loaded agent '%s' from '%s'", metadata.name, agent_dir)
            except Exception as e:
                logger.warning("AgentLoader: failed to load '%s': %s", agent_dir.name, e)
        return result

    def load_metadata(self, agent_dir: Path) -> AgentDefMetadata:
        """解析 SOUL.md frontmatter + TOOLS.md frontmatter.tools → Level 1。"""
        soul_content = (agent_dir / "SOUL.md").read_text(encoding="utf-8")
        soul_fm, _ = _parse_agent_md(soul_content)

        # TOOLS.md frontmatter 提供 tool_list 快速路径
        tool_list: list[str] = []
        tool_list_ready = False
        tools_md_path = agent_dir / "TOOLS.md"
        if tools_md_path.exists():
            tools_content = tools_md_path.read_text(encoding="utf-8")
            tools_fm, _ = _parse_agent_md(tools_content)
            raw_tools = tools_fm.get("tools", [])
            if isinstance(raw_tools, list) and raw_tools:
                tool_list = raw_tools
                tool_list_ready = True

        return AgentDefMetadata(
            name=soul_fm["name"],
            version=str(soul_fm.get("version", "1.0.0")),
            description=str(soul_fm.get("description", "")).strip(),
            tool_list=tool_list,
            tool_list_ready=tool_list_ready,
            agent_dir=agent_dir,
        )

    def load_content(self, agent_dir: Path) -> AgentDefContent:
        """读取四个文件正文 → Level 2（按需调用）。"""
        metadata = self.load_metadata(agent_dir)

        soul_md = _read_body(agent_dir / "SOUL.md")
        role_md = _read_body(agent_dir / "ROLE.md") if (agent_dir / "ROLE.md").exists() else ""
        tools_md = _read_body(agent_dir / "TOOLS.md") if (agent_dir / "TOOLS.md").exists() else ""
        style_md = _read_body(agent_dir / "STYLE.md") if (agent_dir / "STYLE.md").exists() else ""

        return AgentDefContent(
            metadata=metadata,
            soul_md=soul_md,
            role_md=role_md,
            tools_md=tools_md,
            style_md=style_md,
        )


# ── 私有解析工具 ───────────────────────────────────────────────────────────

def _read_body(path: Path) -> str:
    """读取文件并返回 frontmatter 之后的正文部分。"""
    content = path.read_text(encoding="utf-8")
    _, body = _parse_agent_md(content)
    return body


def _parse_agent_md(content: str) -> tuple[dict, str]:
    """将 Agent 定义文件内容分离为 (frontmatter_dict, body_str)。

    frontmatter 以 '---' 开头和结尾包裹，body 是其余部分。
    """
    if not content.startswith("---"):
        return {}, content.strip()

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content.strip()

    frontmatter_str = content[3:end].strip()
    body = content[end + 4:].strip()
    frontmatter = _parse_simple_yaml(frontmatter_str)
    return frontmatter, body


def _parse_simple_yaml(text: str) -> dict:
    """轻量级 YAML 解析，支持 Agent 定义文件 frontmatter 所需格式：

    - 简单键值：  key: value
    - 折叠字符串：key: >\\n  line1\\n  line2
    - 列表：      key:\\n  - item1\\n  - item2
    - 带引号值：  key: "value" 或 key: 'value'
    """
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

        if raw_val in (">", "|"):
            # 折叠/字面量多行字符串
            parts: list[str] = []
            i += 1
            while i < len(lines) and lines[i].startswith("  "):
                parts.append(lines[i].strip())
                i += 1
            result[key] = " ".join(parts)
            continue

        if not raw_val:
            # 可能是列表
            items: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            result[key] = items
            continue

        # 去掉引号
        result[key] = raw_val.strip('"').strip("'")
        i += 1

    return result
