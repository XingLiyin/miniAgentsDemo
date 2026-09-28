"""Tests for the LLM registry layer.

Replaces test_phase_implementations.py, which targeted removed code (SecretString,
app/runtime/compaction.py, the agent_framework-backed app/llm/client.py). Covers:
  ProviderRegistry  (app/llm/provider_registry.py)
  ModelConfig / LLMProvider / LLMRegistry (app/llm/registry.py)
  MockChatClient    (app/llm/mock_client.py)
  HttpxTransport    (app/llm/transport_httpx.py)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.llm.anthropic_adapter import AnthropicAdapter
from app.llm.base import BaseChatClient
from app.llm.mock_client import MockChatClient
from app.llm.openai_adapter import OpenAIAdapter
from app.llm.provider_registry import ProviderInfo, ProviderRegistry
from app.llm.registry import (
    SUPPORTED_LLM_STYLES, LLMProvider, LLMRegistry, ModelConfig,
    get_llm_registry, get_llm_registry_client,
)
from app.llm.transport_httpx import HttpxTransport


def _transport() -> MagicMock:
    return MagicMock()


def _provider(name="gpt", style="openai", models=None, **kw) -> LLMProvider:
    return LLMProvider(
        name=name, style=style, api_key="sk-test",
        base_url="https://api.openai.com",
        models=models if models is not None else [ModelConfig("gpt-4.1-mini", 128_000)],
        **kw,
    )


def _registry() -> tuple[LLMRegistry, MagicMock, MagicMock]:
    """A registry whose provider registry and config store are both mocks."""
    pr = MagicMock()
    reg = LLMRegistry(pr)
    store = MagicMock()
    store.list_all.return_value = []
    reg._store = store
    return reg, pr, store


# ── ProviderRegistry ──────────────────────────────────────────────────────────

class TestProviderRegistry:
    def test_register_openai_builds_adapter(self):
        pr = ProviderRegistry(_transport())
        pr.register_openai("gpt", "sk", "https://api.openai.com/v1", 30)
        assert isinstance(pr.get("gpt"), OpenAIAdapter)

    def test_register_anthropic_builds_adapter(self):
        pr = ProviderRegistry(_transport())
        pr.register_anthropic("claude", "ant")
        assert isinstance(pr.get("claude"), AnthropicAdapter)

    def test_default_base_urls(self):
        pr = ProviderRegistry(_transport())
        pr.register_openai("gpt", "sk")
        pr.register_anthropic("claude", "ant")
        assert pr.get("gpt")._base_url == "https://api.openai.com"
        assert pr.get("claude")._base_url == "https://api.anthropic.com"

    def test_duplicate_openai_raises(self):
        pr = ProviderRegistry(_transport())
        pr.register_openai("gpt", "sk")
        with pytest.raises(KeyError, match="已存在"):
            pr.register_openai("gpt", "sk")

    def test_duplicate_anthropic_raises(self):
        pr = ProviderRegistry(_transport())
        pr.register_anthropic("c", "k")
        with pytest.raises(KeyError, match="已存在"):
            pr.register_anthropic("c", "k")

    def test_cross_style_name_collision_raises(self):
        pr = ProviderRegistry(_transport())
        pr.register_openai("dup", "sk")
        with pytest.raises(KeyError):
            pr.register_anthropic("dup", "ant")

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="未注册"):
            ProviderRegistry(_transport()).get("ghost")

    def test_delete_removes_and_is_idempotent(self):
        pr = ProviderRegistry(_transport())
        pr.register_openai("gpt", "sk")
        pr.delete("gpt")
        pr.delete("gpt")
        with pytest.raises(KeyError):
            pr.get("gpt")

    def test_list_providers_reports_styles(self):
        pr = ProviderRegistry(_transport())
        pr.register_openai("gpt", "sk")
        pr.register_anthropic("claude", "ant")
        listed = {p.name: p.style for p in pr.list_providers()}
        assert listed == {"gpt": "openai", "claude": "anthropic"}

    def test_list_providers_reports_custom_for_untagged(self):
        pr = ProviderRegistry(_transport())
        pr._providers["odd"] = MagicMock()          # registered outside either helper
        assert pr.list_providers()[0].style == "custom"

    def test_empty_listing(self):
        assert ProviderRegistry(_transport()).list_providers() == []

    def test_provider_info_is_frozen(self):
        info = ProviderInfo(name="n", style="openai")
        with pytest.raises(Exception):
            info.name = "other"


# ── ModelConfig / LLMProvider ─────────────────────────────────────────────────

class TestModelConfig:
    def test_roundtrip(self):
        m = ModelConfig("gpt-4", 128_000, 4096)
        assert ModelConfig.from_dict(m.to_dict()) == m

    def test_max_output_tokens_default(self):
        assert ModelConfig("m", 1000).max_output_tokens == 8192

    def test_from_dict_defaults_max_output_tokens(self):
        m = ModelConfig.from_dict({"name": "m", "context_limit": 5})
        assert m.max_output_tokens == 8192


class TestLLMProviderSerialization:
    def test_roundtrip(self):
        p = _provider(default_model="gpt-4.1-mini", timeout_sec=99)
        back = LLMProvider.from_dict(p.to_dict())
        assert back == p

    def test_defaults(self):
        p = LLMProvider(name="n", style="openai", api_key="k", base_url="u")
        assert p.models == [] and p.default_model == "" and p.timeout_sec == 60

    def test_from_dict_tolerates_missing_optionals(self):
        p = LLMProvider.from_dict(
            {"name": "n", "style": "openai", "api_key": "k", "base_url": "u"})
        assert p.models == [] and p.default_model == "" and p.timeout_sec == 60

    def test_supported_styles(self):
        assert SUPPORTED_LLM_STYLES == {"openai", "anthropic"}


# ── LLMRegistry: register / delete ────────────────────────────────────────────

class TestRegister:
    def test_registers_and_connects_openai(self):
        reg, pr, store = _registry()
        reg.register(_provider())
        assert reg.is_registered("gpt")
        assert pr.register_openai.call_args.kwargs["api_key"] == "sk-test"
        assert store.save.called

    def test_registers_and_connects_anthropic(self):
        reg, pr, _ = _registry()
        reg.register(_provider(name="claude", style="anthropic"))
        assert pr.register_anthropic.call_args.kwargs["name"] == "claude"

    def test_persist_false_skips_store(self):
        reg, _, store = _registry()
        reg.register(_provider(), persist=False)
        assert not store.save.called

    def test_default_model_inferred_from_first_model(self):
        reg, _, _ = _registry()
        p = _provider(models=[ModelConfig("a", 1), ModelConfig("b", 2)])
        reg.register(p, persist=False)
        assert p.default_model == "a"

    def test_explicit_default_model_preserved(self):
        reg, _, _ = _registry()
        p = _provider(models=[ModelConfig("a", 1), ModelConfig("b", 2)], default_model="b")
        reg.register(p, persist=False)
        assert p.default_model == "b"

    def test_no_models_leaves_default_empty(self):
        reg, _, _ = _registry()
        p = _provider(models=[])
        reg.register(p, persist=False)
        assert p.default_model == ""

    def test_duplicate_name_raises(self):
        reg, _, _ = _registry()
        reg.register(_provider(), persist=False)
        with pytest.raises(KeyError, match="已存在"):
            reg.register(_provider(), persist=False)

    def test_unsupported_style_raises(self):
        reg, _, _ = _registry()
        with pytest.raises(ValueError, match="不支持的 LLM 风格"):
            reg.register(_provider(style="gemini"), persist=False)

    def test_unsupported_style_is_not_registered(self):
        reg, _, _ = _registry()
        with pytest.raises(ValueError):
            reg.register(_provider(style="gemini"), persist=False)
        assert not reg.is_registered("gpt")


class TestDelete:
    def test_removes_everywhere(self):
        reg, pr, store = _registry()
        reg.register(_provider(), persist=False)
        reg.delete("gpt")
        assert not reg.is_registered("gpt")
        pr.delete.assert_called_once_with("gpt")
        store.delete.assert_called_once_with("gpt")

    def test_unknown_raises(self):
        reg, _, _ = _registry()
        with pytest.raises(KeyError, match="未注册 LLM"):
            reg.delete("ghost")


# ── LLMRegistry: model management ─────────────────────────────────────────────

class TestModelManagement:
    def _reg_with_provider(self, models=None, default_model=""):
        reg, pr, store = _registry()
        p = _provider(models=models if models is not None else [], default_model=default_model)
        reg.register(p, persist=False)
        store.reset_mock()
        return reg, p, store

    def test_add_model_appends_and_persists(self):
        reg, p, store = self._reg_with_provider()
        out = reg.add_model("gpt", "gpt-4o", context_limit=64_000)
        assert out is p
        assert [m.name for m in p.models] == ["gpt-4o"]
        assert p.models[0].context_limit == 64_000
        assert store.save.called

    def test_add_model_sets_default_when_absent(self):
        reg, p, _ = self._reg_with_provider()
        reg.add_model("gpt", "gpt-4o")
        assert p.default_model == "gpt-4o"

    def test_add_model_keeps_existing_default(self):
        reg, p, _ = self._reg_with_provider(
            models=[ModelConfig("first", 1)], default_model="first")
        reg.add_model("gpt", "second")
        assert p.default_model == "first"

    def test_add_duplicate_model_is_noop_on_list(self):
        reg, p, _ = self._reg_with_provider(models=[ModelConfig("dup", 10)])
        reg.add_model("gpt", "dup", context_limit=999)
        assert len(p.models) == 1 and p.models[0].context_limit == 10

    def test_add_model_uses_settings_default_context_limit(self):
        reg, p, _ = self._reg_with_provider()
        with patch("app.config.settings.get_settings",
                   return_value=SimpleNamespace(default_context_limit=4321)):
            reg.add_model("gpt", "m")
        assert p.models[0].context_limit == 4321

    def test_add_model_unknown_provider_raises(self):
        reg, _, _ = _registry()
        with pytest.raises(KeyError):
            reg.add_model("ghost", "m")

    def test_remove_model(self):
        reg, p, store = self._reg_with_provider(
            models=[ModelConfig("a", 1), ModelConfig("b", 2)], default_model="a")
        reg.remove_model("gpt", "a")
        assert [m.name for m in p.models] == ["b"]
        assert p.default_model == "b"          # promoted
        assert store.save.called

    def test_remove_last_model_clears_default(self):
        reg, p, _ = self._reg_with_provider(
            models=[ModelConfig("only", 1)], default_model="only")
        reg.remove_model("gpt", "only")
        assert p.models == [] and p.default_model == ""

    def test_remove_non_default_keeps_default(self):
        reg, p, _ = self._reg_with_provider(
            models=[ModelConfig("a", 1), ModelConfig("b", 2)], default_model="a")
        reg.remove_model("gpt", "b")
        assert p.default_model == "a"

    def test_remove_absent_model_is_noop(self):
        reg, p, _ = self._reg_with_provider(models=[ModelConfig("a", 1)], default_model="a")
        reg.remove_model("gpt", "ghost")
        assert [m.name for m in p.models] == ["a"]

    def test_set_default_model(self):
        reg, p, store = self._reg_with_provider(
            models=[ModelConfig("a", 1), ModelConfig("b", 2)], default_model="a")
        reg.set_default_model("gpt", "b")
        assert p.default_model == "b" and store.save.called

    def test_set_default_model_not_in_list_raises(self):
        reg, _, _ = self._reg_with_provider(models=[ModelConfig("a", 1)])
        with pytest.raises(ValueError, match="不在 provider"):
            reg.set_default_model("gpt", "ghost")


# ── LLMRegistry: queries ──────────────────────────────────────────────────────

class TestGetClient:
    def test_uses_model_config_limits(self):
        reg, pr, _ = _registry()
        adapter = MagicMock()
        pr.get.return_value = adapter
        reg.register(_provider(models=[ModelConfig("gpt-4.1-mini", 111, 222)]), persist=False)
        client = reg.get_client("gpt")
        assert isinstance(client, BaseChatClient)
        assert client.context_limit == 111 and client.max_output_tokens == 222
        pr.get.assert_called_once_with("gpt")

    def test_explicit_model_overrides_default(self):
        reg, pr, _ = _registry()
        pr.get.return_value = MagicMock()
        reg.register(_provider(models=[ModelConfig("a", 1), ModelConfig("b", 500)]),
                     persist=False)
        assert reg.get_client("gpt", "b").context_limit == 500

    def test_unknown_model_falls_back_to_settings(self):
        reg, pr, _ = _registry()
        pr.get.return_value = MagicMock()
        reg.register(_provider(models=[ModelConfig("a", 1)]), persist=False)
        with patch("app.config.settings.get_settings",
                   return_value=SimpleNamespace(default_context_limit=7777)):
            client = reg.get_client("gpt", "unlisted-model")
        assert client.context_limit == 7777 and client.max_output_tokens == 8192

    def test_no_model_available_raises(self):
        reg, _, _ = _registry()
        reg.register(_provider(models=[]), persist=False)
        with pytest.raises(ValueError, match="无可用模型"):
            reg.get_client("gpt")

    def test_unknown_provider_raises(self):
        reg, _, _ = _registry()
        with pytest.raises(KeyError):
            reg.get_client("ghost")


class TestQueries:
    def test_get_provider(self):
        reg, _, _ = _registry()
        p = _provider()
        reg.register(p, persist=False)
        assert reg.get_provider("gpt") is p

    def test_get_provider_unknown_raises(self):
        reg, _, _ = _registry()
        with pytest.raises(KeyError):
            reg.get_provider("ghost")

    def test_list_providers(self):
        reg, _, _ = _registry()
        reg.register(_provider(name="a"), persist=False)
        reg.register(_provider(name="b", style="anthropic"), persist=False)
        assert {p.name for p in reg.list_providers()} == {"a", "b"}

    def test_is_registered(self):
        reg, _, _ = _registry()
        assert not reg.is_registered("gpt")
        reg.register(_provider(), persist=False)
        assert reg.is_registered("gpt")

    def test_get_store_is_lazy_and_cached(self):
        reg = LLMRegistry(MagicMock())
        with patch("app.llm.registry.LLMConfigStore") as cls:
            first = reg._get_store()
            second = reg._get_store()
        assert first is second
        assert cls.call_count == 1


class TestLoadFromStore:
    def test_loads_all_providers(self):
        reg, _, store = _registry()
        store.list_all.return_value = [
            _provider(name="a").to_dict(),
            _provider(name="b", style="anthropic").to_dict(),
        ]
        assert reg.load_from_store() == 2
        assert reg.is_registered("a") and reg.is_registered("b")

    def test_skips_nameless_entries(self):
        reg, _, store = _registry()
        store.list_all.return_value = [{"style": "openai"}]
        assert reg.load_from_store() == 0

    def test_skips_already_registered(self):
        reg, _, store = _registry()
        reg.register(_provider(name="a"), persist=False)
        store.list_all.return_value = [_provider(name="a").to_dict()]
        assert reg.load_from_store() == 0

    def test_bad_entry_is_logged_and_skipped(self):
        reg, _, store = _registry()
        store.list_all.return_value = [
            {"name": "broken", "style": "openai"},        # missing api_key -> KeyError
            _provider(name="ok").to_dict(),
        ]
        assert reg.load_from_store() == 1
        assert reg.is_registered("ok") and not reg.is_registered("broken")

    def test_empty_store(self):
        reg, _, _ = _registry()
        assert reg.load_from_store() == 0


# ── module-level singleton ────────────────────────────────────────────────────

class TestGetLLMRegistry:
    def _settings(self, **kw):
        base = dict(
            default_llm_timeout_sec=60, default_llm_provider="", default_llm_api_key="",
            default_llm_base_url="", default_llm_style="openai", default_llm_model="",
            default_llm_context_limit=200_000, default_llm_max_output_tokens=8192,
            default_context_limit=200_000,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def setup_method(self):
        get_llm_registry.cache_clear()

    def teardown_method(self):
        get_llm_registry.cache_clear()

    def test_builds_registry_and_restores_store(self):
        store = MagicMock()
        store.list_all.return_value = [_provider(name="stored").to_dict()]
        with patch("app.config.settings.get_settings", return_value=self._settings()), \
             patch("app.llm.registry.LLMConfigStore", return_value=store):
            reg = get_llm_registry()
        assert reg.is_registered("stored")

    def test_is_cached(self):
        store = MagicMock()
        store.list_all.return_value = []
        with patch("app.config.settings.get_settings", return_value=self._settings()), \
             patch("app.llm.registry.LLMConfigStore", return_value=store):
            assert get_llm_registry() is get_llm_registry()

    def test_bootstraps_default_provider_from_env(self):
        store = MagicMock()
        store.list_all.return_value = []
        settings = self._settings(
            default_llm_provider="envgpt", default_llm_api_key="sk-env",
            default_llm_base_url="https://env/v1", default_llm_model="env-model",
            default_llm_context_limit=1234, default_llm_max_output_tokens=77,
        )
        with patch("app.config.settings.get_settings", return_value=settings), \
             patch("app.llm.registry.LLMConfigStore", return_value=store):
            reg = get_llm_registry()
        p = reg.get_provider("envgpt")
        assert p.api_key == "sk-env" and p.default_model == "env-model"
        assert p.models[0].context_limit == 1234
        assert p.models[0].max_output_tokens == 77
        assert not store.save.called          # bootstrapped with persist=False

    def test_bootstrap_without_model_has_empty_model_list(self):
        store = MagicMock()
        store.list_all.return_value = []
        settings = self._settings(
            default_llm_provider="envgpt", default_llm_api_key="sk",
            default_llm_base_url="https://env", default_llm_model="",
        )
        with patch("app.config.settings.get_settings", return_value=settings), \
             patch("app.llm.registry.LLMConfigStore", return_value=store):
            reg = get_llm_registry()
        assert reg.get_provider("envgpt").models == []

    def test_no_bootstrap_without_credentials(self):
        store = MagicMock()
        store.list_all.return_value = []
        settings = self._settings(default_llm_provider="envgpt")   # no key/url
        with patch("app.config.settings.get_settings", return_value=settings), \
             patch("app.llm.registry.LLMConfigStore", return_value=store):
            reg = get_llm_registry()
        assert not reg.is_registered("envgpt")

    def test_no_bootstrap_when_already_in_store(self):
        store = MagicMock()
        store.list_all.return_value = [_provider(name="envgpt").to_dict()]
        settings = self._settings(
            default_llm_provider="envgpt", default_llm_api_key="sk-env",
            default_llm_base_url="https://env",
        )
        with patch("app.config.settings.get_settings", return_value=settings), \
             patch("app.llm.registry.LLMConfigStore", return_value=store):
            reg = get_llm_registry()
        assert reg.get_provider("envgpt").api_key == "sk-test"   # the stored one wins

    def test_get_llm_registry_client_delegates(self):
        store = MagicMock()
        store.list_all.return_value = [_provider(name="p").to_dict()]
        with patch("app.config.settings.get_settings", return_value=self._settings()), \
             patch("app.llm.registry.LLMConfigStore", return_value=store):
            client = get_llm_registry_client("p")
        assert isinstance(client, BaseChatClient)


# ── MockChatClient ────────────────────────────────────────────────────────────

class TestMockChatClient:
    def test_is_a_base_chat_client(self):
        assert isinstance(MockChatClient(), BaseChatClient)

    def test_returns_fixed_text(self):
        assert MockChatClient("canned").send_message([]).text == "canned"

    def test_default_text(self):
        assert MockChatClient().send_message([]).text == "mock"


# ── HttpxTransport ────────────────────────────────────────────────────────────

class TestHttpxTransportPost:
    def _client_returning(self, resp):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = resp
        return client

    def test_returns_json_body(self):
        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        client = self._client_returning(resp)
        with patch("httpx.Client", return_value=client), \
             patch("app.common.ssl_verify.with_ssl_retry", side_effect=lambda fn, url: fn(False)):
            out = HttpxTransport().post("https://x/y", {"H": "v"}, {"a": 1}, 5)
        assert out == {"ok": True}
        assert client.post.call_args.kwargs["headers"] == {"H": "v"}
        assert client.post.call_args.kwargs["json"] == {"a": 1}

    def test_falls_back_to_constructor_timeout(self):
        resp = MagicMock()
        resp.json.return_value = {}
        with patch("httpx.Client", return_value=self._client_returning(resp)) as cls, \
             patch("app.common.ssl_verify.with_ssl_retry", side_effect=lambda fn, url: fn(False)):
            HttpxTransport(timeout=42).post("https://x", {}, {}, 0)
        assert cls.call_args.kwargs["timeout"] == 42

    def test_status_error_becomes_runtime_error(self):
        err_resp = MagicMock(status_code=401, text="denied")
        exc = httpx.HTTPStatusError("bad", request=MagicMock(), response=err_resp)
        with patch("app.common.ssl_verify.with_ssl_retry", side_effect=exc):
            with pytest.raises(RuntimeError, match="401"):
                HttpxTransport().post("https://x", {}, {}, 1)

    def test_timeout_becomes_runtime_error(self):
        with patch("app.common.ssl_verify.with_ssl_retry",
                   side_effect=httpx.TimeoutException("slow")):
            with pytest.raises(RuntimeError, match="超时"):
                HttpxTransport().post("https://x", {}, {}, 1)

    def test_request_error_becomes_runtime_error(self):
        with patch("app.common.ssl_verify.with_ssl_retry",
                   side_effect=httpx.RequestError("refused")):
            with pytest.raises(RuntimeError, match="请求失败"):
                HttpxTransport().post("https://x", {}, {}, 1)


class TestHttpxTransportStream:
    def _streaming_client(self, lines, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.text = "error body"
        resp.iter_lines.return_value = iter(lines)
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.stream.return_value = resp
        return client

    def _stream(self, lines, status=200):
        client = self._streaming_client(lines, status)
        with patch("httpx.Client", return_value=client), \
             patch("app.llm.transport_httpx.make_ssl_verify", return_value=False):
            return list(HttpxTransport().stream_post("https://x", {}, {}, 5))

    def test_strips_data_prefix(self):
        assert self._stream(["data: {\"a\":1}", "data: {\"b\":2}"]) == ['{"a":1}', '{"b":2}']

    def test_skips_blank_and_non_data_lines(self):
        assert self._stream(["", "event: ping", "data: payload"]) == ["payload"]

    def test_stops_at_done_sentinel(self):
        assert self._stream(["data: first", "data: [DONE]", "data: never"]) == ["first"]

    def test_http_error_status_raises_with_body(self):
        with pytest.raises(RuntimeError, match="500"):
            self._stream(["data: x"], status=500)

    def test_timeout_becomes_runtime_error(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.stream.side_effect = httpx.TimeoutException("slow")
        with patch("httpx.Client", return_value=client), \
             patch("app.llm.transport_httpx.make_ssl_verify", return_value=False):
            with pytest.raises(RuntimeError, match="流式请求超时"):
                list(HttpxTransport().stream_post("https://x", {}, {}, 1))

    def test_request_error_becomes_runtime_error(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.stream.side_effect = httpx.RequestError("refused")
        with patch("httpx.Client", return_value=client), \
             patch("app.llm.transport_httpx.make_ssl_verify", return_value=False):
            with pytest.raises(RuntimeError, match="流式请求失败"):
                list(HttpxTransport().stream_post("https://x", {}, {}, 1))

    def test_status_error_becomes_runtime_error(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.stream.side_effect = httpx.HTTPStatusError(
            "bad", request=MagicMock(), response=MagicMock(status_code=403))
        with patch("httpx.Client", return_value=client), \
             patch("app.llm.transport_httpx.make_ssl_verify", return_value=False):
            with pytest.raises(RuntimeError, match="403"):
                list(HttpxTransport().stream_post("https://x", {}, {}, 1))
