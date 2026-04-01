# Agent Loop v2 开发方案

**对应设计文档**：[agent_loop_architecture.md](agent_loop_architecture.md)  
**日期**：2026-03-31  

---

## 前置说明

### 涉及文件清单

| 操作 | 文件 |
|------|------|
| 新建 | `app/runtime/types.py` |
| 新建 | `app/runtime/reasoner.py` |
| 新建 | `app/runtime/actor.py` |
| 新建 | `app/runtime/observer.py` |
| 重构 | `app/runtime/planner.py` |
| 重构 | `app/runtime/agent_loop.py` |
| 删除 | `app/runtime/task_executor.py` |
| 删除 | `app/runtime/skill_router.py` |
| 修改 | `app/domain/models/agent.py`（LoopGuard 新增字段）|
| 修改 | `app/domain/models/task.py`（注释更新 task.type）|
| 修改 | `app/skills/registry.py`（get_metadata_block 调整文本）|
| 修改 | `app/llm/types.py`（LLMMessage 支持 tool result）|
| 修改 | `app/tools/builtins.py`（新增 request_human_input）|
| 修改 | `app/api/v1/deps.py`（注入新组件）|
| 修改 | `frontend/src/types/index.ts`（TaskType 更新）|
| 修改 | `tests/test_phase_implementations.py` |

### 实现约束

- **不拆分 PR**：所有步骤完成后一次性提交（中间状态不可运行）
- **保留 ToolGateway / PolicyEngine / CompactionStrategy**：这三个组件无需修改
- **保留 WAITING_INPUT 状态机**：已实现，不动

---

## Step 1 — 定义数据结构（`app/runtime/types.py`）

新建文件，定义四个阶段的输入/输出类型。这是后续所有步骤的基础。

```python
"""Agent Loop v2 阶段间数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class SkillMeta:
    """Reasoner 检索出的技能元数据（轻量，不含完整 instructions）。"""
    name: str
    description: str


@dataclass
class ReasoningContext:
    """Reasoner 阶段输出，作为 Planner 和 Actor 的输入。"""
    # Memory 层
    goal: str
    recent_messages: list[dict]
    summary_text: str
    blackboard_snippets: list[str]
    # 检索结果
    relevant_tools: list[Any]           # list[LLMTool]，避免循环导入用 Any
    relevant_skills: list[SkillMeta]
    # Token 估算（给 guard 用）
    token_estimate: int = 0


@dataclass
class PlannedTask:
    """Planner 输出的单个任务。"""
    type: Literal["atomic", "user_input"]
    title: str
    description: str
    skill_name: str | None = None       # Planner 指定，None 表示不用 skill
    prompt: str = ""                    # user_input 专用：展示给用户的问题


@dataclass
class TaskPlan:
    """Planner 阶段完整输出。"""
    tasks: list[PlannedTask] = field(default_factory=list)


@dataclass
class ToolCallRecord:
    """Actor 内单次工具调用记录（用于 ActorResult 和审计）。"""
    tool_name: str
    arguments: dict[str, Any]
    result: str
    is_error: bool = False


@dataclass
class ActorResult:
    """Actor 对单个 atomic task 的执行结果。"""
    task_id: str
    success: bool
    output: str
    tool_calls_made: list[ToolCallRecord] = field(default_factory=list)
    hitl_task_id: str | None = None     # 非 None 表示已暂停等待用户输入
    actor_mode: Literal["text", "tool_use", "skill"] = "text"
    skill_used: str | None = None
    error: str | None = None


@dataclass
class ObserverVerdict:
    """Observer 阶段输出。"""
    done: bool
    summary: str        # 写入 Memory（role=assistant）
    reasoning: str      # 只进日志
```

**验证**：无依赖，可直接 `python -c "from app.runtime.types import ReasoningContext"` 验证。

---

## Step 2 — 扩展 LoopGuard（`app/domain/models/agent.py`）

在 `LoopGuard` 中新增 `actor_max_tool_rounds`：

```python
@dataclass
class LoopGuard:
    turns_used: int = 0
    max_turns: int = 20
    actor_max_tool_rounds: int = 5      # 新增：单个 atomic task 内最多工具调用轮次

    def to_dict(self):
        return {
            "turns_used": self.turns_used,
            "max_turns": self.max_turns,
            "actor_max_tool_rounds": self.actor_max_tool_rounds,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            turns_used=d.get("turns_used", 0),
            max_turns=d.get("max_turns", 20),
            actor_max_tool_rounds=d.get("actor_max_tool_rounds", 5),
        )
```

---

## Step 3 — LLMMessage 支持 tool result（`app/llm/types.py`）

Actor 多轮 tool use 需要把工具结果追加为 `role=tool` 的消息。当前 `LLMMessage.to_af()` 只传字符串 content，AF 的 `Message` 接口需要确认是否支持 function_result 类型的 content block。

**两种方案，在实现 Actor 之前确认选一个：**

**方案 A（推荐）**：给 `LLMMessage` 增加 `tool_use_id` 字段，`to_af()` 中根据 role 分支构建不同 content block：

```python
@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool", "developer"]
    content: str
    tool_use_id: str | None = None      # 新增，role=tool 时必填

    def to_af(self):
        from agent_framework import Message
        if self.role == "tool" and self.tool_use_id:
            # AF function_result content block
            from agent_framework.content import FunctionResultContent
            return Message(
                role="tool",
                contents=[FunctionResultContent(
                    call_id=self.tool_use_id,
                    result=self.content,
                )],
            )
        return Message(role=self.role, contents=[self.content])
```

**方案 B（临时）**：将整个 assistant turn（含 tool call blocks）和 tool result 都以 `role=user` 拼成文本追加，不依赖 AF 的 multi-turn 结构。精度低但不需要改 types.py。

> **动作**：在开始 Step 4 之前，先用 mock client 写一个 Actor 的最小用例，确认 AF 是否支持方案 A 的 FunctionResultContent；若不支持则用方案 B。

---

## Step 4 — 实现 Reasoner（`app/runtime/reasoner.py`）

从现有的 `AgentLoop._observe()` 和 `Planner.plan()` 中的工具/技能检索逻辑迁移过来。

```python
"""Reasoner：上下文构建 + 工具/技能检索（无 LLM 调用）。"""

from app.runtime.types import ReasoningContext, SkillMeta
# ... 导入 MemoryService, BlackboardService, ToolRegistry, SkillRegistry

class Reasoner:
    def __init__(self, memory_svc, blackboard_svc, tool_registry, skill_registry): ...

    def reason(self, session, agent) -> ReasoningContext:
        # 1. 从 MemoryService 取 recent_messages 和 summary_text
        #    现有：AgentLoop._observe() → memory_svc.build_prompt_context()
        
        # 2. 从 BlackboardService 取 blackboard_snippets
        #    现有：AgentLoop._observe() → bb_svc.pull("_root", agent.id)
        
        # 3. 检索 relevant_tools
        #    现有：Planner.plan() 中的 tool_descriptions 构建逻辑
        #    - 有 ToolStoreClient → store_client.search(goal, top_k=10)
        #    - 无 → tool_registry.to_llm_tools(agent.tool_list)
        
        # 4. 检索 relevant_skills
        #    现有：Planner.plan() 中的 skill_metadata_block 构建逻辑
        #    - 有 SkillStoreClient → store_client.search(goal, top_k=5)
        #    - 无 → [SkillMeta(m.name, m.description) for m in registry.list_all()]
        
        return ReasoningContext(...)
```

**关键**：Reasoner 不调用 LLM，没有失败场景，不需要错误处理。

---

## Step 5 — 重构 Planner（`app/runtime/planner.py`）

### 5.1 更新 submit_plan schema

删除 `done`、`summary` 参数，新增 `skill_name`：

```python
@tool_result
def submit_plan(
    tasks: Annotated[
        list,
        (
            "Ordered list of tasks. Each task: "
            "{ type: 'atomic' | 'user_input', title: str, description: str, "
            "  skill_name: str | null, prompt: str }. "
            "— 'description' describes WHAT to achieve, not HOW (no tool names or arguments). "
            "— 'skill_name' must be one of the available skills, or null. "
            "— 'prompt' required only for user_input tasks. "
            "Empty list means nothing to do this turn."
        ),
    ],
) -> ToolResult:
    """Submit a decomposed task list. Do NOT specify tool names or arguments."""
    return ToolResult(content="")
```

### 5.2 更新 Planner.plan() 签名

输入从 `PromptContext` 改为 `ReasoningContext`，输出从 `tuple[dict, LLMUsage]` 改为 `TaskPlan`：

```python
def plan(self, ctx: ReasoningContext, agent: Agent) -> TaskPlan:
    system_prompt = self._build_system_prompt(ctx, agent)
    messages = self._build_messages(ctx)
    
    response = self._llm_client.send_message(
        messages=messages,
        system_prompt=system_prompt,
        tools=[submit_plan.to_llm_tool()],
    )
    
    parsed = self._llm_client.parse_response(response)
    for tool_call in parsed.tool_calls:
        if tool_call.name == "submit_plan":
            return self._parse_task_plan(tool_call.input)
    
    # fallback
    return TaskPlan(tasks=[])
```

### 5.3 更新 _build_system_prompt

从 `ctx.relevant_tools` 和 `ctx.relevant_skills` 构建 capability 列表：

```python
def _build_system_prompt(self, ctx: ReasoningContext, agent: Agent) -> str:
    parts = [agent.system_prompt or _FALLBACK_SYSTEM_PROMPT]
    
    if ctx.relevant_tools:
        lines = ["## Available Tools (for awareness only — Actor decides how to use them)"]
        for tool in ctx.relevant_tools:
            lines.append(f"- {tool.name}: {tool.description or ''}")
        parts.append("\n".join(lines))
    
    if ctx.relevant_skills:
        lines = ["## Available Skills (assign to tasks where appropriate)"]
        for skill in ctx.relevant_skills:
            lines.append(f"- {skill.name}: {skill.description}")
        parts.append("\n".join(lines))
    
    parts.append(
        "Your job: decompose the goal into an ordered task list.\n"
        "- 'description' describes WHAT to achieve, not HOW.\n"
        "- 'skill_name' should be set if a skill fits the task; otherwise null.\n"
        "- Do NOT specify tool names, arguments, or message content."
    )
    return "\n\n---\n\n".join(parts)
```

### 5.4 _parse_task_plan

```python
def _parse_task_plan(self, raw: dict) -> TaskPlan:
    tasks = []
    for spec in raw.get("tasks", []):
        tasks.append(PlannedTask(
            type=spec.get("type", "atomic"),
            title=spec.get("title", ""),
            description=spec.get("description", ""),
            skill_name=spec.get("skill_name") or None,
            prompt=spec.get("prompt", ""),
        ))
    return TaskPlan(tasks=tasks)
```

### 5.5 删除 resolve_planner_config / _ensure_planner

Planner 不再挂在 Agent 上，由 AgentLoop 构造时注入。`agent.planner` 字段同步删除（Agent.to_dict / from_dict 已不序列化它，只需删除 TYPE_CHECKING import）。

**注意**：`Planner.plan()` 内部不再追加 `tokens_used`，这个职责移到 AgentLoop 主流程。

---

## Step 6 — 实现 Actor（`app/runtime/actor.py`）

Actor 是改动最大的新组件，也是整个架构的核心。

```python
"""Actor：执行单个 atomic task，支持多轮 tool use。"""

from app.runtime.types import ActorResult, ReasoningContext, ToolCallRecord
# ... 其他导入

class Actor:
    def __init__(
        self,
        llm_client: BaseChatClient,
        tool_gateway: ToolGateway,
        skill_registry: SkillRegistry,
        task_svc: TaskService,
        session_svc: SessionService,
    ): ...

    def act(self, task: Task, ctx: ReasoningContext, agent: Agent) -> ActorResult:
        # 1. 载入 skill（如果 task.inputs["skill_name"] 有值）
        system_prompt = self._build_system_prompt(task, agent)
        
        # 2. 构建初始 messages
        messages = self._build_messages(task, ctx)
        
        # 3. 构建 available_tools：ctx.relevant_tools + [request_human_input]
        tools = list(ctx.relevant_tools) + [request_human_input.to_llm_tool()]
        
        # 4. 激活 task
        self._task_svc.transition(task.id, "ACTIVE")
        
        # 5. 多轮 tool use 循环
        tool_calls_made: list[ToolCallRecord] = []
        max_rounds = agent.loop_guard.actor_max_tool_rounds
        
        for _round in range(max_rounds):
            response = self._llm_client.send_message(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
            )
            parsed = self._llm_client.parse_response(response)
            
            if not parsed.tool_calls:
                # 无工具调用，取文本输出，结束循环
                return self._finish_task(task, parsed.text, tool_calls_made)
            
            # 处理每个 tool call
            for tool_call in parsed.tool_calls:
                if tool_call.name == "request_human_input":
                    return self._handle_hitl(task, tool_call.input, agent)
                
                result = self._tool_gateway.call(
                    tool_name=tool_call.name,
                    arguments=tool_call.input,
                    agent=agent,
                    task_id=task.id,
                )
                tool_calls_made.append(ToolCallRecord(
                    tool_name=tool_call.name,
                    arguments=tool_call.input,
                    result=result.content,
                    is_error=result.is_error,
                ))
                
                # 追加 assistant 消息（含 tool call）和 tool result 消息
                messages = self._append_tool_turn(messages, response, tool_call, result)
        
        # 超出最大轮次，取最后的文本输出
        return self._finish_task(task, response.text, tool_calls_made)
```

### 6.1 _build_system_prompt

```python
def _build_system_prompt(self, task: Task, agent: Agent) -> str:
    skill_name = task.inputs.get("skill_name")
    if not skill_name:
        return agent.system_prompt
    
    skill_def = self._skill_registry.load_definition(skill_name)
    if skill_def is None:
        logger.warning("Actor: skill '%s' not found, proceeding without it", skill_name)
        return agent.system_prompt
    
    return agent.system_prompt + "\n\n---\n\n" + skill_def.instructions
```

### 6.2 _build_messages

```python
def _build_messages(self, task: Task, ctx: ReasoningContext) -> list[LLMMessage]:
    messages = []
    
    # 目标 + 历史摘要
    goal_content = ctx.goal
    if ctx.summary_text:
        goal_content = f"Previous progress:\n{ctx.summary_text}\n\nCurrent goal: {ctx.goal}"
    messages.append(LLMMessage(role="user", content=goal_content))
    
    # Blackboard 上下文
    if ctx.blackboard_snippets:
        bb = "\n".join(f"- {s}" for s in ctx.blackboard_snippets)
        messages.append(LLMMessage(role="user", content=f"Context:\n{bb}"))
    
    # 近期消息历史
    for m in ctx.recent_messages:
        messages.append(LLMMessage(role=m["role"], content=m["content"]))
    
    # 当前任务描述
    messages.append(LLMMessage(role="user", content=task.description or task.title))
    
    return messages
```

### 6.3 _append_tool_turn

这是 Step 3 中需要确认的关键点。AF 支持方案 A 时：

```python
def _append_tool_turn(self, messages, response, tool_call, tool_result) -> list[LLMMessage]:
    # 追加 assistant 消息（含 tool_call block，保留原始 AF 结构）
    # 追加 tool result 消息
    messages.append(LLMMessage(
        role="tool",
        content=tool_result.content or "",
        tool_use_id=tool_call.id,
    ))
    return messages
```

AF 不支持时，退化为在 user 消息中拼接 tool 结果文本。

### 6.4 _handle_hitl

```python
def _handle_hitl(self, task: Task, inputs: dict, agent: Agent) -> ActorResult:
    prompt = inputs.get("prompt", "")
    hitl_task = self._task_svc.create(
        session_id=task.session_id,
        agent_id=task.agent_id,
        task_type="user_input",
        title="等待用户输入",
        description=prompt,
        inputs={"prompt": prompt},
    )
    self._task_svc.transition(hitl_task.id, "ACTIVE")
    self._session_svc.transition(task.session_id, "WAITING_INPUT")
    
    return ActorResult(
        task_id=task.id,
        success=True,
        output="",
        hitl_task_id=hitl_task.id,
    )
```

### 6.5 _finish_task

```python
def _finish_task(self, task: Task, output: str, tool_calls_made: list) -> ActorResult:
    actor_mode = "tool_use" if tool_calls_made else "text"
    skill_used = task.inputs.get("skill_name")
    if skill_used:
        actor_mode = "skill"
    
    outputs = {
        "actor_mode": actor_mode,
        "skill_used": skill_used,
        "tool_calls_made": [tc.tool_name for tc in tool_calls_made],
        "text": output,
    }
    self._task_svc.finish(task.id, result=output, outputs=outputs)
    
    return ActorResult(
        task_id=task.id,
        success=True,
        output=output,
        tool_calls_made=tool_calls_made,
        actor_mode=actor_mode,
        skill_used=skill_used,
    )
```

### 6.6 request_human_input 内置工具

在 `app/tools/builtins.py` 中新增（不注册到 ToolRegistry，只用于 Actor 的 LLM schema）：

```python
@tool_result
def request_human_input(
    prompt: Annotated[str, "The question or instruction to show the user"],
    context: Annotated[str, "Optional background context for the user"] = "",
) -> ToolResult:
    """Pause execution and request input from the human user.
    Use when you need information or a decision that only the user can provide."""
    return ToolResult(content="")   # 触发信号，Actor 特殊处理，函数体不执行
```

---

## Step 7 — 实现 Observer（`app/runtime/observer.py`）

```python
"""Observer：独立评估本轮执行结果，判断目标是否达成。"""

from app.runtime.types import ActorResult, ObserverVerdict, ReasoningContext
# ...

_OBSERVER_SYSTEM_PROMPT = """You are an objective observer evaluating whether a goal has been achieved.
You will be given the original goal, a summary of past progress, and this turn's execution results.
Call submit_observation exactly once with your assessment."""

@tool_result
def submit_observation(
    done: Annotated[bool, "True if the original session goal is fully and completely achieved"],
    summary: Annotated[str, "Concise summary of what was accomplished this turn (1-3 sentences)"],
    reasoning: Annotated[str, "Internal reasoning: why done=True/False, what is still missing"],
) -> ToolResult:
    """Submit your observation of this turn's execution results."""
    return ToolResult(content="")


class Observer:
    def __init__(self, llm_client: BaseChatClient): ...

    def observe(
        self,
        session: Session,
        results: list[ActorResult],
        ctx: ReasoningContext,
    ) -> ObserverVerdict:
        try:
            return self._llm_observe(session, results, ctx)
        except Exception as e:
            logger.warning("Observer LLM call failed, falling back to rules: %s", e)
            return self._rule_observe(results, session)
    
    def _llm_observe(self, session, results, ctx) -> ObserverVerdict:
        successes = [r.output for r in results if r.success and r.output]
        errors = [r.error for r in results if not r.success and r.error]
        
        results_text = "\n".join([
            f"- Task result: {o}" for o in successes
        ] + [
            f"- Task error: {e}" for e in errors
        ])
        
        user_content = (
            f"Original goal: {session.goal}\n\n"
            f"Previous summary: {ctx.summary_text or 'None'}\n\n"
            f"This turn's results:\n{results_text or 'No results'}\n\n"
            "Is the original goal fully achieved?"
        )
        
        response = self._llm_client.send_message(
            messages=[LLMMessage(role="user", content=user_content)],
            system_prompt=_OBSERVER_SYSTEM_PROMPT,
            tools=[submit_observation.to_llm_tool()],
        )
        parsed = self._llm_client.parse_response(response)
        
        for tool_call in parsed.tool_calls:
            if tool_call.name == "submit_observation":
                inp = tool_call.input
                return ObserverVerdict(
                    done=bool(inp.get("done", False)),
                    summary=inp.get("summary", ""),
                    reasoning=inp.get("reasoning", ""),
                )
        
        raise RuntimeError("Observer: submit_observation not called")
    
    def _rule_observe(self, results: list[ActorResult], session: Session) -> ObserverVerdict:
        """降级规则：有结果但无法判断，返回 done=False 继续。"""
        all_success = all(r.success for r in results) if results else False
        token_pct = session.token_used / session.token_budget if session.token_budget else 0
        
        # Token 快耗尽时强制结束
        if token_pct > 0.9:
            return ObserverVerdict(done=True, summary="Token budget nearly exhausted.", reasoning="rule-based")
        
        summary = "Completed this turn's tasks." if all_success else "Some tasks failed this turn."
        return ObserverVerdict(done=False, summary=summary, reasoning="rule-based fallback")
```

---

## Step 8 — 重构 AgentLoop（`app/runtime/agent_loop.py`）

### 8.1 构造函数变化

去掉 `task_executor`、`skill_router`，新增 `reasoner`、`actor`、`observer`：

```python
class AgentLoop:
    def __init__(
        self,
        session_svc, task_svc, memory_svc, blackboard_svc,
        agent_store,
        llm_client,
        reasoner: Reasoner,
        planner: Planner,
        actor: Actor,
        observer: Observer,
        compaction_strategy=None,
    ): ...
```

### 8.2 主流程

```python
def run(self, session_id: str, agent_id: str) -> None:
    agent = self._load_agent(agent_id)
    agent.status = "RUNNING"
    self._agent_store.save(agent.to_dict())
    
    try:
        while True:
            session = self._session_svc.get(session_id)
            self._check_guard(session, agent)
            
            # Phase 1: Reason
            ctx = self._reasoner.reason(session, agent)
            
            # Phase 2: Plan
            plan = self._planner.plan(ctx, agent)
            usage = None  # Planner 需要返回 usage 供 token 计数
            if usage and usage.total_tokens:
                self._session_svc.add_tokens(session_id, usage.total_tokens)
            agent.loop_guard.turns_used += 1
            self._agent_store.save(agent.to_dict())
            
            # Phase 3: Act
            results, hitl_triggered = self._act_all(plan, ctx, agent, session_id)
            if hitl_triggered:
                return
            
            # Phase 4: Observe
            verdict = self._observer.observe(session, results, ctx)
            logger.debug("Observer reasoning: %s", verdict.reasoning)
            
            self._memory_svc.append_message(
                session_id=session_id,
                agent_id=agent_id,
                role="assistant",
                content=verdict.summary,
            )
            self._bb_svc.publish(session_id, "_root", agent_id, verdict.summary)
            
            if self._memory_svc.should_summarize(session_id):
                self._do_summarize(session_id, agent_id)
            
            if verdict.done:
                self._session_svc.transition(session_id, "SUCCEEDED")
                break
    
    except AppError as e:
        logger.error("AgentLoop terminated: session=%s code=%s", session_id, e.code)
        try:
            self._session_svc.transition(session_id, "FAILED")
        except Exception:
            pass
        raise
    
    finally:
        agent = self._load_agent(agent_id)
        if agent.status == "RUNNING":
            agent.status = "FINISHED"
            self._agent_store.save(agent.to_dict())
```

### 8.3 _act_all

```python
def _act_all(
    self, plan: TaskPlan, ctx: ReasoningContext, agent: Agent, session_id: str
) -> tuple[list[ActorResult], bool]:
    results: list[ActorResult] = []
    
    for planned in plan.tasks:
        if planned.type == "user_input":
            # Planner 直接输出 user_input，不经过 Actor
            self._pause_for_user_input(planned, session_id, agent.id)
            return results, True
        
        # 创建 DB task，skill_name 存入 inputs
        db_task = self._task_svc.create(
            session_id=session_id,
            agent_id=agent.id,
            task_type="atomic",
            title=planned.title,
            description=planned.description,
            inputs={"skill_name": planned.skill_name} if planned.skill_name else {},
        )
        
        result = self._actor.act(db_task, ctx, agent)
        results.append(result)
        
        if result.hitl_task_id:
            return results, True
        
        if not result.success:
            logger.warning("Task %s failed: %s", db_task.id, result.error)
            # 失败后继续执行剩余 task（Observer 统一判断）
    
    return results, False
```

### 8.4 _pause_for_user_input

```python
def _pause_for_user_input(self, planned: PlannedTask, session_id: str, agent_id: str) -> None:
    task = self._task_svc.create(
        session_id=session_id,
        agent_id=agent_id,
        task_type="user_input",
        title=planned.title or "等待用户输入",
        description=planned.description,
        inputs={"prompt": planned.prompt},
    )
    self._task_svc.transition(task.id, "ACTIVE")
    self._session_svc.transition(session_id, "WAITING_INPUT")
```

### 8.5 删除内容

- 删除 `_WaitingForInputSignal` 类（Actor 和 _act_all 直接通过返回值处理 HITL）
- 删除 `_observe()`、`_plan()`、`_create_tasks()`、`_execute()`、`_update_memory()`
- 删除 `_ensure_planner()`（Planner 构造时注入，无需按需初始化）
- 保留 `_check_guard()`、`_load_agent()`、`_do_summarize()`

---

## Step 9 — 更新依赖注入（`app/api/v1/deps.py`）

构造新组件链并注入 AgentLoop：

```python
def get_agent_loop() -> AgentLoop:
    reasoner = Reasoner(
        memory_svc=get_memory_service(),
        blackboard_svc=get_blackboard_service(),
        tool_registry=get_tool_registry(),
        skill_registry=get_skill_registry(),
    )
    planner = Planner(llm_client=get_llm_client())
    actor = Actor(
        llm_client=get_llm_client(),
        tool_gateway=get_tool_gateway(),
        skill_registry=get_skill_registry(),
        task_svc=get_task_service(),
        session_svc=get_session_service(),
    )
    observer = Observer(llm_client=get_llm_client())
    
    return AgentLoop(
        session_svc=get_session_service(),
        task_svc=get_task_service(),
        memory_svc=get_memory_service(),
        blackboard_svc=get_blackboard_service(),
        agent_store=AgentStore(),
        llm_client=get_llm_client(),
        reasoner=reasoner,
        planner=planner,
        actor=actor,
        observer=observer,
        compaction_strategy=get_compaction_strategy(),
    )
```

---

## Step 10 — 清理旧组件

### 10.1 删除文件

- `app/runtime/task_executor.py`（被 Actor 取代）
- `app/runtime/skill_router.py`（SkillRegistry.load_definition 直接调用）

### 10.2 更新 SkillRegistry.get_metadata_block()

删除旧文本中的 `"To use a skill, create a task with type=skill"`，改为：

```
## Available Skills (assign to tasks where appropriate)
- code_review: ...
- api_test: ...
```

（或直接废弃这个方法，Reasoner 直接调用 `list_all()` 构建 SkillMeta 列表。）

### 10.3 Task 模型注释更新

`app/domain/models/task.py` 中 `type` 字段注释改为 `# atomic | user_input`。

---

## Step 11 — 更新前端类型（`frontend/src/types/index.ts`）

```typescript
// 修改 TaskType
export type TaskType = 'atomic' | 'user_input'
// 删除：'reasoning' | 'tool-call' | 'skill'
```

同步更新 `TaskTimeline` 组件中对 task.type 的 display 逻辑。

---

## Step 12 — 更新测试

### 需要重写的测试

**`tests/test_phase_implementations.py`**（已存在但针对旧架构）：
- `TestReasoner`：mock MemoryService + ToolRegistry，验证 ReasoningContext 字段
- `TestPlanner`：mock LLMClient 返回 submit_plan tool call，验证 TaskPlan 解析
- `TestActor`：
  - 纯文本输出路径（无 tool call）
  - 单轮 tool use 路径
  - request_human_input 触发路径
  - skill 注入路径（skill_name != None）
- `TestObserver`：
  - LLM 正常调用路径
  - LLM 失败降级为规则判断路径

**`tests/test_builtins.py`**：新增 `request_human_input` schema 验证。

---

## 实现顺序总结

```
Step 1  → types.py（无依赖，先建基础）
Step 2  → agent.py LoopGuard
Step 3  → llm/types.py LLMMessage（确认 AF tool result 方案）
Step 4  → reasoner.py
Step 5  → planner.py 重构
Step 6  → actor.py + builtins.py request_human_input
Step 7  → observer.py
Step 8  → agent_loop.py 重构
Step 9  → deps.py 重新组装
Step 10 → 删除旧文件，清理
Step 11 → 前端类型
Step 12 → 测试
```

每一步完成后，对应的单元测试应当能独立运行通过，最终 Step 8 完成后做一次完整的集成冒烟测试（创建 session → 运行 loop → 验证 SUCCEEDED）。

---

## 风险点

| 风险 | 影响 | 应对 |
|------|------|------|
| AF 不支持 tool result 消息格式（Step 3） | Actor 多轮 tool use 无法实现 | 先用文本拼接方案 B，后续升级 |
| Observer 额外 LLM 调用增加延迟 | 每轮多 1 次 LLM RTT | 规则降级兜底；长期可做 Observer 缓存 |
| `task.type=atomic` 破坏历史 task 数据 | 旧 task 记录 type 仍为 reasoning/tool-call | 读取时做兼容：未知 type 当作 atomic 显示 |
| Planner 不调用 submit_plan 的 fallback | 空 TaskPlan 触发 Observer 立即返回 done=False | 循环继续，turns_used 递增至 max_turns 保护 |
