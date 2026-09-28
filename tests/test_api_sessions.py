"""Smoke tests for /api/v1/sessions and /api/v1/agent-templates.

The agent loop itself is never started: `schedule_loop` is stubbed so these
tests exercise the HTTP layer plus SessionManager bookkeeping only.
"""
from __future__ import annotations

import asyncio

import pytest

SOUL = """---
name: {name}
version: 1.0.0
description: {name} template for tests.
tools:
  required: [ask_human]
---

You are {name}.
"""


@pytest.fixture
def templates(api_env):
    """Two global templates on disk, synced into the store."""
    for name in ("default", "planner"):
        d = api_env / "agents" / name
        d.mkdir()
        (d / "SOUL.md").write_text(SOUL.format(name=name), encoding="utf-8")
    from app.api.v1.deps import get_agent_template_syncer
    return get_agent_template_syncer()


@pytest.fixture
def no_loop(monkeypatch):
    """Stub out loop scheduling and record the calls."""
    from app.orchestrator.session_manager import SessionManager

    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(SessionManager, "schedule_loop",
                        lambda self, sid, aid: scheduled.append((sid, aid)))
    return scheduled


@pytest.fixture
def session(client, templates, no_loop):
    """One created session (root agent + initial task, no loop running)."""
    resp = client.post("/api/v1/sessions", json={
        "user_prompt": "build a thing",
        "initial_task": {"title": "T1", "description": "D1"},
    })
    assert resp.status_code == 202
    return resp.json()


# ── /api/v1/agent-templates ──────────────────────────────────────────────────

def test_list_global_templates(client, templates):
    names = [t["name"] for t in client.get("/api/v1/agent-templates").json()]
    assert sorted(names) == ["default", "planner"]


def test_get_template(client, templates):
    body = client.get("/api/v1/agent-templates/default").json()
    assert body["name"] == "default"
    assert body["scope"] == "global"

    missing = client.get("/api/v1/agent-templates/ghost")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "TEMPLATE_NOT_FOUND"


def test_workspace_templates_override_global(client, templates, api_env):
    ws = api_env / "workspace" / "proj"
    (ws / ".agents" / "default").mkdir(parents=True)
    (ws / ".agents" / "default" / "SOUL.md").write_text(
        SOUL.format(name="default").replace("1.0.0", "9.9.9"), encoding="utf-8")

    listed = client.get("/api/v1/agent-templates", params={"workspace_dir": "proj"}).json()
    by_name = {t["name"]: t for t in listed}
    assert by_name["default"]["scope"] == "workspace"
    assert by_name["default"]["version"] == "9.9.9"
    assert by_name["planner"]["scope"] == "global"

    single = client.get("/api/v1/agent-templates/default", params={"workspace_dir": "proj"}).json()
    assert single["version"] == "9.9.9"


# ── Session 生命周期 ─────────────────────────────────────────────────────────

def test_create_session(client, session, no_loop):
    assert session["status"] == "QUEUED"
    # The 202 body is built before set_root_agent lands, so read it back.
    root_agent_id = client.get("/api/v1/sessions/" + session["id"]).json()["root_agent_id"]
    assert root_agent_id
    assert no_loop == [(session["id"], root_agent_id)]

    tasks = client.get("/api/v1/sessions/" + session["id"] + "/tasks").json()
    assert [t["title"] for t in tasks] == ["T1"]
    assert tasks[0]["assigned_agent_id"] == root_agent_id


def test_create_session_with_subagent_task(client, templates, no_loop):
    resp = client.post("/api/v1/sessions", json={
        "user_prompt": "delegate this",
        "working_dir": "proj",
        "initial_task": {"title": "T", "description": "D", "use_subagent": True},
    })
    assert resp.status_code == 202
    body = resp.json()
    assert body["working_dir"].endswith("proj")

    task = client.get("/api/v1/sessions/" + body["id"] + "/tasks").json()[0]
    assert task["settings"]["use_subagent"] is True
    assert task["settings"]["subagent_template"] == "planner"


def test_list_and_get_session(client, session):
    assert [s["id"] for s in client.get("/api/v1/sessions").json()] == [session["id"]]
    detail = client.get("/api/v1/sessions/" + session["id"]).json()
    assert detail["user_prompt"] == "build a thing"

    missing = client.get("/api/v1/sessions/ses_ghost")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "SESSION_NOT_FOUND"


def test_send_message_rejects_running_session(client, session):
    """A QUEUED session is still busy — the caller must wait."""
    resp = client.post("/api/v1/sessions/" + session["id"] + "/messages",
                       json={"content": "more"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "SESSION_BUSY"


def test_send_message_reopens_finished_session(client, session, no_loop):
    from app.api.v1.deps import get_session_service

    get_session_service().transition(session["id"], "RUNNING")
    get_session_service().transition(session["id"], "SUCCEEDED")

    resp = client.post("/api/v1/sessions/" + session["id"] + "/messages", json={
        "content": "follow up",
        "initial_task": {"title": "T2", "description": "D2"},
        "llm_provider": "p1",
        "llm_model": "m1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["user_prompt"] == "follow up"
    assert body["llm_provider"] == "p1"
    assert len(no_loop) == 2  # create + re-open

    tasks = client.get("/api/v1/sessions/" + session["id"] + "/tasks").json()
    assert sorted(t["title"] for t in tasks) == ["T1", "T2"]


def test_interrupt_session_cancels_pending_tasks(client, session):
    resp = client.post("/api/v1/sessions/" + session["id"] + "/interrupt")
    assert resp.status_code == 200
    assert resp.json()["status"] == "INTERRUPTED"

    tasks = client.get("/api/v1/sessions/" + session["id"] + "/tasks").json()
    assert [t["status"] for t in tasks] == ["CANCELED"]

    # already interrupted → invalid state
    again = client.post("/api/v1/sessions/" + session["id"] + "/interrupt")
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "INVALID_STATE"


def test_cancel_session(client, session):
    resp = client.post("/api/v1/sessions/" + session["id"] + "/cancel")
    assert resp.json()["status"] == "CANCELED"
    assert client.post("/api/v1/sessions/ses_ghost/cancel").status_code == 404


def test_answer_input(client, session):
    from app.api.v1.deps import get_session_service
    from app.storage.file.hitl_store import get_hitl_store

    not_waiting = client.post("/api/v1/sessions/" + session["id"] + "/input",
                              json={"content": "42"})
    assert not_waiting.status_code == 400
    assert not_waiting.json()["detail"]["code"] == "INVALID_STATE"

    get_session_service().transition(session["id"], "RUNNING")
    get_session_service().transition(session["id"], "WAITING_INPUT")

    resp = client.post("/api/v1/sessions/" + session["id"] + "/input", json={"content": "42"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "WAITING_INPUT"  # worker thread flips it back
    assert get_hitl_store().get_pending(session["id"]) is None


def test_delete_session_removes_tasks(client, session):
    assert client.delete("/api/v1/sessions/" + session["id"]).status_code == 204
    assert client.get("/api/v1/sessions").json() == []
    assert client.delete("/api/v1/sessions/" + session["id"]).status_code == 404


# ── SSE ──────────────────────────────────────────────────────────────────────

def test_stream_unknown_session(client):
    assert client.get("/api/v1/sessions/ses_ghost/stream").status_code == 404


def test_stream_initial_snapshot(client, session):
    """Drive the SSE generator directly with an already-disconnected request so
    it emits its snapshot and exits instead of looping."""
    import json

    from app.api.v1.routes.sessions import stream_session_events
    from app.storage.file.event_store import get_event_store

    get_event_store().append(session["id"], {"type": "task_created"})

    class _Disconnected:
        async def is_disconnected(self) -> bool:
            return True

    async def _collect() -> list[dict]:
        resp = await stream_session_events(session["id"], _Disconnected())
        return [json.loads(chunk.removeprefix("data: "))
                async for chunk in resp.body_iterator]

    events = asyncio.run(_collect())
    assert [e["type"] for e in events] == ["init", "history"]
    assert events[0]["session"]["id"] == session["id"]
    assert [t["title"] for t in events[0]["tasks"]] == ["T1"]
    assert "task_created" in [e["type"] for e in events[1]["events"]]
