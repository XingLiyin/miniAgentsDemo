"""Smoke tests for /api/v1/skills, /api/v1/mcp-servers and
/api/v1/remote-skill-sources.

Local skill import/list/delete runs against a real zip and a real skills_dir.
The MCP-backed endpoints stub the service layer — connecting would spawn
subprocesses / reach the network.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.common.errors import AppError

SKILL_MD = """---
name: pdf-filler
description: Fills PDF forms.
version: 2.1
triggers: [pdf, form]
---

# Steps
Do the thing.
"""


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ── 本地 Skill ───────────────────────────────────────────────────────────────

def test_import_list_and_delete_local_skill(client, api_env):
    payload = _zip({"pdf-filler/SKILL.md": SKILL_MD, "pdf-filler/run.py": "print(1)"})

    imported = client.post("/api/v1/skills/import", files={"file": ("s.zip", payload, "application/zip")})
    assert imported.status_code == 200
    assert imported.json() == {
        "skill_id": "pdf-filler",
        "name": "pdf-filler",
        "description": "Fills PDF forms.",
        "version": "2.1",
        "triggers": ["pdf", "form"],
    }
    # the single top-level dir is stripped on extract
    assert (api_env / "skills" / "pdf-filler" / "SKILL.md").exists()
    assert (api_env / "skills" / "pdf-filler" / "run.py").exists()

    assert [s["skill_id"] for s in client.get("/api/v1/skills").json()] == ["pdf-filler"]

    assert client.delete("/api/v1/skills/pdf-filler").status_code == 204
    assert client.get("/api/v1/skills").json() == []
    assert not (api_env / "skills" / "pdf-filler").exists()


@pytest.mark.parametrize("payload, code", [
    (b"not a zip at all", "IMPORT_INVALID_ZIP"),
    (_zip({"readme.txt": "hi"}), "IMPORT_MISSING_SKILL_MD"),
    (_zip({"SKILL.md": "---\ndescription: d\n---\n"}), "IMPORT_MISSING_NAME"),
    (_zip({"SKILL.md": "---\nname: n\n---\n"}), "IMPORT_MISSING_DESCRIPTION"),
])
def test_import_local_skill_rejects_bad_zip(client, payload, code):
    resp = client.post("/api/v1/skills/import", files={"file": ("s.zip", payload, "application/zip")})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == code


def test_delete_local_skill_errors(client):
    missing = client.delete("/api/v1/skills/ghost")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "LOCAL_SKILL_NOT_FOUND"

    escape = client.delete("/api/v1/skills/..")
    assert escape.status_code == 500
    assert escape.json()["detail"]["code"] == "LOCAL_SKILL_INVALID_ID"


# ── 远端 Skill 拉取服务器 ────────────────────────────────────────────────────

def test_pull_server_not_configured(client):
    """skill_pull_server_url is empty in tests → every remote call 400s."""
    catalog = client.get("/api/v1/skills/pull-server/catalog")
    assert catalog.status_code == 400
    assert catalog.json()["detail"]["code"] == "PULL_SERVER_NOT_CONFIGURED"

    pull = client.post("/api/v1/skills/pull-server/catalog/r1/pull", json={"name": "s"})
    assert pull.status_code == 400

    upload = client.post("/api/v1/skills/pull-server/import",
                         files={"file": ("s.zip", b"x", "application/zip")})
    assert upload.status_code == 400


def test_pull_skill_requires_name(client):
    resp = client.post("/api/v1/skills/pull-server/catalog/r1/pull", json={"name": "  "})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "MISSING_NAME"


def test_pull_server_catalog_and_pull(client, monkeypatch):
    from app.domain.services.skill_pull_service import SkillPullService

    monkeypatch.setattr(SkillPullService, "list_remote",
                        lambda self: [{"id": "r1", "name": "s1", "description": "d"}])
    monkeypatch.setattr(SkillPullService, "pull_skill",
                        lambda self, rid, name: {"skill_id": "s1", "name": name})
    monkeypatch.setattr(SkillPullService, "import_to_remote",
                        lambda self, data, filename: {"skill_id": "s1", "name": filename})

    assert client.get("/api/v1/skills/pull-server/catalog").json()[0]["name"] == "s1"
    assert client.post("/api/v1/skills/pull-server/catalog/r1/pull",
                       json={"name": "s1"}).json()["name"] == "s1"
    assert client.post("/api/v1/skills/pull-server/import",
                       files={"file": ("s.zip", b"x", "application/zip")}).json()["name"] == "s.zip"


# ── MCP Server ───────────────────────────────────────────────────────────────

def test_mcp_servers_empty_and_not_found(client):
    assert client.get("/api/v1/mcp-servers").json() == []
    assert client.get("/api/v1/mcp-servers/ghost").status_code == 404
    assert client.delete("/api/v1/mcp-servers/ghost").status_code == 404
    assert client.post("/api/v1/mcp-servers/ghost/refresh").status_code == 404


def test_register_and_refresh_mcp_server(client, monkeypatch):
    from app.domain.services.mcp_service import MCPService, MCPServerInfo, MCPToolInfo

    info = MCPServerInfo(
        name="fs", type="stdio", status="CONNECTED",
        tools=[MCPToolInfo(name="read_file", description="read a file")],
        command="npx", args=["-y", "server-fs"],
    )
    monkeypatch.setattr(MCPService, "register_stdio",
                        lambda self, **kw: info)
    monkeypatch.setattr(MCPService, "register_http",
                        lambda self, **kw: MCPServerInfo(name="web", type="http", url=kw["url"]))
    monkeypatch.setattr(MCPService, "list_all", lambda self: [info])
    monkeypatch.setattr(MCPService, "get", lambda self, name: info)
    monkeypatch.setattr(MCPService, "refresh", lambda self, name: info)

    created = client.post("/api/v1/mcp-servers/stdio",
                          json={"name": "fs", "command": "npx", "args": ["-y", "server-fs"]})
    assert created.status_code == 201
    assert created.json()["tool_count"] == 1
    assert created.json()["tools"][0]["name"] == "read_file"

    http = client.post("/api/v1/mcp-servers/http", json={"name": "web", "url": "https://mcp.test"})
    assert http.status_code == 201
    assert http.json()["url"] == "https://mcp.test"

    assert [s["name"] for s in client.get("/api/v1/mcp-servers").json()] == ["fs"]
    assert client.get("/api/v1/mcp-servers/fs").json()["status"] == "CONNECTED"
    assert client.post("/api/v1/mcp-servers/fs/refresh").json()["name"] == "fs"


@pytest.mark.parametrize("code, status", [
    ("MCP_ALREADY_EXISTS", 409),
    ("MCP_CONNECT_TIMEOUT", 504),
    ("MCP_CONNECT_CANCELLED", 502),
    ("MCP_UNKNOWN", 500),
])
def test_register_http_mcp_error_mapping(client, monkeypatch, code, status):
    from app.domain.services.mcp_service import MCPService

    def _raise(self, **kw):
        raise AppError(code, "nope")

    monkeypatch.setattr(MCPService, "register_http", _raise)
    resp = client.post("/api/v1/mcp-servers/http", json={"name": "web", "url": "https://mcp.test"})
    assert resp.status_code == status
    assert resp.json()["detail"]["code"] == code


def test_register_stdio_mcp_conflict(client, monkeypatch):
    from app.domain.services.mcp_service import MCPService

    def _raise(self, **kw):
        raise AppError("MCP_ALREADY_EXISTS", "dup")

    monkeypatch.setattr(MCPService, "register_stdio", _raise)
    resp = client.post("/api/v1/mcp-servers/stdio", json={"name": "fs", "command": "npx"})
    assert resp.status_code == 409


# ── 远端 Skill 来源 ──────────────────────────────────────────────────────────

def test_remote_skill_sources_empty_and_not_found(client):
    assert client.get("/api/v1/remote-skill-sources").json() == []
    assert client.get("/api/v1/remote-skill-sources/ghost").status_code == 404
    assert client.delete("/api/v1/remote-skill-sources/ghost").status_code == 404


def test_register_remote_skill_sources(client, monkeypatch):
    from app.domain.services.skill_source_service import RemoteSkillSourceService

    recorded: list = []

    def _register(self, config):
        recorded.append(config)
        return {"source_name": config.source_name, "mcp_type": config.mcp_type,
                "mcp_url": config.mcp_url, "mcp_command": config.mcp_command}

    monkeypatch.setattr(RemoteSkillSourceService, "register", _register)
    monkeypatch.setattr(RemoteSkillSourceService, "list_all",
                        lambda self: [{"source_name": "s1", "mcp_type": "http"}])
    monkeypatch.setattr(RemoteSkillSourceService, "get",
                        lambda self, name: {"source_name": name, "mcp_type": "http"})
    monkeypatch.setattr(RemoteSkillSourceService, "delete", lambda self, name: None)

    http = client.post("/api/v1/remote-skill-sources/http",
                       json={"source_name": "s1", "mcp_url": "https://skills.test"})
    assert http.status_code == 201
    assert http.json()["mcp_url"] == "https://skills.test"
    # unspecified tool names fall back to the protocol defaults
    assert http.json()["mcp_tool_list_skills"] == "listSkills"

    stdio = client.post("/api/v1/remote-skill-sources/stdio",
                        json={"source_name": "s2", "mcp_command": "npx", "mcp_args": ["-y", "pkg"]})
    assert stdio.status_code == 201
    assert stdio.json()["mcp_command"] == "npx"
    assert [c.mcp_type for c in recorded] == ["http", "stdio"]

    assert [s["source_name"] for s in client.get("/api/v1/remote-skill-sources").json()] == ["s1"]
    assert client.get("/api/v1/remote-skill-sources/s1").json()["source_name"] == "s1"
    assert client.delete("/api/v1/remote-skill-sources/s1").status_code == 204


@pytest.mark.parametrize("code, status", [
    ("SKILL_SOURCE_ALREADY_EXISTS", 409),
    ("SKILL_SOURCE_MISSING_TOOLS", 422),
    ("SKILL_SOURCE_VALIDATION_FAILED", 502),
    ("BOOM", 500),
])
def test_register_remote_skill_source_error_mapping(client, monkeypatch, code, status):
    from app.domain.services.skill_source_service import RemoteSkillSourceService

    def _raise(self, config):
        raise AppError(code, "nope")

    monkeypatch.setattr(RemoteSkillSourceService, "register", _raise)
    resp = client.post("/api/v1/remote-skill-sources/http",
                       json={"source_name": "s1", "mcp_url": "https://skills.test"})
    assert resp.status_code == status
