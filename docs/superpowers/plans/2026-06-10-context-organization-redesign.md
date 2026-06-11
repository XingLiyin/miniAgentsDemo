# 上下文组织机制重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 agent memory 收敛成干净的工具使用对话流，process report 成为 observer-only / 交付用元数据，子任务结果以 `submit_task` 的 tool_result 形式回传。

**Architecture:** 两层 memory——Task 持有逐轮细粒度执行记录（turns + process_report + output），agent memory 只持有跨任务的干净结果与 submit_task 的 tool_call/tool_result pair。actor 续跑时把当前 task 的逐轮记录与 agent memory 按时间戳归并重建；observer 从 task 的逐轮记录读 process report；自交付子任务走 tool_result，tracker/sibling 走起始 user message 注入。

**Tech Stack:** Python 3.11、dataclass 领域模型、pytest、JSONL 文件存储。

参考 spec：`docs/superpowers/specs/2026-06-10-context-organization-redesign-design.md`

---

## 文件结构

- `app/domain/models/task.py` — 新增 `execution_rounds` / `parent_tool_call_id` 字段
- `app/runtime/execution_rounds.py`（**新建**）— 逐轮记录的序列化 / 反序列化为 LLMMessage 的纯函数
- `app/runtime/agent_loop.py` — 观察后追加 round 记录；`_write_execution_memory` 改 output-only；挂起时写 submit tool_call
- `app/runtime/prompt_builder.py` — observer 前序轮改数据源；actor 时间戳归并重建
- `app/tools/control_tools.py` — submit_task/submit_plan 在子任务上写 `parent_tool_call_id`
- `app/orchestrator/task_manager.py` — 自交付走 tool_result；tracker/sibling 走 user message 注入

测试运行统一用：`python -m pytest <path> -v`

---

## Task 1: Task 模型新增字段

**Files:**
- Modify: `app/domain/models/task.py`
- Test: `tests/test_task_execution_rounds_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_task_execution_rounds_model.py
from app.domain.models.task import Task


def _task(**kw) -> Task:
    base = dict(
        id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
        status="PENDING", user_prompt="x", title="T", created_at="", updated_at="",
    )
    base.update(kw)
    return Task(**base)


def test_new_fields_default_empty():
    t = _task()
    assert t.execution_rounds == []
    assert t.parent_tool_call_id is None


def test_roundtrip_serialization():
    t = _task()
    t.execution_rounds = [{"turns": [], "process_report": "r", "output": "o", "ts": "2026-06-10T00:00:00Z"}]
    t.parent_tool_call_id = "tc_123"
    t2 = Task.from_dict(t.to_dict())
    assert t2.execution_rounds == t.execution_rounds
    assert t2.parent_tool_call_id == "tc_123"


def test_from_dict_missing_fields_backward_compatible():
    d = _task().to_dict()
    d.pop("execution_rounds", None)
    d.pop("parent_tool_call_id", None)
    t = Task.from_dict(d)
    assert t.execution_rounds == []
    assert t.parent_tool_call_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_task_execution_rounds_model.py -v`
Expected: FAIL（`TypeError`/`AttributeError`：字段不存在）

- [ ] **Step 3: Add fields + serialization**

在 `app/domain/models/task.py` 的 dataclass 字段区（`retry_count` 附近）加入：

```python
    # 逐轮细粒度执行记录（每项: {"turns": [...], "process_report": str, "output": str|list, "ts": str}）
    execution_rounds: list[dict[str, Any]] = field(default_factory=list)
    # 子任务专用：父 agent 中 submit_task/submit_plan 调用的 tool_call id（submit_plan 多子任务共享）
    parent_tool_call_id: str | None = None
```

在 `to_dict()` 的返回 dict 里（`"retry_count"` 之后）加：

```python
            "execution_rounds": self.execution_rounds,
            "parent_tool_call_id": self.parent_tool_call_id,
```

在 `from_dict()` 的 `cls(...)` 调用里（`retry_count=...` 之后）加：

```python
            execution_rounds=d.get("execution_rounds", []),
            parent_tool_call_id=d.get("parent_tool_call_id"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_task_execution_rounds_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/domain/models/task.py tests/test_task_execution_rounds_model.py
git commit -m "feat(task): add execution_rounds and parent_tool_call_id fields"
```

---

## Task 2: 逐轮记录序列化 / 反序列化纯函数

新建一个纯函数模块，集中 round 记录的构造与「round → actor LLMMessage」的还原逻辑，供 agent_loop（写）和 prompt_builder（读）复用。

**Files:**
- Create: `app/runtime/execution_rounds.py`
- Test: `tests/test_execution_rounds_helpers.py`

设计约定：
- `DELEGATION_TOOLS = {"submit_task", "submit_plan"}` 的工具调用**不**写进 round（它们提升到 agent memory）。
- round → actor 消息：每个 turn 还原 `assistant(content=llm_text, tool_calls=...)` + 每个非委派 tool_call 的 `tool` 结果消息；round 末尾追加一条 `user` 角色的 `## Last round review\n{process_report}`（仅当 process_report 非空）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_execution_rounds_helpers.py
from app.runtime.execution_rounds import (
    DELEGATION_TOOLS, make_round_record, round_to_actor_messages,
)
from app.runtime.types import ConversationTurn, ToolCallRecord


def test_make_round_record_serializes_turns_and_skips_delegation():
    turns = [ConversationTurn(
        round=0, messages_sent=[], llm_text="did stuff",
        tool_calls=[
            ToolCallRecord(tool_name="read", arguments={"p": "f"}, result="body", tool_call_id="tc1"),
            ToolCallRecord(tool_name="submit_task", arguments={"title": "Z"}, result="created", tool_call_id="tc2"),
        ],
    )]
    rec = make_round_record(turns, process_report="rep", output="out", ts="2026-06-10T00:00:00Z")
    assert rec["process_report"] == "rep"
    assert rec["output"] == "out"
    assert rec["ts"] == "2026-06-10T00:00:00Z"
    names = [tc["tool_name"] for t in rec["turns"] for tc in t["tool_calls"]]
    assert "read" in names
    assert "submit_task" not in names  # delegation excluded


def test_round_to_actor_messages_shape():
    rec = {
        "turns": [{
            "llm_text": "did stuff",
            "tool_calls": [{"tool_name": "read", "arguments": {"p": "f"}, "result": "body",
                            "is_error": False, "tool_call_id": "tc1"}],
        }],
        "process_report": "next: do Y",
        "output": "out",
        "ts": "2026-06-10T00:00:00Z",
    }
    msgs = round_to_actor_messages(rec)
    roles = [m.role for m in msgs]
    assert roles == ["assistant", "tool", "user"]
    assert msgs[0].tool_calls[0]["name"] == "read"
    assert msgs[0].tool_calls[0]["id"] == "tc1"
    assert msgs[1].tool_call_id == "tc1"
    assert msgs[1].content == "body"
    assert "## Last round review" in msgs[2].content
    assert "next: do Y" in msgs[2].content


def test_round_to_actor_messages_no_report_omits_review_note():
    rec = {"turns": [{"llm_text": "hi", "tool_calls": []}], "process_report": "", "output": "", "ts": ""}
    msgs = round_to_actor_messages(rec)
    assert [m.role for m in msgs] == ["assistant"]


def test_delegation_tools_constant():
    assert DELEGATION_TOOLS == {"submit_task", "submit_plan"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_rounds_helpers.py -v`
Expected: FAIL（`ModuleNotFoundError: app.runtime.execution_rounds`）

- [ ] **Step 3: Create the module**

```python
# app/runtime/execution_rounds.py
"""逐轮执行记录（Task.execution_rounds）的序列化与还原纯函数。

- make_round_record: 把一次 act() 调用的 ConversationTurn 列表 + process_report + output
  序列化成可持久化的 round dict。委派工具（submit_task/submit_plan）的调用不写入——它们
  提升到 agent memory 作为 tool_call/tool_result pair。
- round_to_actor_messages: 把一条 round dict 还原成 actor 视图的 LLMMessage 列表
  （assistant 回复+tool_calls、tool 结果、末尾 process_report 的 user review note）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.llm.types import LLMMessage

if TYPE_CHECKING:
    from app.runtime.types import ConversationTurn

DELEGATION_TOOLS = {"submit_task", "submit_plan"}


def make_round_record(
    turns: "list[ConversationTurn]",
    process_report: str,
    output: "str | list",
    ts: str,
) -> dict[str, Any]:
    """序列化一次 act() 调用为 round 记录；委派工具调用被剔除。"""
    serialized_turns: list[dict] = []
    for t in turns:
        tcs = [
            {
                "tool_name": tc.tool_name,
                "arguments": tc.arguments,
                "result": tc.result,
                "is_error": tc.is_error,
                "tool_call_id": tc.tool_call_id,
            }
            for tc in t.tool_calls
            if tc.tool_name not in DELEGATION_TOOLS
        ]
        serialized_turns.append({"llm_text": t.llm_text, "tool_calls": tcs})
    return {
        "turns": serialized_turns,
        "process_report": process_report or "",
        "output": output if output is not None else "",
        "ts": ts or "",
    }


def round_to_actor_messages(record: dict[str, Any]) -> list[LLMMessage]:
    """把 round 记录还原成 actor 视图的消息序列。"""
    messages: list[LLMMessage] = []
    for t in record.get("turns", []):
        tool_calls = t.get("tool_calls", [])
        messages.append(LLMMessage(
            role="assistant",
            content=t.get("llm_text", "") or "",
            tool_calls=[
                {"id": tc.get("tool_call_id", ""), "name": tc["tool_name"], "input": tc.get("arguments", {})}
                for tc in tool_calls
            ] or None,
        ))
        for tc in tool_calls:
            result = tc.get("result", "") or ""
            if tc.get("is_error"):
                result = f"[ERROR] {result}"
            messages.append(LLMMessage(
                role="tool", content=result, tool_call_id=tc.get("tool_call_id", ""),
            ))
    report = (record.get("process_report") or "").strip()
    if report:
        messages.append(LLMMessage(role="user", content=f"## Last round review\n{report}"))
    return messages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_rounds_helpers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/runtime/execution_rounds.py tests/test_execution_rounds_helpers.py
git commit -m "feat(runtime): add execution_rounds serialization helpers"
```

---

## Task 3: agent_loop 观察后追加 round 记录 + output-only memory

把「写一条 round 到 task.execution_rounds」和「`_write_execution_memory` 改 output-only」一起做（它们都在 `run()` 的观察后段，改动相邻）。

**Files:**
- Modify: `app/runtime/agent_loop.py`
- Test: `tests/test_agent_loop_execution_memory.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_loop_execution_memory.py
from app.domain.models.task import Task
from app.runtime.agent_loop import AgentLoop
from app.runtime.types import ActorResult, ConversationTurn, ObserverVerdict, ToolCallRecord


class _Mem:
    def __init__(self):
        self.msgs = []
    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append({"role": role, "content": content, "tool_call_id": tool_call_id,
                          "tool_calls": tool_calls, "task_id": task_id})


class _TaskSvc:
    def __init__(self):
        self.saved = []
    def save(self, task):
        self.saved.append(task)


def _loop(mem, task_svc):
    # 只用到 _memory_svc / _task_svc 的方法，其余依赖传 None
    return AgentLoop(None, task_svc, mem, None, None, None, None, None)


def _task():
    return Task(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="TO_BE_OBSERVED", user_prompt="x", title="T", created_at="", updated_at="")


def test_append_round_records_turns_and_report():
    mem, ts = _Mem(), _TaskSvc()
    task = _task()
    task.outputs = "final out"
    result = ActorResult(
        task_id="t1", success=True, output="final out",
        conversation_turns=[ConversationTurn(
            round=0, messages_sent=[], llm_text="working",
            tool_calls=[ToolCallRecord(tool_name="read", arguments={}, result="r", tool_call_id="tc1")],
        )],
    )
    verdict = ObserverVerdict(summary="all good")
    _loop(mem, ts)._append_execution_round("a1", task, result, verdict, "s1", "t1")
    assert len(task.execution_rounds) == 1
    rec = task.execution_rounds[0]
    assert rec["process_report"] == "all good"
    assert rec["output"] == "final out"
    assert rec["turns"][0]["tool_calls"][0]["tool_name"] == "read"
    assert task in ts.saved


def test_write_execution_memory_is_output_only_no_process_report():
    mem, ts = _Mem(), _TaskSvc()
    task = _task()
    task.outputs = "the answer"
    verdict = ObserverVerdict(summary="did the work")
    _loop(mem, ts)._write_execution_memory("a1", task, verdict, "s1", "t1")
    assert len(mem.msgs) == 1
    assert mem.msgs[0]["role"] == "assistant"
    assert mem.msgs[0]["content"] == "the answer"
    assert "# Process Report" not in str(mem.msgs[0]["content"])


def test_write_execution_memory_skips_when_no_output():
    mem, ts = _Mem(), _TaskSvc()
    task = _task()
    task.outputs = ""
    _loop(mem, ts)._write_execution_memory("a1", task, ObserverVerdict(summary="report only"), "s1", "t1")
    assert mem.msgs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_loop_execution_memory.py -v`
Expected: FAIL（`_append_execution_round` 不存在；`_write_execution_memory` 仍写 process report）

- [ ] **Step 3: Implement**

在 `app/runtime/agent_loop.py` 顶部 import 区加：

```python
from app.common.utils import now_iso
from app.runtime.execution_rounds import make_round_record
```

新增方法（放在 `_write_execution_memory` 之前）：

```python
    def _append_execution_round(self, agent_id: str, task: Task, result: ActorResult,
                                verdict: ObserverVerdict, session_id: str, task_id: str) -> None:
        """把本次 act() 调用的逐轮记录追加进 task.execution_rounds 并持久化。"""
        record = make_round_record(
            result.conversation_turns,
            process_report=verdict.summary or "",
            output=task.outputs,
            ts=now_iso(),
        )
        task.execution_rounds.append(record)
        self._task_svc.save(task)
```

把 `run()` 中这一行：

```python
            self._write_execution_memory(agent_id, task, verdict, session_id, task_id)
```

改成（先存 round，再写 memory）：

```python
            self._append_execution_round(agent_id, task, result, verdict, session_id, task_id)
            self._write_execution_memory(agent_id, task, verdict, session_id, task_id)
```

把 `_write_execution_memory` 改为 **output-only**（替换整个方法体的 memory 构造部分）：

```python
    def _write_execution_memory(self, agent_id: str, task: Task, verdict: ObserverVerdict, session_id: str, task_id: str) -> None:
        """把任务的最终 output 写入 agent memory（不含 process report）。"""
        if task.outputs:
            if isinstance(task.outputs, list):
                output_images = [p for p in task.outputs if p.get("type") == "image"]
                output_text = next((p.get("text", "") for p in task.outputs if p.get("type") == "text"), "")
                if output_images:
                    mem_content: str | list = list(output_images)
                    if output_text:
                        mem_content.append({"type": "text", "text": output_text})
                else:
                    mem_content = output_text
            else:
                mem_content = task.outputs
            if mem_content:
                self._memory_svc.append_message(
                    agent_id=agent_id,
                    role="assistant",
                    content=mem_content,
                    session_id=session_id,
                    task_id=task_id,
                )

        # HITL 确认回答：先写完上面的输出，再补一条 user 消息，保证时序为「输出 → 人类回答」。
        if task.pending_user_answer:
            self._memory_svc.append_message(
                agent_id=agent_id,
                role="user",
                content=task.pending_user_answer,
                session_id=session_id,
                task_id=task_id,
            )
            task.pending_user_answer = None
            self._task_svc.save(task)
```

> 注：测试用 `AgentLoop(None, task_svc, mem, None, ...)` 直接构造，依赖参数顺序为
> `(session_svc, task_svc, memory_svc, blackboard_svc, agent_store, reasoner, actor, observer)`。
> 被测两个方法只用到 `_task_svc` 和 `_memory_svc`，传 None 不影响。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_loop_execution_memory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/runtime/agent_loop.py tests/test_agent_loop_execution_memory.py
git commit -m "feat(agent_loop): record execution rounds on task, write output-only memory"
```

---

## Task 4: observer 前序轮改读 execution_rounds

`_build_prior_progress` 不再从 memory 切 `# Process Report`，改从 `task.execution_rounds` 读每轮的 `process_report` 与 agent 回复；删除 `_split_agent_summary`。

**Files:**
- Modify: `app/runtime/prompt_builder.py`
- Test: `tests/test_observer_prior_progress_from_task.py`
- Update: `tests/test_observer_memory_history.py`（旧测试基于 memory 切分，需改造）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_observer_prior_progress_from_task.py
from types import SimpleNamespace

from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory
from app.runtime.types import ActorResult, ConversationTurn, ReasoningContext


def _task(**kw):
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="TO_BE_OBSERVED", user_prompt="please do X", title="Do X",
                description="Detailed X", created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _build(task, result):
    builder = PromptBuilderFactory.for_observer()
    ctx = ReasoningContext(goal="g", recent_messages=[], blackboard_snippets=[])
    return builder.build_messages(SimpleNamespace(user_prompt="sess"), result, ctx, task, [])


def test_prior_progress_reads_rounds_from_task():
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "asking the question", "tool_calls": []}],
         "process_report": "loaded refs, awaiting answer", "output": "", "ts": "2026-06-10T00:00:01Z"},
    ])
    result = ActorResult(task_id="t1", success=True, output="cur",
                         conversation_turns=[ConversationTurn(round=0, messages_sent=[], llm_text="round B reply")])
    content = str(_build(task, result)[0].content)
    assert "Prior progress" in content
    assert "=== Round 1 ===" in content
    assert "[Agent reply]\nasking the question" in content
    assert "[Process report]\nloaded refs, awaiting answer" in content
    assert "Current turns" in content
    assert "round B reply" in content


def test_first_round_no_prior_progress():
    task = _task(execution_rounds=[])
    content = str(_build(task, ActorResult(task_id="t1", success=True, output="cur"))[0].content)
    assert "Prior progress: none (first round)." in content


def test_multiple_rounds_numbered():
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "r1 reply", "tool_calls": []}], "process_report": "r1 report", "output": "", "ts": "1"},
        {"turns": [{"llm_text": "r2 reply", "tool_calls": []}], "process_report": "r2 report", "output": "", "ts": "2"},
    ])
    content = str(_build(task, ActorResult(task_id="t1", success=True, output="cur"))[0].content)
    assert "=== Round 1 ===" in content and "=== Round 2 ===" in content
    assert content.index("r1 report") < content.index("r2 report")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_observer_prior_progress_from_task.py -v`
Expected: FAIL（`_build_prior_progress` 仍读 ctx.recent_messages）

- [ ] **Step 3: Implement**

在 `app/runtime/prompt_builder.py` 的 `ObserverPromptBuilder.build_messages` 里把：

```python
        prior_progress = self._build_prior_progress(ctx, task)
```

改为：

```python
        prior_progress = self._build_prior_progress(task)
```

替换 `_build_prior_progress` 整个方法（并删除 `_split_agent_summary`）：

```python
    def _build_prior_progress(self, task: "Task") -> str:
        """从 task.execution_rounds 渲染前序轮：每轮 agent 回复 + process report。

        本轮（当前 act() 调用）尚未写入 execution_rounds，所以这里只含真正的前序轮次。
        """
        blocks: list[str] = []
        for i, rec in enumerate(task.execution_rounds, start=1):
            reply = " ".join(t.get("llm_text", "") for t in rec.get("turns", []) if t.get("llm_text")).strip()
            report = (rec.get("process_report") or "").strip()
            section = [f"=== Round {i} ==="]
            if reply:
                section.append(f"[Agent reply]\n{reply}")
            if report:
                section.append(f"[Process report]\n{report}")
            blocks.append("\n".join(section))
        return "\n\n".join(blocks)
```

删除 `_split_agent_summary`（`ObserverPromptBuilder` 内的 staticmethod，连同其 docstring）。

- [ ] **Step 4: Run new test to verify it passes**

Run: `python -m pytest tests/test_observer_prior_progress_from_task.py -v`
Expected: PASS

- [ ] **Step 5: Update the obsolete memory-based test**

`tests/test_observer_memory_history.py` 基于「memory 切 `# Process Report`」的模型已废弃。删除文件，由 `tests/test_observer_prior_progress_from_task.py` 取代：

```bash
git rm tests/test_observer_memory_history.py
```

Run: `python -m pytest tests/ -k observer -v`
Expected: PASS（无残留对 `_split_agent_summary` 的引用）

- [ ] **Step 6: Commit**

```bash
git add app/runtime/prompt_builder.py tests/test_observer_prior_progress_from_task.py
git commit -m "refactor(observer): read prior progress from task.execution_rounds"
```

---

## Task 5: actor 重建当前 task 视图（时间戳归并）

`ActorPromptBuilder.build_messages` 把 agent memory（含 submit pair、跨任务结果）与当前 task 的 `execution_rounds`（细粒度 + process_report review note）按时间戳归并重建。

**Files:**
- Modify: `app/runtime/prompt_builder.py`
- Test: `tests/test_actor_build_messages_merge.py`

设计：
- agent memory 条目排序键 = `m["created_at"]`；execution_rounds 排序键 = `rec["ts"]`。
- execution_rounds 还原用 Task 2 的 `round_to_actor_messages`。
- 归并后再走原有 tail（blackboard / 非 resume 合成 user message）逻辑。
- 稳定排序：同键时 memory 在前、round 在后（用 (key, source_order) 二级键）。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_actor_build_messages_merge.py
from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory
from app.runtime.types import ReasoningContext


def _task(**kw):
    base = dict(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="ACTIVE", user_prompt="do X", title="Do X", description="d",
                user_prompt_in_memory=True, created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def _ctx(recent, task):
    return ReasoningContext(goal="g", recent_messages=recent, blackboard_snippets=[], current_task=task)


def _build(recent, task):
    builder = PromptBuilderFactory.for_actor()
    return builder.build_messages(task, _ctx(recent, task))


def test_rounds_interleave_with_memory_by_timestamp():
    # memory: user prompt (t=1) then a submit pair (t=4, t=6); rounds at t=2 (before submit), t=7 (after)
    recent = [
        {"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1", "created_at": "1"},
        {"role": "assistant", "content": "delegating", "task_id": "t1", "created_at": "4",
         "tool_calls": [{"id": "tc9", "name": "submit_task", "input": {"title": "Z"}}]},
        {"role": "tool", "content": "child output", "task_id": "t1", "created_at": "6", "tool_call_id": "tc9"},
    ]
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "before submit", "tool_calls": []}], "process_report": "", "output": "", "ts": "2"},
        {"turns": [{"llm_text": "after submit", "tool_calls": []}], "process_report": "", "output": "", "ts": "7"},
    ])
    msgs = _build(recent, task)
    texts = [str(m.content) for m in msgs]
    joined = "\n".join(texts)
    # chronological: goal(1) < before submit(2) < delegating(4) < child output(6) < after submit(7)
    assert joined.index("Do X") < joined.index("before submit") < joined.index("delegating") \
        < joined.index("child output") < joined.index("after submit")


def test_process_report_injected_as_user_review_note():
    recent = [{"role": "user", "content": "## Current Goal\nDo X", "task_id": "t1", "created_at": "1"}]
    task = _task(execution_rounds=[
        {"turns": [{"llm_text": "tried", "tool_calls": []}], "process_report": "do Y next", "output": "", "ts": "2"},
    ])
    msgs = _build(recent, task)
    review = [m for m in msgs if m.role == "user" and "## Last round review" in str(m.content)]
    assert review and "do Y next" in str(review[0].content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_actor_build_messages_merge.py -v`
Expected: FAIL（当前 build_messages 不读 execution_rounds，无 review note）

- [ ] **Step 3: Implement**

在 `app/runtime/prompt_builder.py` 顶部 import 区加：

```python
from app.runtime.execution_rounds import round_to_actor_messages
```

在 `ActorPromptBuilder` 的 `build_messages` 开头（resume 与非 resume 分支之前），把「从 ctx.recent_messages 逐条构造 messages」替换为「归并构造」。具体：新增一个私有方法并在两个分支里复用它来生成基础 `messages`。

新增方法：

```python
    def _merge_timeline(self, task: "Task", ctx: "ReasoningContext") -> list[LLMMessage]:
        """把 agent memory 与当前 task 的 execution_rounds 按时间戳归并成基础消息列表。

        排序键: (timestamp, source_order)——source_order memory=0、round=1，保证同刻 memory 在前。
        """
        entries: list[tuple[str, int, LLMMessage]] = []
        for m in ctx.recent_messages:
            entries.append((
                m.get("created_at", "") or "",
                0,
                LLMMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    tool_calls=m.get("tool_calls"),
                    tool_call_id=m.get("tool_call_id", ""),
                    reasoning_content=m.get("reasoning_content"),
                ),
            ))
        for rec in task.execution_rounds:
            ts = rec.get("ts", "") or ""
            for msg in round_to_actor_messages(rec):
                entries.append((ts, 1, msg))
        entries.sort(key=lambda e: (e[0], e[1]))
        return [e[2] for e in entries]
```

把 `build_messages` 的两个分支改为基于 `_merge_timeline`：

resume 分支（`task.user_prompt_in_memory` 为真）开头：

```python
        if task.user_prompt_in_memory:
            messages = self._merge_timeline(task, ctx)
            # suspend 后 resume：末尾是 assistant，追加 blackboard 上下文
            if ctx.blackboard_snippets and messages and messages[-1].role != "user":
                bb = "## Task Background\n" + "\n".join(
                    f"- {content_to_text(s)}" for s in ctx.blackboard_snippets
                )
                messages.append(LLMMessage(role="user", content=bb))
            return messages
```

非 resume 分支：把原来的 `for m in ctx.recent_messages: messages.append(...)` 替换为：

```python
        messages = self._merge_timeline(task, ctx)
```

其余（parts/blackboard/current message 合成）保持不变。

> 注意：execution_rounds 里委派工具已被 Task 2 剔除，submit pair 仅来自 memory，不会重复。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_actor_build_messages_merge.py -v`
Expected: PASS

- [ ] **Step 5: Run the full prompt_builder-related suite**

Run: `python -m pytest tests/ -k "actor or observer or prompt" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/runtime/prompt_builder.py tests/test_actor_build_messages_merge.py
git commit -m "feat(actor): rebuild current-task view by merging memory and execution_rounds"
```

---

## Task 6: submit_task/submit_plan 记录 parent_tool_call_id + agent_loop 写 submit tool_call

挂起时：control_tools 在子任务上写 `parent_tool_call_id`；agent_loop 用真实 tool_call 替换 `_summarize_spawn` 文本写入 agent memory。

**Files:**
- Modify: `app/tools/control_tools.py`、`app/runtime/agent_loop.py`
- Test: `tests/test_submit_task_tool_call_memory.py`

机制说明：
- `CallContext` 已带 `task`；control_tools 在 `submit_task`/`submit_plan` 里能拿到本次调用——但 tool_call_id 在工具 handler 里不可见。改为：control_tools 把新建子任务 id 暂存到 `ctx.task.settings["_pending_child_ids"]`；agent_loop 在挂起后从 `result.tool_calls_made` 找到 submit_* 调用，取其 `tool_call_id`，写 assistant tool_call 进 memory，并回填到这些子任务的 `parent_tool_call_id`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_submit_task_tool_call_memory.py
from app.domain.models.task import Task
from app.runtime.agent_loop import AgentLoop
from app.runtime.types import ActorResult, ToolCallRecord


class _Mem:
    def __init__(self): self.msgs = []
    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append({"role": role, "content": content, "tool_calls": tool_calls})


class _TaskSvc:
    def __init__(self, children): self._c = {c.id: c for c in children}; self.saved = []
    def get(self, tid, sid=None): return self._c[tid]
    def save(self, t): self.saved.append(t); self._c[t.id] = t


def _parent():
    p = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
             status="SUSPENDED", user_prompt="x", title="P", created_at="", updated_at="")
    p.settings = {"_pending_child_ids": ["c1", "c2"]}
    return p


def _child(cid):
    return Task(id=cid, session_id="s1", creator_agent_id="a1", assigned_agent_id="",
                status="PENDING", user_prompt="x", title=cid, created_at="", updated_at="")


def test_suspension_writes_submit_tool_call_and_backfills_children():
    c1, c2 = _child("c1"), _child("c2")
    mem, ts = _Mem(), _TaskSvc([c1, c2])
    parent = _parent()
    result = ActorResult(task_id="p1", success=True, output="delegating",
                         tool_calls_made=[ToolCallRecord(tool_name="submit_task", arguments={"title": "Z"},
                                                         result="created", tool_call_id="tc77")])
    loop = AgentLoop(None, ts, mem, None, None, None, None, None)
    loop._write_suspension_memory("a1", parent, result, "s1", "p1")

    # assistant tool_call written to memory
    tc_msgs = [m for m in mem.msgs if m["role"] == "assistant" and m["tool_calls"]]
    assert tc_msgs and tc_msgs[0]["tool_calls"][0]["name"] == "submit_task"
    assert tc_msgs[0]["tool_calls"][0]["id"] == "tc77"
    # children backfilled with parent_tool_call_id
    assert ts.get("c1").parent_tool_call_id == "tc77"
    assert ts.get("c2").parent_tool_call_id == "tc77"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_submit_task_tool_call_memory.py -v`
Expected: FAIL（`_write_suspension_memory` 仍写 `_summarize_spawn` 文本，无 tool_calls）

- [ ] **Step 3a: control_tools 暂存子任务 id**

在 `app/tools/control_tools.py` 的 `submit_task` 里，创建子任务并完成 tracking 后、设置 SUSPENDED 之前，追加：

```python
    if task is not None:
        pending = task.settings.setdefault("_pending_child_ids", [])
        pending.append(t.id)
```

在 `submit_plan` 里，循环创建完所有子任务后（`for planned ...` 之后、设置 SUSPENDED 之前）追加：

```python
    if task is not None and task_ids:
        pending = task.settings.setdefault("_pending_child_ids", [])
        pending.extend(task_ids)
```

- [ ] **Step 3b: agent_loop 改写 `_write_suspension_memory`**

替换 `_write_suspension_memory`：

```python
    def _write_suspension_memory(self, agent_id: str, task: Task, result: ActorResult, session_id: str, task_id: str) -> None:
        """挂起时把 submit_task/submit_plan 调用写成 assistant tool_call，并回填子任务的 parent_tool_call_id。"""
        from app.runtime.execution_rounds import DELEGATION_TOOLS
        submit_call = next(
            (tc for tc in result.tool_calls_made if tc.tool_name in DELEGATION_TOOLS),
            None,
        )
        pending_ids = list((task.settings or {}).get("_pending_child_ids", []))
        if submit_call is None:
            return
        self._memory_svc.append_message(
            agent_id=agent_id,
            role="assistant",
            content=result.output or "",
            session_id=session_id,
            task_id=task_id,
            tool_calls=[{"id": submit_call.tool_call_id, "name": submit_call.tool_name,
                         "input": submit_call.arguments}],
        )
        for cid in pending_ids:
            try:
                child = self._task_svc.get(cid, session_id)
                child.parent_tool_call_id = submit_call.tool_call_id
                self._task_svc.save(child)
            except Exception:
                logger.warning("AgentLoop: failed to backfill parent_tool_call_id for child %s", cid)
        # 清理暂存，避免下次挂起重复
        if task.settings is not None:
            task.settings.pop("_pending_child_ids", None)
            self._task_svc.save(task)
```

删除模块底部不再使用的 `_summarize_spawn` 函数。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_submit_task_tool_call_memory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/control_tools.py app/runtime/agent_loop.py tests/test_submit_task_tool_call_memory.py
git commit -m "feat(delegation): record submit_task as assistant tool_call, link children"
```

---

## Task 7: 子任务终结回填 submit_task 的 tool_result（自交付）

父恢复时，对自己提交的子任务（有 `parent_tool_call_id`），往父 agent memory 写一条 `role="tool"` 消息，按 `parent_tool_call_id` 聚合 submit_plan 的多子任务为一条。

**Files:**
- Modify: `app/orchestrator/task_manager.py`
- Test: `tests/test_flush_children_as_tool_result.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flush_children_as_tool_result.py
from types import SimpleNamespace
from app.domain.models.task import Task
from app.orchestrator.task_manager import TaskManager


class _Mem:
    def __init__(self): self.msgs = []
    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append(SimpleNamespace(agent_id=agent_id, role=role, content=content,
                                         tool_call_id=tool_call_id))


class _Agents:
    def __init__(self): self._d = {}
    def add(self, aid, tracking=None): self._d[aid] = {"id": aid, "session_id": "s1",
                                                       "status": "WAITING", "tracking_tasks": list(tracking or [])}
    def get(self, sid, aid): d = self._d.get(aid); return dict(d) if d else None
    def save(self, d): self._d[d["id"]] = dict(d)


class _TaskSvc:
    def __init__(self): self._d = {}
    def add(self, t): self._d[t.id] = t.to_dict()
    def get(self, tid, sid=None): return Task.from_dict(self._d[tid])
    def save(self, t): self._d[t.id] = t.to_dict()


def _child(cid, *, assigned="b1", tcid="tc1", output="out", report="rep", status="FINISHED"):
    t = Task(id=cid, session_id="s1", creator_agent_id="a1", assigned_agent_id=assigned,
             status=status, user_prompt="", title=cid, created_at="", updated_at="")
    t.outputs = output; t.process_report = report; t.parent_tool_call_id = tcid
    return t


def _tm(task_svc, agents, mem):
    return TaskManager(task_svc=task_svc, session_svc=SimpleNamespace(),
                       memory_svc=mem, agent_store=agents)


def test_self_submitted_children_delivered_as_tool_result():
    task_svc, agents, mem = _TaskSvc(), _Agents(), _Mem()
    c1 = _child("c1", tcid="tc1", output="o1", report="r1")
    c2 = _child("c2", tcid="tc1", output="o2", report="r2")  # same tool_call → aggregate
    task_svc.add(c1); task_svc.add(c2)
    agents.add("a1", tracking=["c1", "c2"])
    parent = Task(id="p1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                  status="SUSPENDED", user_prompt="", title="P", created_at="", updated_at="")
    task_svc.add(parent)

    _tm(task_svc, agents, mem)._flush_tracking_tasks_to_memory("s1", parent)

    tool_msgs = [m for m in mem.msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "tc1"
    body = str(tool_msgs[0].content)
    assert "o1" in body and "o2" in body
    assert "r1" in body and "r2" in body  # process report allowed in tool_result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_flush_children_as_tool_result.py -v`
Expected: FAIL（当前 `_flush_tracking_tasks_to_memory` 写 assistant 文本、不按 tool_call_id 聚合）

- [ ] **Step 3: Implement**

在 `app/orchestrator/task_manager.py` 改写 `_flush_tracking_tasks_to_memory`：自交付（child 有 `parent_tool_call_id`）的按 tool_call_id 聚合成 tool 消息；其余维持原逻辑。

```python
    def _flush_tracking_tasks_to_memory(self, session_id: str, parent: Task) -> None:
        """父恢复时把 tracking_tasks 中已终结的子任务结果写入父 memory。

        自己提交的子任务（有 parent_tool_call_id）→ 聚合为 submit_* 的 tool_result。
        其余已终结 tracked task → 维持 assistant 文本（兼容旧路径）。
        """
        if not self._agent_store or not self._memory_svc or not parent.assigned_agent_id:
            return
        agent_data = self._agent_store.get(session_id, parent.assigned_agent_id)
        if not agent_data:
            return
        tracking: list[str] = list(agent_data.get("tracking_tasks", []))
        if not tracking:
            return

        flushed: list[str] = []
        # 1) 自交付：按 parent_tool_call_id 分组聚合
        by_call: dict[str, list[Task]] = {}
        leftovers: list[Task] = []
        for task_id in tracking:
            try:
                t = self._task_svc.get(task_id, session_id)
            except Exception:
                logger.exception("TM: failed to load tracking task %s", task_id)
                continue
            if t.status not in ("FINISHED", "FAILED", "CANCELED"):
                continue
            if t.parent_tool_call_id:
                by_call.setdefault(t.parent_tool_call_id, []).append(t)
            elif t.assigned_agent_id != parent.assigned_agent_id:
                leftovers.append(t)
            else:
                flushed.append(task_id)  # 自己执行的、无需回写

        for call_id, children in by_call.items():
            content = self._aggregate_children_result(children)
            self._memory_svc.append_message(
                agent_id=parent.assigned_agent_id,
                role="tool",
                content=content,
                session_id=session_id,
                task_id=parent.id,
                tool_call_id=call_id,
            )
            flushed.extend(c.id for c in children)

        for t in leftovers:
            outcome = "completed" if t.status == "FINISHED" else "failed"
            content = _task_result_content(
                f"Tracked task「{t.title}」{outcome}.", t.outputs, t.process_report, t.error,
            )
            self._memory_svc.append_message(
                agent_id=parent.assigned_agent_id, role="assistant", content=content,
                session_id=session_id, task_id=parent.id,
            )
            flushed.append(t.id)

        if not flushed:
            return
        new_tracking = [tid for tid in agent_data.get("tracking_tasks", []) if tid not in flushed]
        agent_data["tracking_tasks"] = new_tracking
        self._agent_store.save(agent_data)
        for task_id in flushed:
            try:
                t = self._task_svc.get(task_id, session_id)
                if parent.assigned_agent_id in t.trackers:
                    t.trackers.remove(parent.assigned_agent_id)
                    self._task_svc.save(t)
            except Exception:
                pass

    @staticmethod
    def _aggregate_children_result(children: list[Task]) -> "str | list":
        """把同一 submit_* 调用的多个子任务聚合成一条 tool_result 内容。"""
        parts: list[str] = []
        images: list = []
        for c in children:
            outcome = "completed" if c.status == "FINISHED" else "failed"
            section = [f"Task「{c.title}」{outcome}."]
            if isinstance(c.outputs, list):
                images.extend(p for p in c.outputs if p.get("type") == "image")
                out_text = next((p.get("text", "") for p in c.outputs if p.get("type") == "text"), "")
            else:
                out_text = c.outputs or ""
            if out_text:
                section.append(f"# Output\n\n{out_text}")
            if c.process_report:
                section.append(f"# Process Report\n\n{c.process_report}")
            if c.error:
                section.append(f"Error: {c.error}")
            parts.append("\n".join(section))
        text = "\n\n---\n\n".join(parts)
        if images:
            return [*images, {"type": "text", "text": text}]
        return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_flush_children_as_tool_result.py -v`
Expected: PASS

- [ ] **Step 5: Run existing task_manager regression suite**

Run: `python -m pytest tests/test_task_result_no_duplicate_memory.py -v`
Expected: PASS（自己执行的 inline 子任务仍不重复写；leftover/assistant 路径未破坏）

- [ ] **Step 6: Commit**

```bash
git add app/orchestrator/task_manager.py tests/test_flush_children_as_tool_result.py
git commit -m "feat(task_manager): deliver self-submitted children as submit_task tool_result"
```

---

## Task 8: tracker/sibling 走起始 user message 注入

非自己提交但在 tracking 列表的已终结 task，注入到 tracker 开始自己 task 的起始 user message 的 `## Tracking task updates` 段（不走 tool_result）。

**Files:**
- Modify: `app/runtime/prompt_builder.py`（`ActorPromptBuilder.build_initial_user_content`）、`app/runtime/agent_loop.py`（构造初始 user content 时传入 tracking 结果）
- Test: `tests/test_tracking_updates_in_user_message.py`

设计：
- agent_loop 在持久化 user_prompt 包装内容时（`run()` 里 `build_initial_user_content`），收集该 agent `tracking_tasks` 中**非自己提交**（`parent_tool_call_id` 为空）且已终结的 task 结果，作为 `tracking_updates` 传给 builder，渲染成 `## Tracking task updates` 段。
- 自己提交的（有 `parent_tool_call_id`）不在此注入——它们走 Task 7 的 tool_result。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tracking_updates_in_user_message.py
from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory


def _task():
    return Task(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="PENDING", user_prompt="do X", title="Do X", description="d",
                created_at="", updated_at="")


def test_tracking_updates_section_rendered():
    builder = PromptBuilderFactory.for_actor()
    updates = ["Tracked task「Sib」completed.\n# Output\n\nsib out"]
    content = builder.build_initial_user_content(_task(), tracking_updates=updates)
    text = content if isinstance(content, str) else str(content)
    assert "## Tracking task updates" in text
    assert "sib out" in text


def test_no_tracking_updates_omits_section():
    builder = PromptBuilderFactory.for_actor()
    content = builder.build_initial_user_content(_task())
    assert "## Tracking task updates" not in str(content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracking_updates_in_user_message.py -v`
Expected: FAIL（`build_initial_user_content` 不接受 `tracking_updates`）

- [ ] **Step 3a: prompt_builder 支持 tracking_updates**

把 `ActorPromptBuilder.build_initial_user_content` 改为接受可选 `tracking_updates`：

```python
    def build_initial_user_content(self, task: "Task", tracking_updates: list[str] | None = None) -> "str | list":
        """不依赖 ctx，仅用 task 信息构建初始 user message 内容（写入 memory 用）。"""
        parts: list[str] = []
        if task.title and task.description:
            parts.append(f"## Current Goal\n{task.title}\n{task.description}")
        user_prompt = task.user_prompt
        if user_prompt:
            parts.append(f"## Current Message\n{content_to_text(user_prompt)}")
        if tracking_updates:
            parts.append("## Tracking task updates\n" + "\n\n".join(tracking_updates))
        text_content = "\n\n".join(parts)
        if isinstance(user_prompt, list):
            image_parts = [p for p in content_from_raw(user_prompt) if isinstance(p, ImagePart)]
            return ([*image_parts, TextPart(text=text_content)] if image_parts else text_content)
        return text_content
```

- [ ] **Step 3b: agent_loop 收集 tracking 结果并传入**

在 `app/runtime/agent_loop.py` 的 `run()` 中，持久化 user_prompt 那段：

```python
            if task.user_prompt and not task.user_prompt_in_memory and not (task.settings or {}).get("_daemon"):
                wrapped = self._actor._prompt_builder.build_initial_user_content(task)
```

改为先收集 tracking 更新再传入：

```python
            if task.user_prompt and not task.user_prompt_in_memory and not (task.settings or {}).get("_daemon"):
                tracking_updates = self._collect_tracking_updates(session_id, agent_id)
                wrapped = self._actor._prompt_builder.build_initial_user_content(task, tracking_updates=tracking_updates)
```

新增方法：

```python
    def _collect_tracking_updates(self, session_id: str, agent_id: str) -> list[str]:
        """收集该 agent tracking_tasks 中非自己提交、已终结的 task 结果文本。"""
        from app.orchestrator.task_manager import _task_result_content
        data = self._agent_store.get(session_id, agent_id) or {}
        updates: list[str] = []
        for tid in list(data.get("tracking_tasks", [])):
            try:
                t = self._task_svc.get(tid, session_id)
            except Exception:
                continue
            if t.status not in ("FINISHED", "FAILED", "CANCELED"):
                continue
            if t.parent_tool_call_id:   # 自己提交的走 tool_result，不在此注入
                continue
            if t.assigned_agent_id == agent_id:   # 自己执行的已在自身 memory
                continue
            outcome = "completed" if t.status == "FINISHED" else "failed"
            updates.append(_task_result_content(
                f"Tracked task「{t.title}」{outcome}.", t.outputs, t.process_report, t.error,
            ) if isinstance(t.outputs, str) else f"Tracked task「{t.title}」{outcome}.")
        return updates
```

> 说明：`_task_result_content` 返回 str（无图）时直接用；含图的多模态合并暂不在起始 user message 处理（YAGNI，少见），仅放标题摘要。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracking_updates_in_user_message.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/runtime/prompt_builder.py app/runtime/agent_loop.py tests/test_tracking_updates_in_user_message.py
git commit -m "feat(tracking): inject tracker/sibling results into starting user message"
```

---

## Task 9: 全量回归 + 清理

**Files:**
- 无新增；跑全套测试，修复回归。

- [ ] **Step 1: Run full suite**

Run: `python -m pytest tests/ -v`
Expected: 全绿。重点关注：`test_hitl_pending_answer.py`、`test_task_result_no_duplicate_memory.py`、`test_lifecycle_manager.py`。

- [ ] **Step 2: Grep 残留引用**

确认无残留对已删除符号的引用：

```bash
grep -rn "_summarize_spawn\|_split_agent_summary" app/ tests/
```
Expected: 无输出（或仅注释）。

- [ ] **Step 3: 核心不变量手测**

确认 agent memory 中 assistant 消息不含 `# Process Report`：审阅 `_write_execution_memory`、`_write_suspension_memory` 写入路径，断言 role="assistant" 的 content 不拼接 process report。

- [ ] **Step 4: Commit（如有修复）**

```bash
git add -A
git commit -m "test: fix regressions after context organization redesign"
```

---

## Self-Review 记录

- **Spec 覆盖**：模型(Task1)、序列化(Task2)、写入 round+output-only(Task3)、observer 数据源(Task4)、actor 归并(Task5)、submit tool_call(Task6)、自交付 tool_result(Task7)、tracker 注入(Task8)、回归(Task9) — 与 spec 第 2–6 节一一对应。
- **去重规则**：Task7（`parent_tool_call_id` → tool_result）与 Task8（无 `parent_tool_call_id` 且非自执行 → user message）互斥，覆盖 spec「去重规则」。
- **类型一致**：`make_round_record` / `round_to_actor_messages` / round dict 结构（`turns`/`process_report`/`output`/`ts`）在 Task2/3/4/5 全程一致；`parent_tool_call_id` 在 Task1/6/7/8 一致。
- **已知简化（YAGNI）**：tracker 起始注入对多模态图片只放标题摘要（Task8 Step3b 说明）。
