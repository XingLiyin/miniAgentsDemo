"""SkillLoader：文件系统扫描与 SKILL.md 解析。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.skills.definition import SkillMetadata

logger = logging.getLogger(__name__)


class SkillLoader:
    """扫描 skills 目录并解析 SKILL.md 文件。"""

    def scan(self, skills_dir: Path) -> list[tuple[SkillMetadata, Path]]:
        """扫描 skills_dir，返回 (SkillMetadata, skill_dir) 列表。"""
        if not skills_dir.exists():
            logger.warning("SkillLoader: skills_dir '%s' not found, no skills loaded", skills_dir)
            return []
        result = []
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                metadata = self.load_metadata(skill_md)
                result.append((metadata, skill_dir))
                logger.debug("SkillLoader: loaded skill '%s' from '%s'", metadata.name, skill_dir)
            except Exception as e:
                logger.warning("SkillLoader: failed to load '%s': %s", skill_dir.name, e)
        return result

    def load_metadata(self, skill_md_path: Path) -> SkillMetadata:
        """解析 SKILL.md frontmatter，返回 SkillMetadata（Level 1）。"""
        content = skill_md_path.read_text(encoding="utf-8")
        frontmatter, _ = _parse_skill_md(content)
        return SkillMetadata(
            name=frontmatter["name"],
            description=str(frontmatter.get("description", "")).strip(),
            triggers=frontmatter.get("triggers") or [],
            version=str(frontmatter.get("version", "1.0")),
        )

    def load_instructions(self, skill_dir: Path) -> str:
        """读取 SKILL.md 主体（frontmatter 之后的部分）—— Level 2。"""
        skill_md = skill_dir / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        _, body = _parse_skill_md(content)
        return body

    def load_resource(self, skill_dir: Path, resource_path: str) -> str:
        """读取 skill 目录内的资源文件 —— Level 3。

        安全检查：resource_path 不能逃出 skill_dir。
        """
        full_path = (skill_dir / resource_path).resolve()
        try:
            full_path.relative_to(skill_dir.resolve())
        except ValueError:
            raise ValueError(f"Resource path '{resource_path}' escapes skill directory")
        return full_path.read_text(encoding="utf-8")


# ── 私有解析工具 ───────────────────────────────────────────────────────────

def _parse_skill_md(content: str) -> tuple[dict, str]:
    """将 SKILL.md 内容分离为 (frontmatter_dict, body_str)。

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
    """轻量级 YAML 解析，支持 SKILL.md frontmatter 所需格式：

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
