"""Smoke tests for /api/v1/llms — provider CRUD, model management, and the
network-backed ping / model-listing endpoints with the transport stubbed out."""
from __future__ import annotations

import pytest


@pytest.fixture
def provider(client):
    """A registered openai-style provider with two models."""
    resp = client.post("/api/v1/llms", json={
        "name": "p1",
        "style": "openai",
        "api_key": "sk-test",
        "base_url": "https://llm.test",
        "models": [{"name": "m1", "context_limit": 1000}, {"name": "m2"}],
    })
    assert resp.status_code == 201
    return resp.json()


# ── Provider CRUD ────────────────────────────────────────────────────────────

def test_register_provider_defaults_first_model(provider):
    assert provider["default_model"] == "m1"
    assert [m["name"] for m in provider["models"]] == ["m1", "m2"]
    # context_limit omitted → settings.default_context_limit
    assert provider["models"][1]["context_limit"] == 200_000


def test_register_provider_conflicts_and_validation(client, provider):
    dup = client.post("/api/v1/llms", json={"name": "p1", "style": "openai", "api_key": "k"})
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "LLM_ALREADY_EXISTS"

    bad = client.post("/api/v1/llms", json={"name": "p2", "style": "gemini", "api_key": "k"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "INVALID_LLM_STYLE"


def test_list_and_get_provider(client, provider):
    assert [p["name"] for p in client.get("/api/v1/llms").json()] == ["p1"]
    assert client.get("/api/v1/llms/p1").json()["base_url"] == "https://llm.test"
    assert client.get("/api/v1/llms/nope").status_code == 404


def test_delete_provider(client, provider):
    assert client.delete("/api/v1/llms/p1").status_code == 204
    assert client.get("/api/v1/llms").json() == []
    assert client.delete("/api/v1/llms/p1").status_code == 404


def test_registered_provider_survives_restart(client, provider):
    """Providers are persisted to data_dir, so a fresh registry reloads them."""
    from app.llm.registry import get_llm_registry

    get_llm_registry.cache_clear()
    assert [p["name"] for p in client.get("/api/v1/llms").json()] == ["p1"]


# ── 模型管理 ─────────────────────────────────────────────────────────────────

def test_add_and_remove_model(client, provider):
    added = client.post("/api/v1/llms/p1/models", json={"model": "m3", "context_limit": 5})
    assert [m["name"] for m in added.json()["models"]] == ["m1", "m2", "m3"]

    removed = client.request("DELETE", "/api/v1/llms/p1/models", json={"model": "m1"})
    assert [m["name"] for m in removed.json()["models"]] == ["m2", "m3"]
    # removing the default promotes the next model
    assert removed.json()["default_model"] == "m2"

    assert client.post("/api/v1/llms/nope/models", json={"model": "m"}).status_code == 404
    assert client.request("DELETE", "/api/v1/llms/nope/models", json={"model": "m"}).status_code == 404


def test_set_default_model(client, provider):
    assert client.put("/api/v1/llms/p1/default_model", json={"model": "m2"}).json()["default_model"] == "m2"

    bad = client.put("/api/v1/llms/p1/default_model", json={"model": "ghost"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "INVALID_MODEL"

    assert client.put("/api/v1/llms/nope/default_model", json={"model": "m"}).status_code == 404


# ── Ping ─────────────────────────────────────────────────────────────────────

def test_ping_unregistered_provider(client, monkeypatch):
    import app.api.v1.routes.llms as llms

    monkeypatch.setattr(llms, "_ping_via_stream", lambda adapter, req: None)
    body = client.post("/api/v1/llms/ping", json={"style": "anthropic", "api_key": "k"}).json()
    assert body["ok"] is True
    assert body["latency_ms"] >= 0


def test_ping_failure_and_bad_style(client, monkeypatch):
    import app.api.v1.routes.llms as llms

    def _boom(adapter, req):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(llms, "_ping_via_stream", _boom)
    failed = client.post("/api/v1/llms/ping", json={"style": "openai", "api_key": "k"})
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"] == "PING_FAILED"

    bad = client.post("/api/v1/llms/ping", json={"style": "nope", "api_key": "k"})
    assert bad.status_code == 400


def test_ping_registered_provider(client, provider, monkeypatch):
    import app.api.v1.routes.llms as llms

    monkeypatch.setattr(llms, "_ping_via_stream", lambda adapter, req: None)
    assert client.post("/api/v1/llms/p1/ping").json()["ok"] is True
    assert client.post("/api/v1/llms/p1/ping", params={"model": "m2"}).json()["ok"] is True
    assert client.post("/api/v1/llms/nope/ping").status_code == 404


# ── 可用模型列举 ─────────────────────────────────────────────────────────────

def test_available_models_unregistered(client, monkeypatch):
    import app.api.v1.routes.llms as llms

    monkeypatch.setattr(llms, "_fetch_available_models", lambda style, key, url: ["a", "b"])
    resp = client.post("/api/v1/llms/available-models",
                       json={"style": "openai", "api_key": "k", "base_url": "https://llm.test"})
    assert resp.json()["models"] == ["a", "b"]

    bad = client.post("/api/v1/llms/available-models", json={"style": "nope", "api_key": "k"})
    assert bad.status_code == 400


def test_available_models_registered_and_failure(client, provider, monkeypatch):
    import app.api.v1.routes.llms as llms

    monkeypatch.setattr(llms, "_fetch_available_models", lambda style, key, url: ["x"])
    assert client.get("/api/v1/llms/p1/available-models").json()["models"] == ["x"]
    assert client.get("/api/v1/llms/nope/available-models").status_code == 404

    def _boom(style, key, url):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(llms, "_fetch_available_models", _boom)
    failed = client.get("/api/v1/llms/p1/available-models")
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"] == "LIST_MODELS_FAILED"


def test_fetch_available_models_parses_and_wraps_errors(client, monkeypatch):
    """`_fetch_available_models` itself: response parsing plus error mapping."""
    import httpx
    import app.api.v1.routes.llms as llms

    def _transport(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["x-api-key"] == "k"
        return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {}]})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(_transport)),
    )
    assert llms._fetch_available_models("anthropic", "k", "") == ["a", "b"]

    monkeypatch.setattr(
        httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(
            lambda req: httpx.Response(500, text="boom"))),
    )
    with pytest.raises(RuntimeError, match="HTTP 500"):
        llms._fetch_available_models("openai", "k", "https://llm.test")
