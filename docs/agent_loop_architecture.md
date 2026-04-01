# Agent Loop 架构重构设计

**版本**：v2.0 草案  
**日期**：2026-03-31  
**状态**：设计阶段，待实现

---

## 1. 重构动机

### 现有架构的问题

| 问题 | 具体表现 |
|------|---------|
| **Planner 职责过重** | 既要选工具/技能，又要拆解任务，还要在 task.inputs 中预填执行参数（tool_name、arguments、messages）|
| **执行决策前置** | Planner 在规划阶段就决定了 HOW（用哪个工具、传什么参数），导致规划和执行高度耦合 |
| **submit_plan 函数体是死代码** | `Planner.plan()` 直接读 `tool_call.input`，函数返回值从未被使用 |
| **task.type 语义混乱** | `reasoning`、`tool-call`、`skill` 是执行方式而非任务语义，Planner 越权做了 Actor 的判断 |
| **完成判断由 Planner 自评** | `done` 标志由同一个规划 LLM 调用给出，没有独立的完成验证 |
| **memory 更新是简单追加** | `_update_memory` 只做机械写入，没有对本轮执行情况进行任何评估 |

---

## 2. 新架构概览

每个 Loop 轮次由四个串行阶段组成：

```
┌─────────────────────────────────────────────────────────┐
│                    AgentLoop Turn N                      │
│                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────┐ │
│  │ Reasoner │ → │ Planner  │ → │  Actor   │ → │ Obs- │ │
│  │          │   │          │   │ (×M tasks)│   │ erver│ │
│  └──────────┘   └──────────┘   └──────────┘   └──────┘ │
│       ↓               ↓               ↓            ↓    │
│  ReasonCtx       TaskPlan        ActorResults   Verdict  │
└─────────────────────────────────────────────────────────┘
```

### 各阶段职责边界

| 阶段 | 职责 | 不做什么 |
|------|------|---------|
| **Reasoner** | 从 Memory/Blackboard 构建当前上下文；从 ToolRegistry/SkillStore 检索最相关的工具和技能 | 不拆分任务，不决策执行方式 |
| **Planner** | 将目标拆解为有序的原子任务列表，每个任务只有"做什么"（title + description）| 不决定用哪个工具，不填写执行参数 |
| **Actor** | 接收单个原子任务，自主决策执行方式（LLM tool use）；调用 ToolGateway；处理 HITL 暂停 | 不做跨任务规划，不判断目标是否完成 |
| **Observer** | 综合本轮 Actor 执行结果，判断原始目标是否已达成；向 Memory 写入本轮总结 | 不执行任何工具，不修改规划 |

---

## 3. 数据结构

### 3.1 ReasoningContext（Reasoner 输出）

```python
@dataclass
class ReasoningContext:
    # Memory 层
    goal: str                              # session.goal，不变
    recent_messages: list[dict]            # 从 MemoryService 取的近期消息
    summary_text: str                      # 压缩摘要（如有）
    blackboard_snippets: list[str]         # 本轮 Blackboard 条目

    # 检索结果
    relevant_tools: list[LLMTool]          # top-k 工具 schema（含 description）
    relevant_skills: list[SkillMeta]       # top-k 技能元数据（仅 name + description，不含完整 instructions）

    # token 估算
    token_estimate: int

@dataclass
class SkillMeta:
    name: str
    description: str          # 一句话描述，用于匹配和展示给 Planner
    # 完整 instructions 不在此处，由 Actor 按需通过 SkillRegistry.load_full() 加载
```

**关键设计**：
- `relevant_tools` 和 `relevant_skills` 由 Reasoner 根据 `goal` + `recent_messages` 语义检索得出，而不是把 agent.tool_list 全量丢给 Planner
- `SkillMeta` 只含轻量元数据，完整的 skill instructions 延迟到 Actor 确认匹配后再加载，避免无用文本占用 token

### 3.2 PlannedTask & TaskPlan（Planner 输出）

```python
@dataclass
class PlannedTask:
    type: Literal["atomic", "user_input"]
    title: str
    description: str        # 描述 WHAT，不包含具体工具参数
    skill_name: str | None  # Planner 指定要使用的 skill（None 表示不需要 skill）
    # user_input 专用
    prompt: str = ""        # 展示给用户的问题文本

@dataclass
class TaskPlan:
    tasks: list[PlannedTask]
    # 注意：无 done 字段——完成判断由 Observer 负责
```

**关键设计**：
- Planner 在规划阶段决定每个任务**是否需要 skill 以及用哪个**，这是任务分解的一部分（"用代码审查技能审查这段代码"是 WHAT，不是 HOW）
- Planner 不指定具体工具名称、参数、消息——这些仍由 Actor 自主决定
- Actor 负责按 `task.skill_name` 加载完整 instructions 并注入执行上下文

### 3.3 ActorResult（Actor 输出）

```python
@dataclass
class ActorResult:
    task_id: str
    success: bool
    output: str                          # 最终输出文本（工具结果 / LLM 回复）
    tool_calls_made: list[ToolCallRecord] # 本次执行中实际调用的工具记录
    hitl_task_id: str | None             # 非 None 表示已暂停等待用户确认
    error: str | None
```

### 3.4 ObserverVerdict（Observer 输出）

```python
@dataclass
class ObserverVerdict:
    done: bool           # 原始目标是否已完全达成
    summary: str         # 写入 Memory 的本轮执行总结（角色=assistant）
    reasoning: str       # 内部推理过程，只写日志，不进 Memory
```

---

## 4. 各阶段详细设计

### 4.1 Reasoner

**输入**：Session、Agent、MemoryService、ToolRegistry、SkillRegistry  
**输出**：ReasoningContext  
**是否调用 LLM**：否（纯检索 + 拼装）

```
Reasoner.reason(session, agent) → ReasoningContext
  ├─ MemoryService.build_prompt_context()     → recent_messages, summary_text
  ├─ BlackboardService.pull("_root", agent)   → blackboard_snippets
  ├─ ToolRegistry.search(goal, top_k=10)
  │    ├─ ToolStoreClient.enabled → 语义检索
  │    └─ 否则 → 取 agent.tool_list 对应的 tool schema
  └─ SkillRegistry.search(goal, top_k=5)
       ├─ SkillStoreClient.enabled → 语义检索
       └─ 否则 → 取 agent.skill_list 的元数据
```

**与现有代码的对应**：现在分散在 `Planner._build_messages()`、`Planner.plan()` 中拼 `tool_descriptions` 和 `skill_metadata_block` 的逻辑，全部移到这里。

---

### 4.2 Planner

**输入**：ReasoningContext、Agent  
**输出**：TaskPlan  
**是否调用 LLM**：是（1 次，强制 tool use）

#### submit_plan 工具新 Schema

```python
@tool_result
def submit_plan(
    tasks: Annotated[
        list,
        (
            "Ordered list of tasks. Each task: "
            "{ type: 'atomic' | 'user_input', title: str, description: str, "
            "  skill_name: str | null, prompt: str }  "
            "— 'description' describes WHAT to achieve, not HOW (no tool names or arguments). "
            "— 'skill_name' is the name of a skill from the available skills list, or null. "
            "— 'prompt' required only for user_input tasks. "
            "Empty list means nothing more to do (Observer will confirm completion)."
        ),
    ],
) -> ToolResult:
    """Submit a decomposed task list for this turn. Do NOT specify tool names or arguments."""
    return ToolResult(content="")   # body never used
```

**关键变化**：
- 去掉 `done` 字段——完成判断不再由 Planner 负责
- 去掉 `summary` 字段——总结由 Observer 生成
- 新增 `skill_name`——Planner 决定每个任务是否需要 skill；`null` 表示不需要
- 不包含 `tool_name`、`arguments`、`messages` 等执行细节，这些由 Actor 自主决定

#### Planner System Prompt 结构

```
{agent.system_prompt}

---

## Available Tools (for awareness only — Actor decides how to use them)
{relevant_tools 的 name + description 列表}

## Available Skills (assign to tasks where appropriate)
{relevant_skills 的 name + description 列表}

---

Your job: decompose the goal into an ordered list of atomic tasks.
- 'description' describes WHAT to achieve, not HOW.
- 'skill_name' should be set if a skill fits the task; otherwise null.
- Do NOT specify tool names, arguments, or message content.
```

---

### 4.3 Actor

**输入**：PlannedTask、ReasoningContext、Agent  
**输出**：ActorResult  
**是否调用 LLM**：是（每个 atomic task 1 次，支持多轮 tool use）

Actor 是唯一真正执行工具调用的组件。它给 LLM 提供完整的 tool schema，让 LLM 自主决定：
- 直接输出文本（纯推理）
- 调用一个或多个工具
- 触发 HITL 暂停（通过 `request_human_input` 内置工具）

#### Actor 执行流程

```
Actor.act(task, ctx, agent) → ActorResult
  │
  ├─ 载入 Skill（如 task.skill_name 不为 None）：
  │    skill_def = SkillRegistry.load_full(task.skill_name)
  │    system_prompt = agent.system_prompt + "\n\n---\n\n" + skill_def.instructions
  │
  ├─ 构建 messages:
  │    [system: system_prompt]
  │    [user: ctx.goal + ctx.summary_text]
  │    [*: ctx.recent_messages]
  │    [user: task.description]
  │
  ├─ 构建 available_tools:
  │    ctx.relevant_tools (来自 Reasoner，已过滤)
  │    + [request_human_input] (内置 HITL 工具，始终可用)
  │
  ├─ LLMClient.send_message(messages, tools=available_tools)
  │
  └─ 解析响应，循环处理 tool calls:
       ├─ tool_call.name == "request_human_input"
       │    → 创建 user_input task → ACTIVE
       │    → session → WAITING_INPUT
       │    → return ActorResult(hitl_task_id=task.id)
       │
       ├─ 其他 tool_call
       │    → ToolGateway.call(tool_name, arguments, agent, task_id)
       │    → 将结果追加到 messages（tool result role）
       │    → 继续循环（多轮 tool use）
       │
       └─ 无更多 tool calls → 取最后一条 assistant 消息作为 output
```

#### 内置工具 request_human_input

```python
@tool_result
def request_human_input(
    prompt: Annotated[str, "The question or instruction to show the user"],
    context: Annotated[str, "Optional background context for the user"] = "",
) -> ToolResult:
    """Pause execution and request input from the human user.
    Use when you need information or a decision that only the user can provide."""
    return ToolResult(content="")   # 触发 HITL，不实际执行
```

**关键设计**：
- Actor 支持**多轮 tool use**（agentic loop within a task）——一次 act() 调用内可以连续调用多个工具
- 多轮上限由 `actor_max_tool_rounds`（新增 Guard 参数）控制，防止单任务无限调用

#### Skill 执行方式变化

现有：Planner 指定 `type=skill`，TaskExecutor 用 skill_instructions **替换** system prompt。

新架构：skill 载入的职责拆分如下：

| 阶段 | Skill 相关操作 |
|------|--------------|
| **Reasoner** | 从 SkillRegistry 检索 top-k 技能，放入 `ctx.relevant_skills`（name + description） |
| **Planner** | 读取 `ctx.relevant_skills`，在任务规划时为每个 task 决策 `skill_name`（或 null） |
| **Actor** | 按 `task.skill_name` 加载完整 instructions，注入 system prompt 后执行 |

##### Actor Skill 载入流程

```
Actor.act(task, ctx, agent) 开始时：
  │
  ├─ 若 task.skill_name 不为 None：
  │    skill_def = SkillRegistry.load_full(task.skill_name)
  │    system_prompt = agent.system_prompt + "\n\n---\n\n" + skill_def.instructions
  │
  └─ 否则：
       system_prompt = agent.system_prompt
```

Skill instructions **追加**在 agent.system_prompt 之后，而不是替换它，确保 agent 基础人设保留。

##### actor_mode 记录

Actor 将执行路径写入 task.outputs：

```json
{
  "actor_mode": "skill",         // "skill" | "tool_use" | "text"
  "skill_used": "code_review",   // 仅 actor_mode=skill 时有值，等于 task.skill_name
  "tool_calls_made": ["bash_exec"],
  "text": "...",
  "usage": { "input_tokens": 123, "output_tokens": 456 }
}
```

---

### 4.4 Observer

**输入**：Session、List[ActorResult]、ReasoningContext  
**输出**：ObserverVerdict  
**是否调用 LLM**：是（1 次，可选降级为规则判断）

#### Observer 的判断逻辑

```
Observer.observe(session, results, ctx) → ObserverVerdict
  │
  ├─ 收集本轮执行摘要：
  │    all_outputs = [r.output for r in results if r.success]
  │    all_errors  = [r.error  for r in results if not r.success]
  │
  ├─ 构建 messages:
  │    [system: OBSERVER_SYSTEM_PROMPT]
  │    [user: 
  │       "Original goal: {session.goal}"
  │       "This turn's tasks and results: {...}"
  │       "Previous summary: {ctx.summary_text}"
  │       "Question: Is the original goal fully achieved?"
  │    ]
  │
  ├─ LLMClient.send_message(tools=[submit_observation])
  │
  └─ 解析 submit_observation tool call → ObserverVerdict
```

#### submit_observation 工具

```python
@tool_result
def submit_observation(
    done: Annotated[bool, "True if the original session goal is fully and completely achieved"],
    summary: Annotated[str, "Concise summary of what was accomplished this turn (1-3 sentences)"],
    reasoning: Annotated[str, "Internal reasoning: why done=True/False, what's still missing"],
) -> ToolResult:
    """Submit your observation of this turn's execution results."""
    return ToolResult(content="")
```

**关键设计**：
- Observer 是独立的 LLM 调用，与 Planner 的偏见隔离——即使 Planner 认为"快完成了"，Observer 可以客观评估
- `summary` 直接写入 Memory（role=assistant），成为下一轮 `ctx.summary_text` 的来源
- `reasoning` 只进日志，不进 Memory，保持 Memory 的简洁性
- **降级**：如果 Observer LLM 调用失败，退化为规则判断（所有 task 成功 + 无错误 → done=False，继续；token budget < 10% → done=True 强制结束）

---

## 5. 新 Loop 主流程

```python
def run(session_id: str, agent_id: str) -> None:
    agent = _load_agent(agent_id)
    agent.status = "RUNNING"
    
    try:
        while True:
            session = session_svc.get(session_id)
            _check_guard(session, agent)          # token + turns

            # ── Phase 1: Reason ──────────────────────────────────────
            ctx = reasoner.reason(session, agent)

            # ── Phase 2: Plan ────────────────────────────────────────
            plan = planner.plan(ctx, agent)
            agent.loop_guard.turns_used += 1
            agent_store.save(agent)

            # ── Phase 3: Act ─────────────────────────────────────────
            results: list[ActorResult] = []
            hitl_triggered = False

            for task in plan.tasks:
                if task.type == "user_input":
                    # 直接创建 HITL 暂停，不经过 Actor
                    _pause_for_user_input(task, session_id, agent_id)
                    hitl_triggered = True
                    break

                db_task = task_svc.create(session_id, agent_id, ...)
                result = actor.act(db_task, ctx, agent)
                results.append(result)

                if result.hitl_task_id:
                    hitl_triggered = True
                    break

            if hitl_triggered:
                return   # session 已转为 WAITING_INPUT

            # ── Phase 4: Observe ─────────────────────────────────────
            verdict = observer.observe(session, results, ctx)

            # 写 Memory
            memory_svc.append_message(
                session_id, agent_id,
                role="assistant",
                content=verdict.summary,
            )
            # 写 Blackboard
            blackboard_svc.publish(session_id, "_root", agent_id, verdict.summary)

            # 触发压缩（如需要）
            if memory_svc.should_summarize(session_id):
                _do_summarize(session_id, agent_id)

            if verdict.done:
                session_svc.transition(session_id, "SUCCEEDED")
                break

    except _WaitingForInputSignal:
        pass   # 已在 Actor 内部处理状态转换

    except AppError as e:
        session_svc.transition(session_id, "FAILED")
        raise

    finally:
        agent = _load_agent(agent_id)
        if agent.status == "RUNNING":
            agent.status = "FINISHED"
            agent_store.save(agent)
```

---

## 6. 与现有组件的对照映射

| 现有组件/概念 | 新架构对应 | 变化 |
|-------------|-----------|------|
| `AgentLoop._observe()` | `Reasoner.reason()` | 职责扩展：增加工具/技能语义检索 |
| `AgentLoop._plan()` + `Planner` | `Planner.plan()` | 大幅瘦身：只输出 {title, description}，去掉执行细节 |
| `AgentLoop._create_tasks()` | `Planner.plan()` 内部 | 合并到 Planner 输出，不再单独存在 |
| `AgentLoop._execute()` + `TaskExecutor` | `Actor.act()` | 重构：Actor 自主决策执行方式，支持多轮 tool use |
| `AgentLoop._update_memory()` | `Observer.observe()` + memory write | 质变：从"机械追加"变为"评估后总结" |
| `task.type = reasoning` | Actor 纯文本输出路径 | 不再是 task 类型，是 Actor 的一种执行结果 |
| `task.type = tool-call` | Actor 的 tool use 路径 | 同上 |
| `task.type = skill` | Actor 注入 skill instructions 路径 | 同上 |
| `task.type = user_input` | Planner 输出 / Actor 调用 `request_human_input` | 保留，两个触发路径 |
| `submit_plan.done` | 移至 Observer 的 `submit_observation.done` | 独立评估，不由 Planner 自评 |
| `submit_plan.summary` | 移至 Observer 的 `submit_observation.summary` | Observer 生成的总结质量更高 |
| `SkillRouter.build_skill_prompt()` | Actor 内部 `SkillRegistry.load_full(task.skill_name)` | 选择权移至 Planner（LLM 决策）；instructions 追加到 system prompt 而非替换 |
| `PolicyEngine` | 保留，位置不变（Actor 调 ToolGateway 时触发） | 无变化 |
| `CompactionStrategy` | 保留，Loop 主流程调用 | 无变化 |

---

## 7. Task 存储模型变化

### 现有 task.type 值
`reasoning` | `tool-call` | `skill` | `user_input`

### 新 task.type 值
`atomic` | `user_input`

`atomic` task 的 `outputs` 字段记录 Actor 实际使用的执行方式（便于可观测性）：

```json
{
  "actor_mode": "tool_use",          // "tool_use" | "text" | "skill"
  "tool_calls_made": ["bash_exec"],
  "skill_used": null,
  "text": "...",
  "usage": { "input_tokens": 123, "output_tokens": 456 }
}
```

---

## 8. 新增 Guard 参数

| 参数 | 位置 | 含义 | 默认值 |
|------|------|------|--------|
| `actor_max_tool_rounds` | Agent.loop_guard | 单个 atomic task 内 Actor 最多调用工具的轮次 | 5 |
| `observer_skip_threshold` | Session | 连续多少轮 Observer 跳过 LLM（全部失败时降级为规则） | 3 |

---

## 9. LLM 调用次数对比

| 架构 | 每 Loop 轮次的 LLM 调用数 |
|------|--------------------------|
| **现有** | 1（Planner）+ N（TaskExecutor，每 task 1 次） |
| **新架构** | 1（Planner）+ N×M（Actor，每 task 最多 M 轮 tool use）+ 1（Observer） |

新增了 Observer 的 1 次调用，但换来了：
1. 完成判断独立性（不再自评）
2. Memory 中的总结质量可控
3. 失败恢复决策更准确

---

## 10. 实现顺序建议

1. **定义数据结构**：`ReasoningContext`、`PlannedTask`、`TaskPlan`、`ActorResult`、`ObserverVerdict`
2. **实现 Reasoner**：从现有 `_observe()` + Planner 的检索逻辑迁移
3. **重构 Planner**：精简 `submit_plan` schema，去掉执行细节字段
4. **实现 Actor**：多轮 tool use 循环 + `request_human_input` 内置工具
5. **实现 Observer**：`submit_observation` 工具 + LLM 调用 + 降级规则
6. **重构 AgentLoop.run()**：串联四个阶段
7. **迁移 task.type**：`reasoning`/`tool-call`/`skill` → `atomic`，更新前端 TaskType 类型
8. **更新测试**：`test_phase_implementations.py`、`test_builtins.py`
