"""从远端服务器拉取 Skill 并解压到本地 skills_dir。"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.common.errors import AppError
from app.common.ssl_verify import make_ssl_verify
from app.skills.zip_utils import extract_zip, sanitize_folder
from app.storage.file.skill_pull_store import SkillPullStore

_TIMEOUT = 30
_HEADERS = {"Accept": "application/json"}


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
            with httpx.Client(trust_env=False, timeout=_TIMEOUT, verify=make_ssl_verify()) as client:
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
            with httpx.Client(trust_env=False, timeout=_TIMEOUT, verify=make_ssl_verify()) as client:
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
            folder_name = sanitize_folder(skill_name)
            dest_dir = self._skills_dir / folder_name
            extract_zip(resp.content, dest_dir)
        except AppError:
            raise
        except Exception as e:
            raise AppError("PULL_EXTRACT_FAILED", f"解压失败: {e}")

        self._store.record_pulled(remote_id, folder_name)
        return {"skill_id": folder_name, "name": skill_name}

    def import_to_remote(self, data: bytes, filename: str) -> dict:
        """上传 zip 到远端服务器的 POST /skills/import，返回 {skill_id, name}。"""
        url = self._require_url()
        try:
            with httpx.Client(trust_env=False, timeout=_TIMEOUT, verify=make_ssl_verify()) as client:
                resp = client.post(
                    f"{url}/skills/import",
                    files={"file": (filename, data, "application/zip")},
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise AppError("PULL_SERVER_ERROR", f"远端服务器返回 {e.response.status_code}：{body}")
        except Exception as e:
            raise AppError("PULL_SERVER_UNREACHABLE", f"无法连接远端服务器: {e}")

        item = resp.json()
        return {"skill_id": item.get("id", ""), "name": item.get("name", "")}
