"""从远端服务器拉取 Skill 并解压到本地 skills_dir。"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import httpx

from app.common.errors import AppError
from app.storage.file.skill_pull_store import SkillPullStore

_TIMEOUT = 30
_HEADERS = {"Accept": "application/json"}


def _sanitize_folder(name: str) -> str:
    """将 skill name 转换为合法的目录名：小写、非字母数字替换为连字符。"""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "skill"


class SkillPullService:
    def __init__(self, server_url: str, skills_dir: Path, store: SkillPullStore) -> None:
        self._server_url = server_url.rstrip("/")
        self._skills_dir = skills_dir
        self._store = store

    def _require_url(self) -> str:
        if not self._server_url:
            raise AppError("PULL_SERVER_NOT_CONFIGURED", "远端 Skill 服务器 URL 未配置")
        return self._server_url

    def list_remote(self) -> list[dict]:
        url = self._require_url()
        try:
            with httpx.Client(trust_env=False, timeout=_TIMEOUT) as client:
                resp = client.get(f"{url}/skills", headers=_HEADERS)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise AppError(
                "PULL_SERVER_ERROR",
                f"远端服务器返回 {e.response.status_code}：{body}",
            )
        except Exception as e:
            raise AppError("PULL_SERVER_UNREACHABLE", f"无法连接远端服务器: {e}")

        items = resp.json()
        pulled_map = self._store.get_pulled_map()
        result = []
        for item in items:
            result.append({
                "id": item["id"],
                "name": item["name"],
                "description": item.get("description"),
                "domain": item.get("domain"),
                "create_time": item.get("createTime"),
                "is_pulled": item["id"] in pulled_map,
            })
        return result

    def pull_skill(self, remote_id: str, skill_name: str) -> dict:
        url = self._require_url()
        try:
            with httpx.Client(trust_env=False, timeout=_TIMEOUT) as client:
                resp = client.get(
                    f"{url}/skills/{remote_id}/export",
                    headers={**_HEADERS, "Accept": "application/zip,application/octet-stream,*/*"},
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise AppError("REMOTE_SKILL_NOT_FOUND", f"远端 skill '{remote_id}' 不存在")
            body = e.response.text[:500]
            raise AppError("PULL_SERVER_ERROR", f"远端服务器返回 {e.response.status_code}：{body}")
        except Exception as e:
            raise AppError("PULL_SERVER_UNREACHABLE", f"无法连接远端服务器: {e}")

        try:
            folder_name = _sanitize_folder(skill_name)
            dest_dir = self._skills_dir / folder_name
            _extract_zip(resp.content, dest_dir)
        except AppError:
            raise
        except Exception as e:
            raise AppError("PULL_EXTRACT_FAILED", f"解压失败: {e}")

        self._store.record_pulled(remote_id, folder_name)
        return {"skill_id": folder_name, "name": skill_name}


def _extract_zip(data: bytes, dest_dir: Path) -> None:
    """将 ZIP 内容解压到 dest_dir。

    若 ZIP 内只有单个顶层目录，则展开其内容（去掉外层目录）。
    目标目录已存在时覆盖。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if not names:
            raise AppError("PULL_EXTRACT_FAILED", "ZIP 包为空")

        # 判断是否有单个顶层目录
        top_level = {n.split("/")[0] for n in names}
        single_root = (
            len(top_level) == 1
            and all(n.startswith(next(iter(top_level)) + "/") for n in names)
        )
        root_prefix = (next(iter(top_level)) + "/") if single_root else ""

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
