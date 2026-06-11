"""Tests for HITL user-answer persistence into agent memory.

Covers the full path of the `pending_user_answer` field:

  1. Task model            — to_dict / from_dict round-trip + default.
  2. _ask_human            — pauses the session (WAITING_INPUT → RUNNING),
                             returns the raw user answer, and defaults a blank
                             prompt to a non-empty 'user_input' question.
  3. _write_execution_memory — for a continuing (PENDING) round writes NOTHING to memory
                             (output + human answer are carried by execution_rounds and
                             rendered next round); it only clears + persists the stashed
                             field. Terminal rounds still land the output in memory.
  4. AgentLoop.run start    — a stale pending_user_answer from a prior cycle is
                             cleared when the task is restarted.

The fakes mimic the *real* TaskService persistence semantics: get() returns a
fresh copy (Task.from_dict) and transition reload-from-store then save — so a
field set only on the in-memory ctx.task would be lost. That is exactly the
subtlety the ask_human stash logic must defeat.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models.task import Task


# ── shared fakes ──────────────────────────────────────────────────────────────

def _make_task(task_id: str = "t1", *, status: str = "TO_BE_OBSERVED",
               session_id: str = "s1", agent_id: str = "a1",
               user_prompt: str | list = "", **overrides) -> Task:
    t = Task(
        id=task_id,
        session_id=session_id,
        creator_agent_id=agent_id,
        assigned_agent_id=agent_id,
        status=status,
        user_prompt=user_prompt,
        title="Test task",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    for k, v in overrides.items():
        setattr(t, k, v)
    return t


class FakeTaskStore:
    """In-memory store with REAL reload semantics: get() returns fresh copies,
    finish/fail/transition reload-from-store then save (mirrors TaskService)."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self.save_calls = 0

    def add(self, task: Task) -> None:
        self._data[task.id] = task.to_dict()

    def save(self, task: Task) -> None:
        self.save_calls += 1
        self._data[task.id] = task.to_dict()

    def get(self, task_id: str, session_id: str | None = None) -> Task:
        return Task.from_dict(self._data[task_id])

    def _transition(self, task_id, status, *, process_report=None, error=None) -> Task:
        t = self.get(task_id)
        t.status = status
        if process_report is not None:
            t.process_report = process_report
        if error is not None:
            t.error = error
        self.save(t)
        return t

    def finish(self, task_id, process_report=None, outputs=None, session_id=None) -> Task:
        return self._transition(task_id, "FINISHED", process_report=process_report)

    def fail(self, task_id, error, session_id=None, process_report=None, task_output=None) -> Task:
        return self._transition(task_id, "FAILED", process_report=process_report, error=error)


class FakeSessionSvc:
    def __init__(self) -> None:
        self.session = SimpleNamespace(id="s1", metadata={}, token_budget=0,
                                       output_tokens_used=0)
        self.transitions: list[str] = []

    def transition(self, session_id, status) -> None:
        self.transitions.append(status)

    def get(self, session_id):
        return self.session

    def save(self, sess) -> None:
        pass


class FakeMemorySvc:
    """Records append_message calls in order."""

    def __init__(self) -> None:
        self.messages: list[SimpleNamespace] = []

    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.messages.append(SimpleNamespace(role=role, content=content, agent_id=agent_id))
        return None

    def count_messages(self, agent_id) -> int:
        return len(self.messages)


class _FakeHitl:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def wait(self, session_id, agent_id, prompt, input_type, timeout=3600.0) -> str:
        return self.answer


class _DummyBus:
    def push(self, *args, **kwargs) -> None:
        pass


@pytest.fixture
def patch_hitl_and_bus(monkeypatch):
    """Patch the module-level get_hitl_store / get_sse_bus used inside
    _ask_human. Returns a setter for the canned answer."""
    holder = {"answer": ""}
    monkeypatch.setattr("app.storage.file.hitl_store.get_hitl_store",
                        lambda: _FakeHitl(holder["answer"]))
    monkeypatch.setattr("app.common.sse_bus.get_sse_bus", lambda: _DummyBus())

    def _set(answer: str) -> None:
        holder["answer"] = answer

    return _set


# ══ 1. Task model round-trip ══════════════════════════════════════════════════

class TestTaskModelField:
    def test_default_is_none(self):
        assert _make_task().pending_user_answer is None

    def test_to_dict_includes_field(self):
        t = _make_task(pending_user_answer="hello")
        assert t.to_dict()["pending_user_answer"] == "hello"

    def test_from_dict_restores_field(self):
        t = _make_task(pending_user_answer="hello")
        restored = Task.from_dict(t.to_dict())
        assert restored.pending_user_answer == "hello"

    def test_from_dict_missing_key_defaults_none(self):
        d = _make_task().to_dict()
        del d["pending_user_answer"]  # simulate old persisted task without the field
        assert Task.from_dict(d).pending_user_answer is None


# ══ 2. _ask_human wait/resume ═════════════════════════════════════════════════

class TestAskHuman:
    def _run(self, answer, prompt, patch_set):
        from app.tools.control_tools import _ask_human
        patch_set(answer)
        task = _make_task(process_report=prompt)
        sess = FakeSessionSvc()
        returned = _ask_human(task, prompt, session_svc=sess)
        return returned, sess

    def test_returns_raw_answer(self, patch_hitl_and_bus):
        answer = "用户的回答内容"
        returned, _ = self._run(answer, "need input on X", patch_hitl_and_bus)
        assert returned == answer

    def test_session_toggles_waiting_then_running(self, patch_hitl_and_bus):
        _, sess = self._run("anything", "ask something", patch_hitl_and_bus)
        assert sess.transitions == ["WAITING_INPUT", "RUNNING"]

    def test_frontend_prompt_is_empty(self, monkeypatch):
        """Under the ask_human task status, the process report is internal: the
        frontend-facing prompt (and the stored HITL prompt) must be empty, since
        the task's plain-text output is the actual user-facing question."""
        from app.tools.control_tools import _ask_human
        captured = {}

        class _CapturingHitl(_FakeHitl):
            def wait(self, session_id, agent_id, prompt, input_type, timeout=3600.0):
                captured.update(prompt=prompt, input_type=input_type)
                return self.answer

        monkeypatch.setattr("app.storage.file.hitl_store.get_hitl_store",
                            lambda: _CapturingHitl("ans"))
        monkeypatch.setattr("app.common.sse_bus.get_sse_bus", lambda: _DummyBus())

        _ask_human(_make_task(), "internal process report", session_svc=FakeSessionSvc())
        assert captured["prompt"] == ""  # process report is never shown to the user
        assert captured["input_type"] == "user_input"

    def test_no_context_message_pushed(self, monkeypatch):
        """No assistant 'message' (context) should be pushed for ask_human status —
        only an empty waiting_input to re-enable the input box."""
        from app.tools.control_tools import _ask_human
        pushed = []

        class _RecordingBus:
            def push(self, session_id, event) -> None:
                pushed.append(event)

        monkeypatch.setattr("app.storage.file.hitl_store.get_hitl_store",
                            lambda: _FakeHitl("ans"))
        monkeypatch.setattr("app.common.sse_bus.get_sse_bus", lambda: _RecordingBus())

        _ask_human(_make_task(), "internal process report", session_svc=FakeSessionSvc())
        types = [e["type"] for e in pushed]
        assert "message" not in types
        assert "waiting_input" in types
        wi = next(e for e in pushed if e["type"] == "waiting_input")
        assert wi["prompt"] == ""


# ══ 3. _write_execution_memory ordering + clearing ════════════════════════════

class TestWriteExecutionMemory:
    def _loop(self):
        from unittest.mock import MagicMock
        from app.runtime.agent_loop import AgentLoop
        mem = FakeMemorySvc()
        store = FakeTaskStore()
        loop = AgentLoop(
            session_svc=MagicMock(),
            task_svc=store,
            memory_svc=mem,
            blackboard_svc=MagicMock(),
            agent_store=MagicMock(),
            reasoner=MagicMock(),
            actor=MagicMock(),
            observer=MagicMock(),
        )
        return loop, mem, store

    def _verdict(self, summary=""):
        from app.runtime.types import ObserverVerdict
        return ObserverVerdict(summary=summary)

    def test_continuing_hitl_round_writes_nothing_to_memory(self):
        """A continuing (PENDING) ask_human round writes NOTHING to memory: the output
        (the question) and the human answer are carried by execution_rounds and rendered
        next round as 'transcript → human answer'. _write_execution_memory only clears the
        stashed answer (already captured into the round by _append_execution_round)."""
        loop, mem, store = self._loop()
        task = _make_task(status="PENDING", outputs="the question",
                          pending_user_answer="我确认完成")
        store.add(task)
        loop._write_execution_memory("a1", task, self._verdict("judgment"), "s1", "t1")

        assert mem.messages == []          # nothing double-written into memory
        assert task.pending_user_answer is None

    def test_field_cleared_and_persisted(self):
        loop, mem, store = self._loop()
        task = _make_task(status="PENDING", outputs="out", pending_user_answer="ans")
        store.add(task)
        loop._write_execution_memory("a1", task, self._verdict("s"), "s1", "t1")
        assert task.pending_user_answer is None
        assert store.save_calls == 1
        assert store.get("t1").pending_user_answer is None  # persisted
        assert mem.messages == []          # continuing round writes nothing to memory

    def test_continuing_active_round_skips_output_write(self):
        """A continuing (PENDING) active round (no human answer) also writes nothing to
        memory — its output is carried by execution_rounds. No clear-save either."""
        loop, mem, store = self._loop()
        task = _make_task(status="PENDING", outputs="partial answer")
        store.add(task)
        loop._write_execution_memory("a1", task, self._verdict("continue Y"), "s1", "t1")
        assert mem.messages == []
        assert store.save_calls == 0       # no clear-save when nothing to clear

    def test_terminal_round_writes_output_to_memory(self):
        """A terminal (FINISHED) round still lands the final output in memory so the
        result propagates."""
        loop, mem, store = self._loop()
        task = _make_task(status="FINISHED", outputs="final answer")
        store.add(task)
        loop._write_execution_memory("a1", task, self._verdict("done"), "s1", "t1")
        assert [m.role for m in mem.messages] == ["assistant"]
        assert mem.messages[0].content == "final answer"
        assert store.save_calls == 0       # no pending answer to clear


# ══ 4. AgentLoop.run clears a stale field on restart ══════════════════════════

class _Sentinel(Exception):
    pass


class FakeAgentStore:
    def __init__(self, agent_dict) -> None:
        self._d = agent_dict

    def get(self, session_id, agent_id):
        return dict(self._d)

    def save(self, data) -> None:
        self._d = dict(data)


class TestRunClearsStaleAnswer:
    def _agent_dict(self):
        from app.domain.models.agent import Agent
        return Agent(id="a1", session_id="s1", template_id=None,
                     name="t", status="RUNNING").to_dict()

    def test_stale_pending_answer_cleared_at_task_start(self):
        from unittest.mock import MagicMock
        from app.runtime.agent_loop import AgentLoop

        store = FakeTaskStore()
        # a leftover answer from a previous cycle, plus user_prompt="" to skip the
        # user_prompt-persist branch (which needs the real prompt builder).
        store.add(_make_task("t1", status="PENDING", user_prompt="",
                             pending_user_answer="STALE"))

        reasoner = MagicMock()
        reasoner.reason.side_effect = _Sentinel  # bail right after the clear

        loop = AgentLoop(
            session_svc=FakeSessionSvc(),
            task_svc=store,
            memory_svc=FakeMemorySvc(),
            blackboard_svc=MagicMock(),
            agent_store=FakeAgentStore(self._agent_dict()),
            reasoner=reasoner,
            actor=MagicMock(),
            observer=MagicMock(),
        )

        with pytest.raises(_Sentinel):
            loop.run("s1", "a1", "t1")

        assert store.get("t1").pending_user_answer is None

    def test_no_save_when_field_already_clean(self):
        from unittest.mock import MagicMock
        from app.runtime.agent_loop import AgentLoop

        store = FakeTaskStore()
        store.add(_make_task("t1", status="PENDING", user_prompt=""))  # field is None

        reasoner = MagicMock()
        reasoner.reason.side_effect = _Sentinel

        loop = AgentLoop(
            session_svc=FakeSessionSvc(),
            task_svc=store,
            memory_svc=FakeMemorySvc(),
            blackboard_svc=MagicMock(),
            agent_store=FakeAgentStore(self._agent_dict()),
            reasoner=reasoner,
            actor=MagicMock(),
            observer=MagicMock(),
        )

        with pytest.raises(_Sentinel):
            loop.run("s1", "a1", "t1")

        # no extra clear-save happened for an already-clean field
        assert store.save_calls == 0
