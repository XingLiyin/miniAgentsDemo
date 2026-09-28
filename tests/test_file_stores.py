"""Tests for the file-backed storage layer (app/storage/file/).

Every store resolves its directory through get_settings().data_dir, so the
`isolated_data_dir` fixture repoints that at tmp_path and clears the class-level
write-through caches that several stores share across instances.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.config.settings import get_settings
from app.storage.file import event_store as event_store_mod
from app.storage.file.agent_store import AgentStore
from app.storage.file.agent_template_store import AgentTemplateStore, make_template_id
from app.storage.file.base import (
    append_jsonl, list_json_ids, read_json, read_jsonl, write_json_atomic,
    write_jsonl_atomic,
)
from app.storage.file.blackboard_store import BlackboardStore
from app.storage.file.event_store import EventStore, get_event_store
from app.storage.file.hitl_store import HitlEntry, HitlStore, get_hitl_store
from app.storage.file.llm_config_store import LLMConfigStore
from app.storage.file.mcp_config_store import MCPConfigStore
from app.storage.file.memory_store import MemoryStore
from app.storage.file.remote_skill_source_store import RemoteSkillSourceStore
from app.storage.file.session_store import SessionStore
from app.storage.file.skill_pull_store import SkillPullStore
from app.storage.file.task_store import TaskStore
from app.storage.file.tool_call_store import ToolCallStore


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Point data_dir at tmp_path and drop every shared class-level cache."""
    monkeypatch.setenv("IPMASTER_COWORK_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    for cls in (TaskStore, SessionStore, AgentStore, MemoryStore):
        cls._cache.clear()
        cls._locks.clear()
    TaskStore._session_index.clear()

    yield tmp_path

    for cls in (TaskStore, SessionStore, AgentStore, MemoryStore):
        cls._cache.clear()
        cls._locks.clear()
    TaskStore._session_index.clear()
    get_settings.cache_clear()


# ── base.py atomic primitives ─────────────────────────────────────────────────

class TestWriteJsonAtomic:
    def test_writes_and_creates_parents(self, tmp_path):
        p = tmp_path / "deep" / "nest" / "f.json"
        write_json_atomic(p, {"a": 1})
        assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}

    def test_leaves_no_tmp_files(self, tmp_path):
        p = tmp_path / "f.json"
        write_json_atomic(p, {"a": 1})
        assert list(tmp_path.glob("*.tmp")) == []

    def test_overwrites(self, tmp_path):
        p = tmp_path / "f.json"
        write_json_atomic(p, {"v": 1})
        write_json_atomic(p, {"v": 2})
        assert read_json(p) == {"v": 2}

    def test_unicode_preserved(self, tmp_path):
        p = tmp_path / "f.json"
        write_json_atomic(p, {"k": "中文"})
        assert "中文" in p.read_text(encoding="utf-8")

    def test_retries_on_permission_error(self, tmp_path, monkeypatch):
        import os as os_mod
        p = tmp_path / "f.json"
        calls = {"n": 0}
        real_replace = os_mod.replace

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked")
            return real_replace(src, dst)

        monkeypatch.setattr("app.storage.file.base.os.replace", flaky)
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        write_json_atomic(p, {"ok": True})
        assert read_json(p) == {"ok": True} and calls["n"] == 3

    def test_gives_up_after_five_attempts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.storage.file.base.os.replace",
                            lambda s, d: (_ for _ in ()).throw(PermissionError("locked")))
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        with pytest.raises(PermissionError):
            write_json_atomic(tmp_path / "f.json", {"a": 1})


class TestReadJson:
    def test_missing_file_is_none(self, tmp_path):
        assert read_json(tmp_path / "nope.json") is None

    def test_reads_content(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        assert read_json(p) == {"a": 1}

    def test_retries_on_permission_error(self, tmp_path, monkeypatch):
        p = tmp_path / "f.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        calls = {"n": 0}
        real = Path.read_text

        def flaky(self, *a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked")
            return real(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", flaky)
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        assert read_json(p) == {"a": 1}

    def test_gives_up_after_five_attempts(self, tmp_path, monkeypatch):
        p = tmp_path / "f.json"
        p.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(Path, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(PermissionError()))
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        with pytest.raises(PermissionError):
            read_json(p)


class TestJsonl:
    def test_write_and_read_roundtrip(self, tmp_path):
        p = tmp_path / "f.jsonl"
        write_jsonl_atomic(p, [{"a": 1}, {"b": 2}])
        assert read_jsonl(p) == [{"a": 1}, {"b": 2}]

    def test_write_empty_list_makes_empty_file(self, tmp_path):
        p = tmp_path / "f.jsonl"
        write_jsonl_atomic(p, [])
        assert p.read_text(encoding="utf-8") == ""
        assert read_jsonl(p) == []

    def test_append_creates_and_accumulates(self, tmp_path):
        p = tmp_path / "sub" / "f.jsonl"
        append_jsonl(p, {"n": 1})
        append_jsonl(p, {"n": 2})
        assert read_jsonl(p) == [{"n": 1}, {"n": 2}]

    def test_read_missing_file_is_empty(self, tmp_path):
        assert read_jsonl(tmp_path / "nope.jsonl") == []

    def test_blank_lines_skipped(self, tmp_path):
        p = tmp_path / "f.jsonl"
        p.write_text('{"a":1}\n\n   \n{"b":2}\n', encoding="utf-8")
        assert read_jsonl(p) == [{"a": 1}, {"b": 2}]

    def test_write_retries_on_permission_error(self, tmp_path, monkeypatch):
        import os as os_mod
        real = os_mod.replace
        calls = {"n": 0}

        def flaky(s, d):
            calls["n"] += 1
            if calls["n"] < 2:
                raise PermissionError()
            return real(s, d)

        monkeypatch.setattr("app.storage.file.base.os.replace", flaky)
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        p = tmp_path / "f.jsonl"
        write_jsonl_atomic(p, [{"a": 1}])
        assert read_jsonl(p) == [{"a": 1}]

    def test_write_gives_up_after_five(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.storage.file.base.os.replace",
                            lambda s, d: (_ for _ in ()).throw(PermissionError()))
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        with pytest.raises(PermissionError):
            write_jsonl_atomic(tmp_path / "f.jsonl", [{"a": 1}])

    def test_read_retries_on_permission_error(self, tmp_path, monkeypatch):
        p = tmp_path / "f.jsonl"
        p.write_text('{"a":1}\n', encoding="utf-8")
        real = Path.read_text
        calls = {"n": 0}

        def flaky(self, *a, **k):
            calls["n"] += 1
            if calls["n"] < 2:
                raise PermissionError()
            return real(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", flaky)
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        assert read_jsonl(p) == [{"a": 1}]

    def test_read_gives_up_after_five(self, tmp_path, monkeypatch):
        p = tmp_path / "f.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(Path, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(PermissionError()))
        monkeypatch.setattr("app.storage.file.base.time.sleep", lambda s: None)
        with pytest.raises(PermissionError):
            read_jsonl(p)


class TestListJsonIds:
    def test_lists_stems(self, tmp_path):
        (tmp_path / "a.json").write_text("{}", encoding="utf-8")
        (tmp_path / "b.json").write_text("{}", encoding="utf-8")
        (tmp_path / "c.txt").write_text("", encoding="utf-8")
        assert sorted(list_json_ids(tmp_path)) == ["a", "b"]

    def test_missing_dir_is_empty(self, tmp_path):
        assert list_json_ids(tmp_path / "nope") == []


# ── SessionStore ──────────────────────────────────────────────────────────────

class TestSessionStore:
    def test_save_and_get(self, isolated_data_dir):
        s = SessionStore()
        s.save({"id": "s1", "status": "RUNNING"})
        assert s.get("s1") == {"id": "s1", "status": "RUNNING"}

    def test_get_returns_a_copy(self, isolated_data_dir):
        s = SessionStore()
        s.save({"id": "s1", "status": "RUNNING"})
        s.get("s1")["status"] = "MUTATED"
        assert s.get("s1")["status"] == "RUNNING"

    def test_cache_shared_across_instances(self, isolated_data_dir):
        SessionStore().save({"id": "s1", "v": 1})
        assert SessionStore().get("s1")["v"] == 1

    def test_get_reads_from_disk_when_not_cached(self, isolated_data_dir):
        s = SessionStore()
        s.save({"id": "s1", "v": 1})
        SessionStore._cache.clear()
        assert s.get("s1")["v"] == 1
        assert "s1" in SessionStore._cache      # now warmed

    def test_missing_is_none(self, isolated_data_dir):
        assert SessionStore().get("ghost") is None

    def test_list_ids(self, isolated_data_dir):
        s = SessionStore()
        s.save({"id": "a"})
        s.save({"id": "b"})
        assert sorted(s.list_ids()) == ["a", "b"]

    def test_list_ids_when_empty(self, isolated_data_dir):
        assert SessionStore().list_ids() == []

    def test_delete(self, isolated_data_dir):
        s = SessionStore()
        s.save({"id": "s1"})
        s.delete("s1")
        assert s.get("s1") is None
        assert not (isolated_data_dir / "sessions" / "s1.json").exists()

    def test_delete_missing_is_safe(self, isolated_data_dir):
        SessionStore().delete("ghost")


# ── TaskStore ─────────────────────────────────────────────────────────────────

class TestTaskStore:
    def _task(self, tid="t1", sid="s1", **kw):
        return {"id": tid, "session_id": sid, "status": "PENDING", **kw}

    def test_save_and_get_with_session(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task())
        assert st.get("t1", "s1")["status"] == "PENDING"

    def test_get_returns_a_copy(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task())
        st.get("t1", "s1")["status"] = "X"
        assert st.get("t1", "s1")["status"] == "PENDING"

    def test_get_from_disk_warms_cache(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task())
        TaskStore._cache.clear()
        TaskStore._session_index.clear()
        assert st.get("t1", "s1")["status"] == "PENDING"
        assert ("s1", "t1") in TaskStore._cache

    def test_get_missing_with_session(self, isolated_data_dir):
        assert TaskStore().get("ghost", "s1") is None

    def test_get_without_session_scans(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task(tid="t9", sid="sX"))
        TaskStore._cache.clear()
        assert st.get("t9")["session_id"] == "sX"

    def test_get_without_session_missing_dir(self, isolated_data_dir):
        assert TaskStore().get("t1") is None

    def test_get_without_session_not_found(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task())
        assert st.get("ghost") is None

    def test_list_by_session(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task("t1"))
        st.save(self._task("t2"))
        assert sorted(st.list_by_session("s1")) == ["t1", "t2"]

    def test_list_by_session_reads_disk_when_index_cold(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task("t1"))
        TaskStore._session_index.clear()
        assert st.list_by_session("s1") == ["t1"]
        assert "s1" in TaskStore._session_index

    def test_list_by_unknown_session(self, isolated_data_dir):
        assert TaskStore().list_by_session("ghost") == []

    def test_delete_with_session(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task())
        st.delete("t1", "s1")
        assert st.get("t1", "s1") is None
        assert st.list_by_session("s1") == []

    def test_delete_with_session_not_indexed(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task())
        TaskStore._session_index.clear()
        st.delete("t1", "s1")
        assert st.get("t1", "s1") is None

    def test_delete_without_session_scans(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task(tid="t7", sid="sY"))
        TaskStore._cache.clear()
        st.delete("t7")
        assert not (isolated_data_dir / "tasks" / "sY" / "t7.json").exists()

    def test_delete_without_session_missing_dir(self, isolated_data_dir):
        TaskStore().delete("t1")

    def test_delete_without_session_no_match(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task())
        st.delete("ghost")
        assert st.get("t1", "s1") is not None

    def test_delete_session_removes_everything(self, isolated_data_dir):
        st = TaskStore()
        st.save(self._task("t1"))
        st.save(self._task("t2"))
        st.save(self._task("other", sid="s2"))
        st.delete_session("s1")
        assert st.list_by_session("s1") == []
        assert not (isolated_data_dir / "tasks" / "s1").exists()
        assert st.get("other", "s2") is not None

    def test_delete_session_when_absent(self, isolated_data_dir):
        TaskStore().delete_session("ghost")

    def test_key_lock_is_per_session_and_reused(self, isolated_data_dir):
        st = TaskStore()
        first = st._key_lock("s1")
        assert st._key_lock("s1") is first
        assert st._key_lock("s2") is not first


# ── AgentStore ────────────────────────────────────────────────────────────────

class TestAgentStore:
    def _agent(self, aid="a1", sid="s1", **kw):
        return {"id": aid, "session_id": sid, "status": "IDLE", **kw}

    def test_save_and_get(self, isolated_data_dir):
        st = AgentStore()
        st.save(self._agent())
        assert st.get("s1", "a1")["status"] == "IDLE"

    def test_get_returns_a_copy(self, isolated_data_dir):
        st = AgentStore()
        st.save(self._agent())
        st.get("s1", "a1")["status"] = "X"
        assert st.get("s1", "a1")["status"] == "IDLE"

    def test_get_from_disk_warms_cache(self, isolated_data_dir):
        st = AgentStore()
        st.save(self._agent())
        AgentStore._cache.clear()
        assert st.get("s1", "a1")["status"] == "IDLE"
        assert ("s1", "a1") in AgentStore._cache

    def test_missing_is_none(self, isolated_data_dir):
        assert AgentStore().get("s1", "ghost") is None

    def test_list_by_session(self, isolated_data_dir):
        st = AgentStore()
        st.save(self._agent("a1"))
        st.save(self._agent("a2"))
        assert sorted(st.list_by_session("s1")) == ["a1", "a2"]

    def test_delete(self, isolated_data_dir):
        st = AgentStore()
        st.save(self._agent())
        st.delete("s1", "a1")
        assert st.get("s1", "a1") is None

    def test_delete_session(self, isolated_data_dir):
        st = AgentStore()
        st.save(self._agent("a1"))
        st.save(self._agent("keep", sid="s2"))
        st.delete_session("s1")
        assert st.list_by_session("s1") == []
        assert st.get("s2", "keep") is not None

    def test_delete_session_when_absent(self, isolated_data_dir):
        AgentStore().delete_session("ghost")


# ── MemoryStore ───────────────────────────────────────────────────────────────

class TestMemoryStore:
    def test_append_and_read(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"role": "user", "content": "hi"})
        m.append_message("a1", {"role": "assistant", "content": "yo"})
        assert [r["content"] for r in m.read_messages("a1")] == ["hi", "yo"]

    def test_read_messages_returns_a_copy(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"role": "user"})
        m.read_messages("a1").append({"injected": True})
        assert len(m.read_messages("a1")) == 1

    def test_append_before_cache_warm_still_reads_from_disk(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"n": 1})
        MemoryStore._cache.clear()
        m.append_message("a1", {"n": 2})
        assert [r["n"] for r in m.read_messages("a1")] == [1, 2]

    def test_read_window(self, isolated_data_dir):
        m = MemoryStore()
        for i in range(5):
            m.append_message("a1", {"n": i})
        assert [r["n"] for r in m.read_window("a1", 2)] == [3, 4]

    def test_read_window_larger_than_history(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"n": 0})
        assert len(m.read_window("a1", 99)) == 1

    def test_count_messages(self, isolated_data_dir):
        m = MemoryStore()
        assert m.count_messages("a1") == 0
        m.append_message("a1", {"n": 1})
        assert m.count_messages("a1") == 1

    def test_summary_roundtrip(self, isolated_data_dir):
        m = MemoryStore()
        assert m.get_summary("a1") is None
        m.save_summary("a1", {"summary_text": "so far"})
        assert m.get_summary("a1")["summary_text"] == "so far"

    def test_bulk_write_replaces_everything(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"n": "old"})
        m.bulk_write_messages("a1", [{"n": "new1"}, {"n": "new2"}])
        assert [r["n"] for r in m.read_messages("a1")] == ["new1", "new2"]

    def test_rewrite_messages_backs_up_the_old_file(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"n": "old"})
        m.rewrite_messages("a1", [{"n": "compacted"}])
        assert [r["n"] for r in m.read_messages("a1")] == ["compacted"]
        bak = isolated_data_dir / "memory" / "a1" / "messages.bak.jsonl"
        assert "old" in bak.read_text(encoding="utf-8")

    def test_rewrite_without_existing_file_skips_backup(self, isolated_data_dir):
        m = MemoryStore()
        m.rewrite_messages("a1", [{"n": 1}])
        assert not (isolated_data_dir / "memory" / "a1" / "messages.bak.jsonl").exists()

    def test_rewrite_appends_to_existing_backup(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"n": "first"})
        m.rewrite_messages("a1", [{"n": "second"}])
        m.rewrite_messages("a1", [{"n": "third"}])
        bak = (isolated_data_dir / "memory" / "a1" / "messages.bak.jsonl").read_text(encoding="utf-8")
        assert "first" in bak and "second" in bak

    def test_delete_agent(self, isolated_data_dir):
        m = MemoryStore()
        m.append_message("a1", {"n": 1})
        m.delete_agent("a1")
        assert m.count_messages("a1") == 0
        assert not (isolated_data_dir / "memory" / "a1").exists()

    def test_delete_absent_agent(self, isolated_data_dir):
        MemoryStore().delete_agent("ghost")


# ── BlackboardStore ───────────────────────────────────────────────────────────

class TestBlackboardStore:
    def test_append_and_read_all(self, isolated_data_dir):
        b = BlackboardStore()
        b.append("s1", "topic", {"n": 1})
        b.append("s1", "topic", {"n": 2})
        assert [e["n"] for e in b.read_all("s1", "topic")] == [1, 2]

    def test_read_all_unknown_topic(self, isolated_data_dir):
        assert BlackboardStore().read_all("s1", "ghost") == []

    def test_read_since(self, isolated_data_dir):
        b = BlackboardStore()
        for i in range(4):
            b.append("s1", "t", {"n": i})
        assert [e["n"] for e in b.read_since("s1", "t", 2)] == [2, 3]

    def test_read_since_beyond_end(self, isolated_data_dir):
        b = BlackboardStore()
        b.append("s1", "t", {"n": 0})
        assert b.read_since("s1", "t", 5) == []

    def test_count(self, isolated_data_dir):
        b = BlackboardStore()
        assert b.count("s1", "t") == 0
        b.append("s1", "t", {"n": 1})
        assert b.count("s1", "t") == 1

    def test_list_topics(self, isolated_data_dir):
        b = BlackboardStore()
        b.append("s1", "alpha", {})
        b.append("s1", "beta", {})
        assert sorted(b.list_topics("s1")) == ["alpha", "beta"]

    def test_list_topics_unknown_session(self, isolated_data_dir):
        assert BlackboardStore().list_topics("ghost") == []

    def test_delete_session(self, isolated_data_dir):
        b = BlackboardStore()
        b.append("s1", "t", {})
        b.delete_session("s1")
        assert b.list_topics("s1") == []

    def test_delete_absent_session(self, isolated_data_dir):
        BlackboardStore().delete_session("ghost")


# ── ToolCallStore ─────────────────────────────────────────────────────────────

class TestToolCallStore:
    def test_append_and_read(self, isolated_data_dir):
        st = ToolCallStore()
        st.append("s1", {"tool": "read"})
        st.append("s1", {"tool": "write"})
        assert [r["tool"] for r in st.read_all("s1")] == ["read", "write"]

    def test_read_unknown_session(self, isolated_data_dir):
        assert ToolCallStore().read_all("ghost") == []

    def test_delete(self, isolated_data_dir):
        st = ToolCallStore()
        st.append("s1", {"tool": "read"})
        st.delete("s1")
        assert st.read_all("s1") == []

    def test_delete_absent(self, isolated_data_dir):
        ToolCallStore().delete("ghost")


# ── LLMConfigStore / MCPConfigStore / RemoteSkillSourceStore ──────────────────

class TestLLMConfigStore:
    def test_roundtrip(self, isolated_data_dir):
        st = LLMConfigStore()
        st.save({"name": "gpt", "style": "openai"})
        assert st.get("gpt")["style"] == "openai"

    def test_missing_is_none(self, isolated_data_dir):
        assert LLMConfigStore().get("ghost") is None

    @pytest.mark.parametrize("name", ["a/b", "a\\b"])
    def test_unsafe_names_sanitised(self, isolated_data_dir, name):
        st = LLMConfigStore()
        st.save({"name": name})
        assert (isolated_data_dir / "llm_configs" / "a_b.json").exists()
        assert st.get(name) is not None

    def test_delete_returns_true_then_false(self, isolated_data_dir):
        st = LLMConfigStore()
        st.save({"name": "gpt"})
        assert st.delete("gpt") is True
        assert st.delete("gpt") is False

    def test_list_all(self, isolated_data_dir):
        st = LLMConfigStore()
        st.save({"name": "a"})
        st.save({"name": "b"})
        assert sorted(c["name"] for c in st.list_all()) == ["a", "b"]

    def test_list_all_when_empty(self, isolated_data_dir):
        assert LLMConfigStore().list_all() == []


class TestMCPConfigStore:
    def test_roundtrip(self, isolated_data_dir):
        st = MCPConfigStore()
        st.save({"name": "fs", "command": "npx"})
        assert st.get("fs")["command"] == "npx"

    def test_missing_is_none(self, isolated_data_dir):
        assert MCPConfigStore().get("ghost") is None

    def test_unsafe_name_sanitised(self, isolated_data_dir):
        st = MCPConfigStore()
        st.save({"name": "org/srv"})
        assert (isolated_data_dir / "mcp_configs" / "org_srv.json").exists()

    def test_delete(self, isolated_data_dir):
        st = MCPConfigStore()
        st.save({"name": "fs"})
        assert st.delete("fs") is True
        assert st.delete("fs") is False

    def test_list_all(self, isolated_data_dir):
        st = MCPConfigStore()
        st.save({"name": "a"})
        assert [c["name"] for c in st.list_all()] == ["a"]

    def test_list_all_when_empty(self, isolated_data_dir):
        assert MCPConfigStore().list_all() == []


class TestRemoteSkillSourceStore:
    def test_roundtrip(self, isolated_data_dir):
        st = RemoteSkillSourceStore()
        st.save({"source_name": "hub", "url": "https://hub"})
        assert st.get("hub")["url"] == "https://hub"

    def test_missing_is_none(self, isolated_data_dir):
        assert RemoteSkillSourceStore().get("ghost") is None

    def test_unsafe_name_sanitised(self, isolated_data_dir):
        st = RemoteSkillSourceStore()
        st.save({"source_name": "a/b"})
        assert (isolated_data_dir / "remote_skill_sources" / "a_b.json").exists()

    def test_delete(self, isolated_data_dir):
        st = RemoteSkillSourceStore()
        st.save({"source_name": "hub"})
        assert st.delete("hub") is True
        assert st.delete("hub") is False

    def test_list_all(self, isolated_data_dir):
        st = RemoteSkillSourceStore()
        st.save({"source_name": "a"})
        assert [c["source_name"] for c in st.list_all()] == ["a"]

    def test_list_all_when_empty(self, isolated_data_dir):
        assert RemoteSkillSourceStore().list_all() == []


# ── SkillPullStore ────────────────────────────────────────────────────────────

class TestSkillPullStore:
    def test_empty_state(self, isolated_data_dir):
        st = SkillPullStore()
        assert st.get_pulled_map() == {}
        assert st.is_pulled("r1") is False

    def test_record_and_query(self, isolated_data_dir):
        st = SkillPullStore()
        st.record_pulled("r1", "folder-1")
        assert st.is_pulled("r1")
        assert st.get_pulled_map() == {"r1": "folder-1"}

    def test_record_is_persisted(self, isolated_data_dir):
        SkillPullStore().record_pulled("r1", "f1")
        assert SkillPullStore().is_pulled("r1")

    def test_remove_pulled(self, isolated_data_dir):
        st = SkillPullStore()
        st.record_pulled("r1", "f1")
        st.remove_pulled("r1")
        assert not st.is_pulled("r1")

    def test_remove_absent_is_safe(self, isolated_data_dir):
        SkillPullStore().remove_pulled("ghost")

    def test_remove_by_folder_removes_all_matching(self, isolated_data_dir):
        st = SkillPullStore()
        st.record_pulled("r1", "shared")
        st.record_pulled("r2", "shared")
        st.record_pulled("r3", "other")
        st.remove_pulled_by_folder("shared")
        assert st.get_pulled_map() == {"r3": "other"}

    def test_remove_by_unknown_folder_is_noop(self, isolated_data_dir):
        st = SkillPullStore()
        st.record_pulled("r1", "f1")
        st.remove_pulled_by_folder("ghost")
        assert st.get_pulled_map() == {"r1": "f1"}


# ── AgentTemplateStore ────────────────────────────────────────────────────────

class TestMakeTemplateId:
    def test_deterministic(self):
        assert make_template_id("global", "", "default") == make_template_id("global", "", "default")

    def test_varies_by_input(self):
        ids = {
            make_template_id("global", "", "a"),
            make_template_id("global", "", "b"),
            make_template_id("workspace", "", "a"),
            make_template_id("global", "/w", "a"),
        }
        assert len(ids) == 4


class TestAgentTemplateStore:
    def _tpl(self, name="default", scope="global", workspace_dir="", tid=None):
        return {
            "id": tid or make_template_id(scope, workspace_dir, name),
            "name": name, "scope": scope, "workspace_dir": workspace_dir,
        }

    def test_roundtrip(self, isolated_data_dir):
        st = AgentTemplateStore()
        t = self._tpl()
        st.save(t)
        assert st.get(t["id"])["name"] == "default"

    def test_missing_is_none(self, isolated_data_dir):
        assert AgentTemplateStore().get("ghost") is None

    def test_delete(self, isolated_data_dir):
        st = AgentTemplateStore()
        t = self._tpl()
        st.save(t)
        assert st.delete(t["id"]) is True
        assert st.delete(t["id"]) is False

    def test_list_ids_and_dicts(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("a"))
        st.save(self._tpl("b"))
        assert len(st.list_ids()) == 2
        assert sorted(d["name"] for d in st.list_all_dicts()) == ["a", "b"]

    def test_list_all_dicts_when_empty(self, isolated_data_dir):
        assert AgentTemplateStore().list_all_dicts() == []

    def test_find_by_name_global(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("planner"))
        assert st.find_by_name("planner")["scope"] == "global"

    def test_find_by_name_prefers_matching_workspace(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("planner"))
        st.save(self._tpl("planner", scope="workspace", workspace_dir="/w"))
        assert st.find_by_name("planner", "/w")["scope"] == "workspace"

    def test_find_by_name_ignores_other_workspace(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("planner"))
        st.save(self._tpl("planner", scope="workspace", workspace_dir="/other"))
        assert st.find_by_name("planner", "/w")["scope"] == "global"

    def test_find_by_name_missing(self, isolated_data_dir):
        assert AgentTemplateStore().find_by_name("ghost") is None

    def test_find_by_name_workspace_only_without_match_is_none(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("p", scope="workspace", workspace_dir="/other"))
        assert st.find_by_name("p", "/w") is None

    def test_list_for_workspace_merges_with_override(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("shared"))
        st.save(self._tpl("shared", scope="workspace", workspace_dir="/w"))
        st.save(self._tpl("global_only"))
        st.save(self._tpl("elsewhere", scope="workspace", workspace_dir="/other"))
        out = {d["name"]: d["scope"] for d in st.list_for_workspace("/w")}
        assert out == {"shared": "workspace", "global_only": "global"}

    def test_list_global(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("g"))
        st.save(self._tpl("w", scope="workspace", workspace_dir="/w"))
        assert [d["name"] for d in st.list_global()] == ["g"]

    def test_scope_defaults_to_global(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save({"id": "x", "name": "n"})            # no scope key
        assert [d["name"] for d in st.list_global()] == ["n"]

    def test_delete_workspace_returns_count(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("a", scope="workspace", workspace_dir="/w"))
        st.save(self._tpl("b", scope="workspace", workspace_dir="/w"))
        st.save(self._tpl("c"))
        assert st.delete_workspace("/w") == 2
        assert [d["name"] for d in st.list_all_dicts()] == ["c"]

    def test_delete_workspace_no_matches(self, isolated_data_dir):
        st = AgentTemplateStore()
        st.save(self._tpl("c"))
        assert st.delete_workspace("/w") == 0


# ── EventStore ────────────────────────────────────────────────────────────────

@pytest.fixture
def event_dir(tmp_path, monkeypatch):
    d = tmp_path / "event_logs"
    monkeypatch.setattr(event_store_mod, "_DATA_DIR", d)
    return d


class TestEventStore:
    def test_persists_whitelisted_type(self, event_dir):
        st = EventStore()
        st.append("s1", {"type": "message", "text": "hi"})
        loaded = st.load("s1")
        assert len(loaded) == 1 and loaded[0]["text"] == "hi"

    def test_stamps_created_at(self, event_dir):
        st = EventStore()
        st.append("s1", {"type": "message"})
        assert "created_at" in st.load("s1")[0]

    def test_keeps_supplied_created_at(self, event_dir):
        st = EventStore()
        st.append("s1", {"type": "message", "created_at": "2020-01-01T00:00:00Z"})
        assert st.load("s1")[0]["created_at"] == "2020-01-01T00:00:00Z"

    def test_does_not_mutate_the_caller_dict(self, event_dir):
        st = EventStore()
        event = {"type": "message"}
        st.append("s1", event)
        assert "created_at" not in event

    @pytest.mark.parametrize("etype", ["ping", "text_delta", "lifecycle", None])
    def test_ephemeral_types_dropped(self, event_dir, etype):
        st = EventStore()
        st.append("s1", {"type": etype} if etype else {})
        assert st.load("s1") == []

    def test_load_unknown_session(self, event_dir):
        assert EventStore().load("ghost") == []

    def test_load_since(self, event_dir):
        st = EventStore()
        for i in range(4):
            st.append("s1", {"type": "message", "n": i})
        assert [e["n"] for e in st.load_since("s1", 2)] == [2, 3]

    def test_load_since_zero_returns_all(self, event_dir):
        st = EventStore()
        st.append("s1", {"type": "message", "n": 0})
        assert len(st.load_since("s1", 0)) == 1

    def test_load_since_negative_returns_all(self, event_dir):
        st = EventStore()
        st.append("s1", {"type": "message", "n": 0})
        assert len(st.load_since("s1", -5)) == 1

    def test_delete(self, event_dir):
        st = EventStore()
        st.append("s1", {"type": "message"})
        st.delete("s1")
        assert st.load("s1") == []

    def test_delete_absent_is_safe(self, event_dir):
        EventStore().delete("ghost")

    def test_append_failure_is_swallowed(self, event_dir, monkeypatch):
        st = EventStore()
        monkeypatch.setattr(event_store_mod, "append_jsonl",
                            lambda p, r: (_ for _ in ()).throw(OSError("disk full")))
        st.append("s1", {"type": "message"})      # must not raise

    def test_load_failure_is_swallowed(self, event_dir, monkeypatch):
        st = EventStore()
        monkeypatch.setattr(event_store_mod, "read_jsonl",
                            lambda p: (_ for _ in ()).throw(OSError("boom")))
        assert st.load("s1") == []

    def test_load_since_failure_is_swallowed(self, event_dir, monkeypatch):
        st = EventStore()
        monkeypatch.setattr(event_store_mod, "read_jsonl",
                            lambda p: (_ for _ in ()).throw(OSError("boom")))
        assert st.load_since("s1", 2) == []

    def test_delete_failure_is_swallowed(self, event_dir, monkeypatch):
        st = EventStore()
        st.append("s1", {"type": "message"})
        monkeypatch.setattr(event_store_mod.os, "remove",
                            lambda p: (_ for _ in ()).throw(OSError("locked")))
        st.delete("s1")

    def test_lock_is_per_session_and_reused(self, event_dir):
        st = EventStore()
        first = st._get_lock("s1")
        assert st._get_lock("s1") is first
        assert st._get_lock("s2") is not first

    def test_get_event_store_is_a_singleton(self, event_dir):
        event_store_mod._event_store = None
        try:
            assert get_event_store() is get_event_store()
        finally:
            event_store_mod._event_store = None


# ── HitlStore ─────────────────────────────────────────────────────────────────

class TestHitlStore:
    def test_no_pending_by_default(self):
        assert HitlStore().get_pending("s1") is None

    def test_submit_without_waiter_returns_none(self):
        assert HitlStore().submit("s1", "answer") is None

    def test_wait_times_out_with_empty_answer(self):
        assert HitlStore().wait("s1", "a1", "prompt", "text", timeout=0.01) == ""

    def test_entry_cleared_after_timeout(self):
        st = HitlStore()
        st.wait("s1", "a1", "p", "text", timeout=0.01)
        assert st.get_pending("s1") is None

    def test_submit_unblocks_the_waiter(self):
        st = HitlStore()
        result = {}

        def waiter():
            result["answer"] = st.wait("s1", "a1", "Continue?", "text", timeout=5)

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        for _ in range(200):                       # wait for registration
            if st.get_pending("s1") is not None:
                break
            threading.Event().wait(0.01)
        entry = st.submit("s1", "yes please")
        t.join(timeout=5)
        assert result["answer"] == "yes please"
        assert entry.agent_id == "a1" and entry.prompt == "Continue?"

    def test_pending_entry_exposes_metadata(self):
        st = HitlStore()
        t = threading.Thread(
            target=lambda: st.wait("s1", "a9", "Pick one", "choice", timeout=2),
            daemon=True)
        t.start()
        for _ in range(200):
            if st.get_pending("s1") is not None:
                break
            threading.Event().wait(0.01)
        pending = st.get_pending("s1")
        assert pending.agent_id == "a9" and pending.input_type == "choice"
        st.submit("s1", "done")
        t.join(timeout=5)

    def test_sessions_are_independent(self):
        st = HitlStore()
        t = threading.Thread(
            target=lambda: st.wait("s1", "a1", "p", "text", timeout=2), daemon=True)
        t.start()
        for _ in range(200):
            if st.get_pending("s1") is not None:
                break
            threading.Event().wait(0.01)
        assert st.get_pending("s2") is None
        st.submit("s1", "x")
        t.join(timeout=5)

    def test_entry_defaults_to_blank_answer(self):
        e = HitlEntry(event=threading.Event(), agent_id="a", prompt="p", input_type="text")
        assert e.answer == ""

    def test_get_hitl_store_is_a_singleton(self):
        assert get_hitl_store() is get_hitl_store()
