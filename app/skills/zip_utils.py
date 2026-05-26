"""ZIP 工具：校验 skill zip 包并解压。"""

from __future__ import annotations

import io
import re
import zipfile

from app.common.errors import AppError
from app.skills.loader import _parse_skill_md


def sanitize_folder(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "skill"


def _find_skill_md(names: list[str]) -> str | None:
    """找到 ZIP 内的 SKILL.md 路径（兼容单顶层目录）。"""
    top_level = {n.split("/")[0] for n in names}
    single_root = (
        len(top_level) == 1
        and all(n.startswith(next(iter(top_level)) + "/") for n in names)
    )
    if single_root:
        root = next(iter(top_level))
        candidate = f"{root}/SKILL.md"
    else:
        candidate = "SKILL.md"
    return candidate if candidate in names else None


def validate_skill_zip(data: bytes) -> tuple[str, str]:
    """校验 zip 包，返回 (name, description)。校验失败时抛出 AppError。

    校验规则：
    1. 必须是合法 ZIP。
    2. 内含 SKILL.md。
    3. SKILL.md frontmatter 必须有非空 name 和 description。
    """
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise AppError("IMPORT_INVALID_ZIP", "上传的文件不是有效的 ZIP 包")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if not names:
            raise AppError("IMPORT_INVALID_ZIP", "ZIP 包为空")

        skill_md_path = _find_skill_md(names)
        if skill_md_path is None:
            raise AppError("IMPORT_MISSING_SKILL_MD", "ZIP 包中未找到 SKILL.md")

        try:
            content = zf.read(skill_md_path).decode("utf-8")
        except Exception as e:
            raise AppError("IMPORT_INVALID_ZIP", f"无法读取 SKILL.md: {e}")

    frontmatter, _ = _parse_skill_md(content)
    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()

    if not name:
        raise AppError("IMPORT_MISSING_NAME", "SKILL.md frontmatter 缺少 name 字段")
    if not description:
        raise AppError("IMPORT_MISSING_DESCRIPTION", "SKILL.md frontmatter 缺少 description 字段")

    return name, description


def extract_zip(data: bytes, dest_dir: "Path") -> None:  # noqa: F821
    """将 ZIP 内容解压到 dest_dir。

    若 ZIP 内只有单个顶层目录则展开其内容（去掉外层目录）。
    目标目录已存在时覆盖。
    """
    from pathlib import Path  # local import to avoid circular if needed

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if not names:
            raise AppError("IMPORT_INVALID_ZIP", "ZIP 包为空")

        top_level = {n.split("/")[0] for n in names}
        single_root = (
            len(top_level) == 1
            and all(n.startswith(next(iter(top_level)) + "/") for n in names)
        )
        root_prefix = (next(iter(top_level)) + "/") if single_root else ""

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for member in zf.infolist():
            rel_path = member.filename
            if root_prefix:
                if not rel_path.startswith(root_prefix):
                    continue
                rel_path = rel_path[len(root_prefix):]
            if not rel_path:
                continue
            target = dest_dir / rel_path
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member.filename))
