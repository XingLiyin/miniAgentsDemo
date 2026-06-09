"""Tests for ResourceSummarizer and DescriptionSummaryCache."""

from __future__ import annotations

import re
import threading
import time
from types import SimpleNamespace

import pytest

from app.runtime.resource_summarizer import ResourceSummarizer
from app.runtime.resource_summary_cache import DescriptionSummaryCache


# ── Helpers ─────────────────────────────────────────────────────────────────

_LONG = "word " * 400  # ~400 tokens, well over the default 200 threshold


class _FakeClient:
    """Records calls and returns a canned summary; can block or raise."""

    def __init__(self, text="SHORT SUMMARY", *, raise_exc=False, block=None):
        self.text = text
        self.raise_exc = raise_exc
        self.block = block
        self.calls = 0

    def send_message(self, **kwargs):
        self.calls += 1
        if self.block is not None:
            self.block.wait(timeout=5)
        if self.raise_exc:
            raise RuntimeError("boom")
        return SimpleNamespace(text=self.text)


def _make(monkeypatch, tmp_path, client, **kw) -> ResourceSummarizer:
    cache = DescriptionSummaryCache(tmp_path / "summaries.json")
    summarizer = ResourceSummarizer(cache=cache, llm_provider="fake", **kw)
    fake_registry = SimpleNamespace(get_client=lambda provider, model: client)
    monkeypatch.setattr(
        "app.runtime.resource_summarizer.get_llm_registry",
        lambda: fake_registry,
    )
    return summarizer


# ── Cache ─────────────────────────────────────────────────────────────────────

def test_cache_key_changes_with_content():
    k1 = DescriptionSummaryCache.key("tool", "aaa")
    k2 = DescriptionSummaryCache.key("tool", "bbb")
    k3 = DescriptionSummaryCache.key("other", "aaa")
    assert k1 != k2 and k1 != k3


def test_cache_roundtrip(tmp_path):
    path = tmp_path / "s.json"
    c = DescriptionSummaryCache(path)
    c.put("k", "v")
    assert DescriptionSummaryCache(path).get("k") == "v"  # reloaded from disk


# ── prepare / warm ──────────────────────────────────────────────────────────

def test_short_description_returned_verbatim(monkeypatch, tmp_path):
    client = _FakeClient()
    s = _make(monkeypatch, tmp_path, client)
    assert s.prepare("tool", "tiny description") == "tiny description"
    assert client.calls == 0  # no LLM call for short text


def test_prepare_blocks_and_returns_summary(monkeypatch, tmp_path):
    client = _FakeClient(text="COMPACT")
    s = _make(monkeypatch, tmp_path, client)
    assert s.prepare("tool", _LONG) == "COMPACT"
    assert client.calls == 1


def test_second_prepare_hits_cache(monkeypatch, tmp_path):
    client = _FakeClient(text="COMPACT")
    s = _make(monkeypatch, tmp_path, client)
    s.prepare("tool", _LONG)
    s.prepare("tool", _LONG)
    assert client.calls == 1  # cached, no second LLM call


def test_warm_then_prepare_shares_job(monkeypatch, tmp_path):
    gate = threading.Event()
    client = _FakeClient(text="COMPACT", block=gate)
    s = _make(monkeypatch, tmp_path, client)
    s.warm("tool", _LONG)            # schedules background job, does not block
    time.sleep(0.05)
    gate.set()                        # let the job finish
    assert s.prepare("tool", _LONG) == "COMPACT"
    assert client.calls == 1          # warm + prepare reused one job


def test_failure_falls_back_to_truncation_and_cooldown(monkeypatch, tmp_path):
    client = _FakeClient(raise_exc=True)
    s = _make(monkeypatch, tmp_path, client, cooldown_sec=60)
    out = s.prepare("tool", _LONG)
    assert out.endswith("[auto-summarizing]")  # truncated fallback
    assert len(out) < len(_LONG)
    # within cooldown: no new job scheduled, still truncates
    out2 = s.prepare("tool", _LONG)
    assert out2.endswith("[auto-summarizing]")
    assert client.calls == 1


def test_timeout_falls_back_to_truncation(monkeypatch, tmp_path):
    gate = threading.Event()  # never set → job blocks past wait timeout
    client = _FakeClient(block=gate)
    s = _make(monkeypatch, tmp_path, client, wait_timeout_sec=0.1)
    out = s.prepare("tool", _LONG)
    gate.set()
    assert out.endswith("[auto-summarizing]")


# ── compress_many (批量读路径) ────────────────────────────────────────────────

class _FakeBatchClient:
    """Tool-calling fake: one send_message handles a whole batch, keyed by id."""

    def __init__(self, prefix="C", *, drop_ids=(), raise_exc=False):
        self.calls = 0
        self.prefix = prefix
        self.drop_ids = set(drop_ids)
        self.raise_exc = raise_exc
        self.max_output_tokens = 8192

    def send_message(self, **kwargs):
        self.calls += 1
        if self.raise_exc:
            raise RuntimeError("boom")
        content = kwargs["messages"][0].content
        ids = [int(m) for m in re.findall(r"\[(\d+)\]", content)]
        return SimpleNamespace(text="", _ids=ids)

    def parse_response(self, resp):
        summaries = [
            {"id": i, "summary": f"{self.prefix}{i}"}
            for i in resp._ids if i not in self.drop_ids
        ]
        tc = SimpleNamespace(input={"summaries": summaries})
        return SimpleNamespace(text="", tool_calls=[tc])


def test_compress_many_single_batch(monkeypatch, tmp_path):
    client = _FakeBatchClient(prefix="SUM")
    s = _make(monkeypatch, tmp_path, client)
    items = [("a", _LONG), ("b", _LONG + " x"), ("c", _LONG + " y")]
    res = s.compress_many(items, provider="fake")
    assert client.calls == 1                       # 3 条只调用一次 LLM
    assert res[("a", _LONG)] == "SUM0"
    assert res[("b", _LONG + " x")] == "SUM1"
    assert res[("c", _LONG + " y")] == "SUM2"


def test_compress_many_short_and_cached(monkeypatch, tmp_path):
    client = _FakeBatchClient()
    s = _make(monkeypatch, tmp_path, client)
    s.compress_many([("a", _LONG)], provider="fake")   # 预热缓存
    calls = client.calls
    res = s.compress_many([("a", _LONG), ("short", "tiny")], provider="fake")
    assert res[("short", "tiny")] == "tiny"            # 短描述原样
    assert res[("a", _LONG)] == "C0"                   # 命中缓存
    assert client.calls == calls                        # 无新调用


def test_compress_many_no_provider_returns_raw(tmp_path):
    s = ResourceSummarizer(cache=DescriptionSummaryCache(tmp_path / "s.json"), llm_provider="")
    res = s.compress_many([("a", _LONG)], provider="")
    assert res[("a", _LONG)] == _LONG                  # 无 provider：原样，不截断


def test_compress_many_missing_id_falls_back_to_truncation(monkeypatch, tmp_path):
    client = _FakeBatchClient(drop_ids=(1,))            # 第 2 条缺失
    s = _make(monkeypatch, tmp_path, client)
    items = [("a", _LONG), ("b", _LONG + " x")]
    res = s.compress_many(items, provider="fake")
    assert res[("a", _LONG)] == "C0"
    assert res[("b", _LONG + " x")].endswith("[auto-summarizing]")


def test_compress_many_batch_failure_truncates_all(monkeypatch, tmp_path):
    client = _FakeBatchClient(raise_exc=True)
    s = _make(monkeypatch, tmp_path, client)
    res = s.compress_many([("a", _LONG), ("b", _LONG + " x")], provider="fake")
    assert all(v.endswith("[auto-summarizing]") for v in res.values())
