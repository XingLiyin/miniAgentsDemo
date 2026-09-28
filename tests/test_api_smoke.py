"""Smoke tests for the simple read/write routes: health, workspace, tasks,
memories, tool-call audit."""
from __future__ import annotations


# ── /health ──────────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── /api/v1/workspace ────────────────────────────────────────────────────────

def test_list_files_relative_to_workspace_root(client, api_env):
    (api_env / "workspace" / "proj").mkdir()
    (api_env / "workspace" / "proj" / "a.txt").write_text("hello", encoding="utf-8")
    (api_env / "workspace" / ".hidden").write_text("x", encoding="utf-8")

    body = client.get("/api/v1/workspace/files").json()
    assert [e["name"] for e in body["entries"]] == ["proj"]  # dotfiles filtered out
    assert body["path"] == ""

    body = client.get("/api/v1/workspace/files", params={"path": "proj"}).json()
    assert [e["name"] for e in body["entries"]] == ["a.txt"]
    assert body["entries"][0]["size"] == 5
    assert body["path"] == "proj"
    assert body["parent"] == ""


def test_list_files_absolute_path_mode(client, api_env):
    (api_env / "outside").mkdir()
    (api_env / "outside" / "sub").mkdir()

    body = client.get("/api/v1/workspace/files", params={"path": str(api_env / "outside")}).json()
    assert body["root"] == str(api_env / "outside")
    # absolute mode keeps absolute paths for child entries so navigation continues
    assert body["entries"][0]["path"] == str(api_env / "outside" / "sub")


def test_list_files_missing_dir_returns_empty(client):
    body = client.get("/api/v1/workspace/files", params={"path": "nope"}).json()
    assert body["entries"] == []


def test_list_files_rejects_file_and_escape(client, api_env):
    (api_env / "workspace" / "f.txt").write_text("x", encoding="utf-8")
    assert client.get("/api/v1/workspace/files", params={"path": "f.txt"}).status_code == 400
    assert client.get("/api/v1/workspace/files", params={"path": "../.."}).status_code == 403


def test_read_file(client, api_env):
    (api_env / "workspace" / "f.txt").write_text("content", encoding="utf-8")

    resp = client.get("/api/v1/workspace/file", params={"path": "f.txt"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "content"

    abs_resp = client.get("/api/v1/workspace/file", params={"path": str(api_env / "workspace" / "f.txt")})
    assert abs_resp.json()["content"] == "content"

    assert client.get("/api/v1/workspace/file", params={"path": "missing.txt"}).status_code == 404


def test_read_file_too_large(client, api_env):
    (api_env / "workspace" / "big.txt").write_bytes(b"x" * 1_048_577)
    assert client.get("/api/v1/workspace/file", params={"path": "big.txt"}).status_code == 413


def test_read_file_raw(client, api_env):
    (api_env / "workspace" / "p.json").write_text("{}", encoding="utf-8")

    resp = client.get("/api/v1/workspace/file/raw", params={"path": "p.json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.content == b"{}"

    assert client.get("/api/v1/workspace/file/raw", params={"path": "missing"}).status_code == 404


# ── /api/v1/tasks ────────────────────────────────────────────────────────────

def test_get_task(client):
    from app.api.v1.deps import get_task_service

    task = get_task_service().create(
        session_id="ses_1", creator_agent_id="agt_1", user_prompt="do it", title="T",
    )
    resp = client.get(f"/api/v1/tasks/{task.id}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "T"
    assert resp.json()["status"] == "PENDING"


def test_get_task_not_found(client):
    resp = client.get("/api/v1/tasks/tsk_missing")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "TASK_NOT_FOUND"


# ── /api/v1/memories ─────────────────────────────────────────────────────────

def test_append_and_list_agent_messages(client):
    resp = client.post(
        "/api/v1/memories/agents/agt_1/messages",
        json={"role": "user", "content": "hi", "task_id": "tsk_1"},
    )
    assert resp.status_code == 201
    assert resp.json()["content"] == "hi"

    listed = client.get("/api/v1/memories/agents/agt_1/messages", params={"limit": 10}).json()
    assert [m["content"] for m in listed] == ["hi"]


def test_list_session_messages_spans_all_agents(client):
    from app.storage.file.agent_store import AgentStore

    store = AgentStore()
    for agent_id in ("agt_a", "agt_b"):
        store.save({"id": agent_id, "session_id": "ses_1"})
        client.post(f"/api/v1/memories/agents/{agent_id}/messages",
                    json={"role": "user", "content": agent_id})

    listed = client.get("/api/v1/memories/sessions/ses_1/messages").json()
    assert sorted(m["content"] for m in listed) == ["agt_a", "agt_b"]


def test_get_summary(client):
    from app.api.v1.deps import get_memory_service
    from app.domain.models.memory import MemorySummary

    assert client.get("/api/v1/memories/agents/agt_1/summary").status_code == 404

    get_memory_service().save_summary("agt_1", MemorySummary(
        session_id="ses_1", agent_id="agt_1", summary_text="so far", covered_up_to=3,
    ))
    body = client.get("/api/v1/memories/agents/agt_1/summary").json()
    assert body["summary_text"] == "so far"
    assert body["covered_up_to"] == 3


# ── /api/v1/tools ────────────────────────────────────────────────────────────

def test_list_tool_calls_keeps_only_final_record(client):
    from app.storage.file.tool_call_store import ToolCallStore

    store = ToolCallStore()
    base = {
        "id": "tc_1", "session_id": "ses_1", "task_id": "tsk_1", "agent_id": "agt_1",
        "tool_name": "bash_exec", "arguments": {"cmd": "ls"},
        "started_at": "2026-01-01T00:00:00Z", "finished_at": "",
    }
    store.append("ses_1", {**base, "status": "RUNNING"})
    store.append("ses_1", {**base, "status": "SUCCEEDED", "result": "ok",
                           "finished_at": "2026-01-01T00:00:01Z"})

    assert client.get("/api/v1/tools/sessions/ses_other/tool-calls").json() == []

    records = client.get("/api/v1/tools/sessions/ses_1/tool-calls").json()
    assert len(records) == 1
    assert records[0]["status"] == "SUCCEEDED"
