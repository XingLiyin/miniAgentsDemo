# miniAgents 后端服务完整设计文档

> 最终态版本 · 2026-04-28
>
> 本文档基于初版设计（v1.0 Final，2026-03-26）整合后续所有优化迭代，是当前系统的权威参考。

---

## 目录

1. [项目目标与范围](#1-项目目标与范围)
2. [总体架构](#2-总体架构)
3. [核心领域模型](#3-核心领域模型)
4. [Agent 定义文件系统](#4-agent-定义文件系统)
5. [Agent Loop 与 Guard 机制](#5-agent-loop-与-guard-机制)
6. [调度架构：SM / TM / LM 三层分工](#6-调度架构sm--tm--lm-三层分工)
7. [Task 全生命周期](#7-task-全生命周期)
8. [Agent 生命周期管理](#8-agent-生命周期管理)
9. [Tool 执行机制](#9-tool-执行机制)
10. [上下文管理机制](#10-上下文管理机制)
11. [Blackboard 发布订阅系统](#11-blackboard-发布订阅系统)
12. [Agent 协作模型（动态多层树）](#12-agent-协作模型动态多层树)
13. [Human-in-the-Loop（HITL）](#13-human-in-the-loophitl)
14. [Memory 管理](#14-memory-管理)
15. [LLM 适配层](#15-llm-适配层)
16. [REST API 设计](#16-rest-api-设计)
17. [数据库 Schema 汇总](#17-数据库-schema-汇总)
18. [事件类型全集](#18-事件类型全集)
19. [可观测性与非功能设计](#19-可观测性与非功能设计)
20. [技术选型](#20-技术选型)
21. [核心设计原则](#21-核心设计原则)

---

## 1. 项目目标与范围

miniAgents 是一个 **Agent 后端服务**，以学习验证架构思路为首要目标，同时构建稳定、可扩展、可观测的基础设施 Demo。核心理念是：先跑通最小可运行的 Agent Loop，再逐步迭代安全防护与可观测性。

### 1.1 核心能力

- **Agent 级 Memory 管理**：短期消息窗口 + 滚动摘要压缩（Compact）+ pgvector 语义检索
- **Session 级调度管理**：TaskQueue（LIFO + blocked DAG 感知）+ 三层调度分工（SM/TM/LM）
- **动态多层 Agent 树**：任意 agent 可通过 spawn 晋升为局部 root，形成运行时树形结构
- **Blackboard 发布订阅**：topic 主键、与 agent 实例解耦、父→子单向结果传递
- **内置工具执行**：Builtin（bash_exec、http_request 等）/ MCP / Control 三大类，双阶段授权
- **Human-in-the-Loop**：Task 级执行前人工确认，支持 approve / reject / modify
- **Loop 安全防护**：token_budget 硬终止 + failure_threshold 软暂停 + 并发数量上限
- **项目背景注入**：working_dir/BACKGROUND.md 静态注入 system prompt

### 1.2 V1 范围约束

| 边界 | 说明 |
|------|------|
| 单租户优先 | 后续平滑扩展为多租户 |
| Python + FastAPI 主栈 | PostgreSQL（pgvector）+ Redis |
| 先跑通核心 Loop | Guard 机制先于 Observability 落地 |
| 串行调度为主 | TaskQueue 为 LIFO 单路，并行调度为未来扩展点 |
| 不支持 agent 间实时流式协作 | V2 可考虑 streaming entry |

---

## 2. 总体架构

### 2.1 架构分层

| 层次 | 职责 |
|------|------|
| **API 层** | REST/WebSocket 接口；鉴权（JWT/API Key）、参数校验、限流；Idempotency-Key 幂等；X-Request-Id 全链路透传 |
| **Orchestrator 层** | SessionManager 创建/恢复 Session；TaskManager 调度决策；LifecycleManager agent tree 管理 |
| **Worker Runtime 层** | AgentLoop（Reasoner→Actor→Observer）；token_budget 硬检查；failure_counter；Skill Router |
| **LLM 适配层** | 统一 LLMClient 门面；OpenAIAdapter / AnthropicAdapter；Tool Calling 格式归一；token 用量统计 |
| **Tool Gateway 层** | ToolGateway 统一入口；PolicyEngine 双阶段授权；Builtin/MCP/Control 三类 handler；调用全量审计脱敏 |
| **存储层** | PostgreSQL（事务数据 + pgvector 向量索引）；Redis（短期缓存、分布式锁）；Blackboard 文件系统 |
| **Observability 层** | OpenTelemetry 全链路 Trace；Prometheus 指标；结构化 JSON 日志；Event Bus |

### 2.2 逻辑组件

| 组件 | 职责简述 |
|------|----------|
| **SessionManager (SM)** | 创建/恢复/终止会话；通知 TM 启动调度 |
| **TaskManager (TM)** | 全部 task 层决策：状态转换、调度、重试、resume 父任务、spawn vs inline 决策 |
| **TaskQueue** | per-session LIFO 栈（_ready）+ blocked 集合；DAG dep-aware push/pop |
| **LifecycleManager (LM)** | agent tree 管理：注册、装配（prepare_executor）、回收（_settle_executor）、起线程执行 |
| **AgentLoop** | 单次 task 执行驱动：Reasoner → Actor → Observer 循环 |
| **HITL Manager** | 管理待确认 Task 队列；提供 approve/reject/modify API；超时自动取消 |
| **Memory Service** | 消息写入；Compact 压缩；pgvector 语义检索；Token 预算拼装 |
| **Blackboard Service** | topic 文件管理；publish/pull 接口；游标增量读取 |
| **Tool Gateway** | ToolRegistry + PolicyEngine；Builtin/MCP/Control 三路执行；调用脱敏审计 |
| **Skill Router** | description 匹配 + 显式触发；按需加载 SKILL.md；生成执行计划 |
| **LLM Registry** | 供应商注册/切换；运行时动态注册 |
| **Event Bus** | 内部事件发布订阅；TASK_EXECUTION_FINISHED/FAILED 驱动调度 |

---

## 3. 核心领域模型

### 3.1 Session

一次目标导向的 LLM 会话容器，持有完整的调度上下文和 Guard 参数。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR PK | ses_ULID |
| user_prompt | TEXT | 用户原始输入 |
| goal | TEXT | 会话目标（初始值同 user_prompt，等待 metadata_filler 提炼后写入） |
| status | VARCHAR | QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELED / WAITING_INPUT |
| template_id | VARCHAR FK | 关联的 AgentTemplate（root agent 使用） |
| root_agent_id | VARCHAR FK | 会话入口 Agent（spawn_depth=0） |
| token_budget | INTEGER DEFAULT 200000 | 全局 token 上限（硬限制） |
| token_used | INTEGER DEFAULT 0 | 已消耗 token |
| root_max_turns | INTEGER DEFAULT 20 | Session root agent Loop 轮次上限（软限制） |
| failure_counter | INTEGER DEFAULT 0 | 连续失败次数 |
| failure_threshold | INTEGER DEFAULT 3 | 达到阈值时触发 HITL 介入（Phase 2） |
| metadata | JSONB | 扩展元数据（运行时统计等） |

> **注：** `max_concurrent_tasks`、`max_concurrent_agents` 由 LMState 在内存中维护（见第8章），不存在于 Session 模型中。

**Session 状态机：**

```
QUEUED       → RUNNING, CANCELED
RUNNING      → SUCCEEDED, FAILED, CANCELED, WAITING_INPUT
WAITING_INPUT → RUNNING, QUEUED, CANCELED
SUCCEEDED    → QUEUED   （用户续话）
FAILED       → QUEUED   （用户重试）
CANCELED     → （终态）
```

- `WAITING_INPUT`：agent 调用 `request_human_input` 或 Observer 裁决 needs_user_input 时进入，等待用户应答
- token_budget 耗尽 → 立即 FAILED，发布 TOKEN_BUDGET_EXCEEDED 事件

### 3.2 Task

会话内的最小工作单元，具有完整的状态机和派生关系追踪。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR PK | tsk_ULID |
| session_id | VARCHAR FK | 所属会话 |
| creator_agent_id | VARCHAR FK | 创建此 Task 的 Agent |
| assigned_agent_id | VARCHAR FK | 执行此 Task 的 Agent |
| status | ENUM | PENDING → ACTIVE → TO_BE_OBSERVED → FINISHED / FAILED / SUSPENDED / CANCELED |
| user_prompt | TEXT / list | task 的用户输入（str 或多模态 ContentPart list） |
| title | VARCHAR | 简短标题，供展示和 Agent 识别用 |
| description | TEXT | 详细描述 |
| settings | JSONB | 扩展配置：use_subagent / subagent_template / inherit_memory / skill_name 等 |
| result | TEXT | 任务结果文本 |
| outputs | JSONB | 结构化输出（如 progress_text 等） |
| error | TEXT | 失败时的错误信息 |
| dag_deps | JSONB | 依赖的前置 task id 列表，用于 TaskQueue 阻塞/就绪判断 |
| parent_task_id | VARCHAR | 派生关系：此 Task 是哪个 SUSPENDED 祖先 Task 的子任务 |
| retry_count | INTEGER | 当前重试次数 |
| conversation_turns | JSONB | 任务相关对话历史（Agent 内部维护） |
| progress_text | TEXT | 执行中的进度描述 |

**运行时临时字段（不持久化，control_tools 直接写入，AgentLoop 直接读取）：**
- `actor_done`: bool — control_tools 设置，通知 loop 本轮 Actor 已完成
- `actor_outcome`: str — Actor 裁决结果（success/failed/active/needs_user_input）
- `actor_result`: str — Actor 输出的结果文本
- `actor_summary`: str — 用于写入 memory 的摘要
- `proceed_to_review`: bool — 是否进入 Observer 评估阶段

**完整状态机合法转换：**

```
PENDING        → ACTIVE, CANCELED, FINISHED, TO_BE_OBSERVED
ACTIVE         → FINISHED, FAILED, CANCELED, SUSPENDED, TO_BE_OBSERVED
SUSPENDED      → ACTIVE, PENDING, FAILED, CANCELED
TO_BE_OBSERVED → SUSPENDED, FINISHED, FAILED, PENDING
FINISHED       → PENDING   （reopen）
FAILED         → PENDING   （retry）
CANCELED       → （终态）
```

### 3.3 Agent

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR PK | agt_ULID |
| session_id | VARCHAR FK | 所属会话 |
| template_id | VARCHAR FK | 关联的 AgentTemplate |
| name | VARCHAR | agent 名称 |
| status | ENUM | IDLE → RUNNING → WAITING → RUNNING（恢复）→ FINISHED / FAILED |
| soul_md | TEXT | Actor 阶段 system prompt（执行人格，来自 SOUL.md） |
| role_md | TEXT | Observer 阶段 system prompt（评判准则，来自 ROLE.md） |
| act_tool_list | JSONB | Actor 阶段显式工具名称列表 |
| observe_tool_list | JSONB | Observer 阶段显式工具名称列表 |
| mcp_act_servers | JSONB | Actor 阶段订阅的 MCP server 列表 |
| mcp_observe_servers | JSONB | Observer 阶段订阅的 MCP server 列表 |
| skill_list | JSONB | 可用 Skill 名称列表 |
| loop_guard | JSONB | { actor_max_tool_rounds, observer_max_tool_rounds, context_tokens, context_limit } |
| has_spawn_permission | BOOLEAN | 是否允许派生子 agent |
| spawn_depth | INTEGER DEFAULT 0 | 在 agent 树中的层级深度，session root = 0 |
| inherit_memory | BOOLEAN DEFAULT true | spawn 时是否复制父 agent 的记忆 |
| llm_provider | VARCHAR | 指定的 LLM 供应商名称 |
| llm_model | VARCHAR | 指定的模型（空则使用供应商默认） |
| soul_path | VARCHAR | SOUL.md 文件路径（懒加载使用） |
| settings | JSONB | 运行时扩展配置（如 working_dir） |

> **注：** `parent_agent_id` 和当前 task 信息由 LifecycleManager 的 AgentMeta 在内存中维护（见第8章），不存在于 Agent 持久化模型中。

**Agent Status 流转：**

```
IDLE ──→ RUNNING ──→ FINISHED   （正常完成）
                 └──→ WAITING    （task SUSPENDED，等子任务）
                        └──→ RUNNING  （task 恢复，重新执行）
                               └──→ FINISHED
```

### 3.4 AgentTemplate

AgentTemplate 是 Agent 实例的配置原型，由 AgentLoader 从文件系统扫描生成，存储在内存注册表中。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR PK | tpl_ULID |
| name | VARCHAR | 模板名称（与 agent 目录名对应） |
| version | VARCHAR | semver 版本号 |
| description | VARCHAR | 模板描述 |
| act_tool_list | JSONB | Actor 阶段工具白名单（来自 SOUL.md Frontmatter tools.required） |
| observe_tool_list | JSONB | Observer 阶段工具白名单（来自 ROLE.md Frontmatter tools.required） |
| mcp_act_servers | JSONB | Actor 阶段 MCP server 列表 |
| mcp_observe_servers | JSONB | Observer 阶段 MCP server 列表 |
| has_spawn_permission | BOOLEAN DEFAULT false | 是否允许调用 spawn 晋升 |
| source_dir | VARCHAR | agent 目录路径（SOUL.md 等文件的所在位置） |

> **注：** system_prompt 不作为独立字段存储——Actor 阶段 soul_md 和 Observer 阶段 role_md 分别从 SOUL.md、ROLE.md 读取后直接注入 Agent 实例。`max_spawn_tasks`、`skill_list`、`memory_config`、`default_model` 等高级配置为 Phase 2 扩展点，当前未实现。

### 3.5 HITL 实现（当前）

当前 HITL 通过 **阻塞等待 + Session 状态挂起** 实现，而非独立的 HitlApproval 实体：

1. Agent 调用 `request_human_input` 或 Observer 裁决 `needs_user_input`
2. Session 状态切换为 `WAITING_INPUT`
3. SSE 推送 `waiting_input` 事件给前端（含提示文本）
4. `hitl_store.wait()` 阻塞当前 agent 线程，等待 `POST /sessions/{id}/input` 应答
5. 用户提交应答 → `hitl_store` 解除阻塞 → Session 恢复 `RUNNING`

> **Phase 2 扩展：** 独立的 HitlApproval 实体（含 trigger_reason / task_snapshot / expire_at 等字段）和正式的 `PENDING_APPROVAL` task 状态，支持超时取消、修改 inputs、多级审批，留待后续实现。

---

## 4. Agent 定义文件系统

Agent 的身份定义以 **Markdown 文件**形式由用户在本地维护，是创作物（source of truth），系统从中导入生成 agent_templates 和 tool_list。

### 4.1 四个文件的职责

| 文件 | 定义什么 | 说明 |
|------|----------|------|
| **SOUL.md** | agent 是谁 | 人格、价值观、行为风格、边界原则。每轮 Loop 始终注入，优先级最高 |
| **ROLE.md** | agent 做什么 | 职责范围、能力边界、不做什么。每轮始终注入 |
| **TOOLS.md** | agent 用什么 | 工具使用指南；Frontmatter 中 tools 字段直接写入 tool_list 白名单 |
| **STYLE.md** | agent 怎么输出 | 输出语言、格式偏好。可选注入（inject_style=true 时） |

每个文件使用 YAML Frontmatter + Markdown 正文，必填字段：`name`、`version`（semver）、`description`；TOOLS.md 额外必填 `tools` 列表。

### 4.2 导入流程

1. 接收四个 MD 文件（至少 SOUL.md + ROLE.md）
2. Frontmatter 解析：提取元数据，正文存入 `agent_definition_files` 表
3. TOOLS.md → tool_list：优先读 Frontmatter `tools` 字段（快速路径），否则 LLM 提取（慢速路径）
4. 生成/更新 `agent_templates` 记录
5. 若该 template 已有运行中 agent 实例，推送 `TEMPLATE_UPDATED` 事件
6. 返回 template_id、提取到的 tool_list、解析警告

### 4.3 懒加载与缓存机制

Agent 实例化时触发懒加载，拼装 system prompt 后缓存至 Redis：

```
缓存 Key：agent_prompt:{template_id}:{soul_version}:{role_version}:{tools_version}:{style_version}
```

版本号变化时 key 自然失效，无需主动删除。强制刷新只在 Loop 轮次边界（UpdateMemory 阶段结束后）生效，不在执行中途切换 prompt。

### 4.4 System Prompt 组装顺序

| # | 来源 | 条件 | 说明 |
|---|------|------|------|
| S1 | SOUL.md 正文 | 始终 | agent 人格与价值观，最高优先级 |
| S2 | BACKGROUND.md | working_dir 存在时 | 项目背景（见第10章），置于 soul 之后 |
| S3 | 资源列表 | 始终 | Available Skills / Tools / Sub-Agents |
| S4 | skill_instructions | 需要时 | Skill 执行指南 |

> **Skill 不放入 system prompt**：按需加载 SKILL.md 正文，避免刚性 token 开销。

---

## 5. Agent Loop 与 Guard 机制

### 5.1 AgentLoop 执行结构

AgentLoop 驱动单次 task 的完整执行，分为三个阶段：

**Reasoner（构建上下文）**

- 加载 soul_md / role_md / skill_instructions（来自 SOUL.md 等文件）
- 加载 BACKGROUND.md（项目背景）
- 拉取 recent_messages 消息窗口
- 拉取子任务的 Blackboard snippets（`bb_svc.pull(session_id, child.task_id, agent_id)`）
- 拼装 ReasoningContext 传给 PromptBuilder

**Actor（LLM 推理 + 工具调用）**

- 多轮 LLM 调用，执行 tool use（bash / http / control_tools 等）
- Control tools 直接操作 task/session 状态（submit_plan、submit_task、replan 等）
- 任务挂起（SUSPENDED）时直接 return，不进入 Observer

**Observer（评估裁决）**

- 构建评估上下文（role_md + 本轮 Actor transcript）
- 调用 `submit_task_assessment` 写入 task 最终状态
- 裁决：success → FINISHED；failed → FAILED；active → PENDING（重新入队）；needs_user_input → HITL

### 5.2 Guard 机制

**硬限制（超出立即 FAILED）：**

- `token_budget`：session.token_used >= session.token_budget → TOKEN_BUDGET_EXCEEDED → session FAILED
- `max_concurrent_tasks`：ACTIVE Task 数上限，超出时 Schedule 阶段阻塞
- `max_concurrent_agents`：含 WAITING 的存活 Agent 数上限；spawn 时 LM 检查，超限拒绝
- `max_spawn_tasks`：单次 spawn 最多派生子 Task 数，超限返回 PLAN_TOO_LARGE
- `token_budget × 80% 阈值`：spawn 时余量不足返回 TOKEN_BUDGET_LOW

**软限制（警告 + 暂停）：**

- `failure_threshold`：连续失败 >= 3 次 → 触发 HITL（Phase 2 实现）
- `root_max_turns`：session.root_max_turns 超限 → 发布 MAX_TURNS_EXCEEDED 事件，请求人工决策
- `actor_max_tool_rounds`：单个 task 内 Actor 工具调用轮次超限 → 当前 Task FAILED
- `observer_max_tool_rounds`：Observer ReAct 循环轮次超限 → Observer 退出
- 工具连续失败：同一工具连续失败 → 写警告事件

### 5.3 并发计数规则

| 状态 | 计入 concurrent_tasks | 计入 concurrent_agents |
|------|----------------------|----------------------|
| Task: PENDING / PENDING_APPROVAL | ✗ 否 | — |
| Task: ACTIVE | ✓ 是 | — |
| Task: SUSPENDED | ✗ 否（挂起不占槽） | — |
| Agent: RUNNING | — | ✓ 是 |
| Agent: WAITING（spawn 后等待子任务） | — | ✓ 是（仍占槽，防无限派生） |
| Agent: FINISHED（待回收） | — | ✗ 否 |

---

## 6. 调度架构：SM / TM / LM 三层分工

### 6.1 职责边界

```
SessionManager (SM)
    职责：创建 Session / root Agent / 初始 Task，通知 TM 启动
    对外接口：SM → TM.start_session

TaskManager (TM)
    职责：所有 task 层决策——状态转换、调度、重试、resume 父任务、spawn vs inline 判断
    对外接口：TM → LM.prepare_executor / release / run_agent

LifecycleManager (LM)
    职责：agent tree 管理——注册、装配、回收；执行层起 daemon thread
    对外接口：LM → EventBus(TASK_EXECUTION_FINISHED / TASK_EXECUTION_FAILED)
```


### 6.2 Session 启动流程

```
API
  └─ SM.create_session
       ├─ session_svc.create()
       ├─ agent_store.save(root_agent)
       ├─ LM.init_session() + register_root_agent()   写入 registry, parent_id=None
       ├─ TM.init_session()                           初始化 TaskQueue（必须在 task 创建前）
       └─ SM._create_initial_task()                   task_svc.create(PENDING) → TASK_CREATED 事件 → push 入栈

  └─ SM.schedule_loop(session_id)
       └─ TM.start_session
            ├─ session QUEUED → RUNNING
            └─ task_queue.pop() → _dispatch_next → LM
```

### 6.3 _dispatch_next（TM → LM 唯一通道）

```
TM._dispatch_next(session_id, finished_agent_id, next_task)

  next_task = None → LM.release(session_id) → 清理 agent tree → return

  task_svc.transition(next_task.id, ACTIVE)

  agent_id = LM.prepare_executor(finished_agent_id, task_id, use_subagent, template_name, inherit_memory)
    └─ _settle_executor:
         finished 是 FINISHED sub-agent → 回收，返回 parent
         finished 是 WAITING → 直接返回（保留作为 parent）
         finished 是 root（depth=0）→ 直接返回
       use_subagent=False → 返回 executor
       use_subagent=True  → _check_spawn_permission
           通过  → _instantiate_sub_agent(parent_id=executor)，写入 registry
           拒绝  → 降级 inline，返回 executor（发 SPAWN_REJECTED 事件）

  LM.run_agent(agent_id, task_id)
    → 更新 registry.task_id
    → daemon thread → LM._execute → AgentLoop.run
```

### 6.4 TaskQueue 设计

**数据结构：两区分离**

```
_ready:   list[str]  栈（LIFO）—— dep 已满足，可立即执行
_blocked: set[str]   集合      —— dag_deps 未满足，等待中
```

选用栈而非队列：子任务（如 submit_task 产生的）入栈顶，优先于其他任务执行，自然产生深度优先执行顺序。

| 方法 | 语义 |
|------|------|
| `push(session_id, task_id)` | deps 满足 → 入栈；不满足 → 入 blocked |
| `pop(session_id) -> Task` | 弹出栈顶（LIFO），跳过非 PENDING |
| `notify_completed(session_id, task_id)` | task 完成后，将 blocked 中 deps 现已满足的 task 升入栈 |
| `is_empty / has_work` | 两区均空 → session 可结束；有任一非空 → 还有任务 |

**三个显式 Push 点：**

| 时机 | 位置 | 操作 |
|------|------|------|
| task 被创建 | 订阅 TASK_CREATED → on_task_created | push |
| 父任务从 SUSPENDED 恢复 | _try_resume_parent resume 之后 | push |
| 失败任务重试 | on_task_failed retry 之后 | push |

### 6.5 Task 完成后续接

```
LM._execute 完成 → 发布 TASK_EXECUTION_FINISHED

TM.on_task_finished
  ├─ record_success
  ├─ [session lock]
  │    task_queue.notify_completed(finished_task_id)   → 将 blocked 中满足 deps 的 task 升入栈
  │    _try_resume_parent(finished_task_id)
  │        → 若父任务 SUSPENDED 且所有子任务 terminal
  │            → task_svc.resume(parent) → task_queue.push(parent)
  │    task_queue.pop() → next_task
  │    next_task = None + is_empty()  → session SUCCEEDED
  │    next_task = None + has_work()  → deps 等待中，暂不调度
  └─ _dispatch_next(agent_id, next_task)
```

---

## 7. Task 全生命周期

### 7.1 Task 产生路径

两条路径：
- **Session 启动时**：SM 创建初始 task（通常是 plan 类型），状态 PENDING
- **Spawn 时**：Agent 执行期间调用 control_tools（submit_plan / submit_task），创建子 task，parent_task_id 指向当前 SUSPENDED 的父 task

### 7.2 Observer 裁决表

| Observer 裁决 | task 状态 | 后续 |
|--------------|-----------|------|
| success | FINISHED | 结果写 Blackboard，发 TASK_EXECUTION_FINISHED |
| failed | FAILED | 发 TASK_EXECUTION_FAILED |
| active | PENDING | 任务重新入队，下一轮 Actor 继续 |
| needs_user_input | PENDING | HITL 暂停 |

Observer 降级规则（LLM 不可用时走规则兜底）：token 用量 > 90% → FINISHED；result.success → FINISHED；否则 → FAILED。

### 7.3 Control Tools 触发的状态变更

| 工具 | 触发的状态变更 |
|------|--------------|
| `submit_plan` | 父 task → SUSPENDED；批量创建子 task（PENDING），建立 dag_deps 链 |
| `submit_task` | 父 task → SUSPENDED；创建单个子 task（PENDING） |
| `submit_task_assessment` | → FINISHED / FAILED / PENDING（active 路径） |
| `replan` | 当前 task → FINISHED；cancel_pending（所有 PENDING → CANCELED）；创建新 plan task |

### 7.4 Task Suspend 机制

整个流程分三个阶段：

1. **触发挂起**（control_tools.py）：创建子任务 → 父任务 → SUSPENDED → 设置 `task.actor_done = True`，让 agent loop 正常退出本轮迭代
2. **Agent Loop 识别挂起**：每次 loop 迭代开始前检查 `task.status == "SUSPENDED"` → 直接退出
3. **自动恢复**（TM._try_resume_parent）：监听子任务完成事件 → 所有同父子任务全部进入终态后 → `task_svc.resume(parent)` → push 回栈

```
父任务: ACTIVE → (spawn) → SUSPENDED → (所有子任务终态) → PENDING → ACTIVE
子任务:                    PENDING → ACTIVE → FINISHED
```

**关键设计点**：挂起不是阻塞等待，而是"状态标记 + 事件驱动恢复"，父任务的 actor 不持有任何锁或线程。

---

## 8. Agent 生命周期管理

### 8.1 核心数据结构

**AgentMeta（内存，per-session）**

```python
@dataclass
class AgentMeta:
    agent_id: str
    parent_id: str | None   # None for root agent
    task_id: str | None
    spawn_depth: int
    status: str  # RUNNING / WAITING / FINISHED
```

**LMState（内存，per-session）**

```python
@dataclass
class LMState:
    session_id: str
    root_agent_id: str
    max_concurrent_agents: int  # 默认 5
    max_concurrent_tasks: int   # 默认 10
    concurrent_agents: int
    concurrent_tasks: int
    agent_registry: dict[str, AgentMeta]
```

每个活跃 agent 在 `LMState.agent_registry` 中有一条 meta，是生命周期决策的依据。agent_store 持久化完整 Agent 记录，parent_id 等 tree 关系仅在 LMState 内存中维护。

### 8.2 Root Agent 的创建与注册

```
API → SM.create_session
    → Agent(name="root", status="IDLE", spawn_depth=0, has_spawn_permission=True)
    → agent_store.save()
    → LM.init_session()                 初始化 LMState
    → LM.register_root_agent()          写入 registry，status="RUNNING"
```

Root agent 是唯一 spawn_depth=0 的 agent，整个 session 期间不被中途回收，仅在 release 时清理。

### 8.3 _settle_executor 回收规则

| 情况 | 处理 |
|------|------|
| depth=0（root） | 直接返回 root，永不中途回收 |
| status=WAITING | 直接返回（保留，作为 parent） |
| status=FINISHED（sub-agent） | 回收，返回 meta.parent_id |

### 8.4 _check_spawn_permission 检查项

- `spawn_depth >= max_spawn_depth` → 拒绝，inline fallback
- `concurrent_agents` 超限 → 拒绝
- `token > 90%` → 拒绝

### 8.5 关键设计约束

| 规则 | 实现位置 |
|------|----------|
| Root 永不中途回收 | _settle_executor: depth=0 直接返回 |
| WAITING agent 不回收 | _settle_executor: status=WAITING 直接返回 |
| FINISHED sub-agent 回收到直接 parent | _settle_executor: 返回 meta.parent_id |
| 子任务必须 spawn（不能 inline） | prepare_executor: WAITING branch 强制 use_subagent=True |
| 深度限制控制树的层数 | _check_spawn_permission + has_spawn_permission |
| Session 结束全清 | release: 遍历回收所有 registry 条目 |
| Daemon agent 不干扰主流程 | _is_tracked 守卫，不在 registry 的 agent 完成后静默退出 |

---

## 9. Tool 执行机制

### 9.1 执行流程

所有 tool 调用统一经过 ToolGateway：

```
LLM 输出 tool_call
    ↓
PolicyEngine.authorize()   ← 鉴权（两层）
    ↓
写 RUNNING 审计记录
    ↓
ToolRegistry.get(name).handler(args, ctx)  ← 实际执行
    ↓
写 SUCCEEDED/FAILED 审计记录，返回 ToolResult
```

`ToolResult` 结构统一：`content`、`is_error`、`error_code`、`metadata`（含 exit_code、http_status 等）。

### 9.2 Tool 分类

| 类型 | 数量 | 执行方式 | 代表工具 |
|------|------|----------|----------|
| **Builtin** | 若干 | 进程内 Python 函数 | bash_exec, http_request, read/write/glob, exec_skill_script |
| **MCP** | 动态 | 远程 MCP 协议（stdio/HTTP） | @mcp/server-filesystem 等外部 MCP server |
| **Control** | 6 个 | 内部状态变更 | request_human_input, submit_plan, submit_task, submit_task_assessment, replan, update_task_metadata |

Control Tools 是特殊内部工具，直接操作 task/session 状态，不走普通鉴权流程。

### 9.3 双阶段授权（Actor / Observer）

Agent 运行时分为 **Actor**（执行）和 **Observer**（评估）两个阶段，各自有独立的 tool 授权列表：

| 阶段 | 权限字段 | 典型用途 |
|------|----------|----------|
| Actor | act_tool_list + mcp_act_servers | 有副作用操作：bash、写文件、HTTP 请求 |
| Observer | observe_tool_list + mcp_observe_servers | 只读评估：submit_task_assessment、request_human_input |

LLM 只能调用当前阶段被注入到 prompt 中的 tools，无法访问未授权的工具。

### 9.4 鉴权机制（PolicyEngine）

两层校验：
1. **工具存在性**：ToolRegistry.is_registered(tool_name)，否则抛 TOOL_NOT_FOUND
2. **Agent 权限**：工具名在 act_tool_list 或 observe_tool_list 中；或工具属于已订阅 MCP server 的全部工具；否则抛 TOOL_NOT_AUTHORIZED

内置安全限制（在 handler 层强制，不依赖鉴权）：
- `bash_exec`：黑名单过滤（rm -rf、sudo 等）+ 超时
- `http_request`：SSRF 防护（拦截 localhost/10.x/192.168.x 等）+ Header 脱敏
- `read/write/glob`：路径限制在 working_dir
- `exec_skill_script`：路径限制在 scripts/ 或 references/ 目录

### 9.5 Control Tools 详单

| 工具 | 作用 |
|------|------|
| `submit_plan(tasks)` | 批量创建子 task（建立 dag_deps 链）→ 父任务 SUSPENDED → 子任务依次执行 → 全完成后父任务恢复 |
| `submit_task(...)` | 创建单个子 task → 父任务 SUSPENDED → 子任务完成后父任务恢复 |
| `submit_task_assessment(task_outcome, task_result, task_reviews, next_step_hint)` | Observer 评估当前 task 结果（success/failed/active/needs_user_input）；可同时 review 兄弟 task |
| `replan(reason, summary)` | 取消所有 PENDING tasks → 当前 task FINISHED → 创建新 plan task（use_subagent=True） |
| `request_human_input(prompt, context)` | Session → WAITING_INPUT；阻塞等待用户应答；应答后 Session → RUNNING |
| `update_task_metadata(title, description, session_goal)` | 更新 task title/description 和 session goal；task 立即标记为 actor_done |

**submit_task_assessment 的 task_reviews 参数**（Observer 专用）：对同一 agent 下其他 FINISHED/PENDING 兄弟任务进行复核：
- `review_status = "reopen"` → FINISHED task 重新进入 PENDING
- `review_status = "skip"` → PENDING task 直接标记 FINISHED（视为间接完成）
- `review_status = "confirmed"` → 无状态变更，仅记录确认

---

## 10. 上下文管理机制

### 10.1 Actor 上下文组织

**System prompt（静态，每轮不变）：**

```
soul_md
───
## Project Background
{BACKGROUND.md 内容}（存在时注入）
───
## Available Skills / Tools / Sub-Agents
{resources 列表}
───
{skill_instructions}
```

**Messages（动态，两条路径）：**

普通路径：
```
[recent_messages 窗口，去掉最后一条 user 消息]
↓
user: {blackboard_snippets（Task Background）}
      {task.title + description}
      {task.user_prompt}
```

Resume 路径（memory 里有当前 task_id 的挂起记录）：
```
[recent_messages 全量还原，含 user_prompt + spawn note]
↓
user: {Sub-task results: blackboard_snippets}
      "Sub-tasks have completed. Please review the results and continue."
```

### 10.2 Observer 上下文组织

**System prompt：** role_md + 可用 control tools（submit_task_assessment、request_human_input）

**Messages（单条 user 消息）：**
```
Current task: title / description
User requirements: session.user_prompt
Execution transcript: Actor 本轮所有 turns（tool calls + llm_text）
[Session task list]（仅当存在可复核 sibling 时）
```

Observer 不感知 recent_messages 和 blackboard_snippets，只看本轮 Actor 的执行过程。

### 10.3 历史记忆的组织

消息以 `messages.jsonl` 追加存储，每轮任务结束写入两条：
```
role=user      ← task.user_prompt
role=assistant ← verdict.summary（Observer 的 task_result + next_step_hint）
```

**Suspend 时的内存写入（关键修复）：** SUSPENDED 提前 return 前写入两条记录：
```python
# agent_loop.py
if task.status == "SUSPENDED":
    if task.user_prompt:
        memory_svc.append_message(role="user", content=task.user_prompt, task_id=task_id)
    memory_svc.append_message(role="assistant", content=_summarize_spawn(result), task_id=task_id)
    return
```

Resume 检测：`is_resume = any(m.get("task_id") == task.id for m in ctx.recent_messages)`，检测到则走 Resume 路径。

### 10.4 Compact 压缩机制

**触发条件（满足任一即触发）：**
- 自上次 compact 以来新增消息数 ≥ `default_summary_threshold`（默认 20）
- 本轮 `context_tokens` ≥ `context_limit × 80%`（默认 180,000 × 80%）

SUSPENDED 路径不触发——loop 在写完挂起记录后直接 return，不到检查点。

**压缩执行：**
- 调用 `MemoryCompactionAgent`（soul 来自 `resources/agents/memory-compactor/SOUL.md`）
- 切分：`to_compact = messages[:-6]`，`kept = messages[-6:]`
- LLM 最多 8 轮，可调用 read/glob 查阅文件
- 输出 compacted_summary，拼为 `[Context so far]: ...` 前置消息
- 失败降级：截断为最后 keep_last 条，无摘要头

**压缩后变化：**
- `messages.jsonl` 归档为 `.bak.jsonl`，重写为 [Context so far] 消息 + 最后 6 条
- `agent.loop_guard.context_tokens = 0`，下轮重新测量

### 10.5 项目背景注入（BACKGROUND.md）

**文件约定：** `{working_dir}/BACKGROUND.md`，用户手动维护，纯 Markdown，存在即读，不存在则跳过。

**加载流程：**
```python
def _load_background(self, agent: Agent) -> str:
    working_dir = (agent.settings or {}).get("working_dir", "")
    bg_path = Path(working_dir) / "BACKGROUND.md"
    return bg_path.read_text(encoding="utf-8") if bg_path.is_file() else ""
```

注入在 ActorPromptBuilder 的 soul 之后、resources 之前。ObserverPromptBuilder 不注入。

> **TODO（未来优化）**：引入 auto-dream 机制，专门的 sub-agent 定期整理项目背景，结合 Blackboard 内容自动更新 BACKGROUND.md。

---

## 11. Blackboard 发布订阅系统

### 11.1 存储结构

```
/data/blackboard/{session_id}/{topic}.jsonl
```

每个 topic 一个文件，追加写、不可变。每条 entry：`id / session_id / topic / publisher_id / content / created_at`。

游标为纯内存态：`_cursors: dict[(agent_id, session_id, topic) → int]`，进程重启后丢失（已知限制）。

### 11.2 当前实际产生的 Topic

| Topic | 内容 |
|-------|------|
| `{task.id}` | 每个完成子任务的 `task.result`（父任务 resume 时消费） |
| `_root` | 所有 Actor 的 llm_text 和 tool call 记录（当前无人消费） |

### 11.3 发布（Publish）

触发点：`AgentLoop.run()` 末尾，task 状态确认为 FINISHED 后同步写入：
```python
bb_svc.publish(session_id, task.id, agent_id, task.result)      # 任务结果
for result_turn in result.conversation_turns:
    bb_svc.publish(session_id, "_root", ...)                     # Actor 流水
```

task.id 这条**只在 task.status == "FINISHED" 时写入**，FAILED 或 SUSPENDED 不写。

### 11.4 拉取（Pull）

触发点：`Reasoner._fetch_base()` 每次构建 ReasoningContext 时：
```python
for child in task_svc.list_children(task.id, session.id):
    for entry in bb_svc.pull(session_id, child.id, agent_id):
        bb_snippets.append(entry.content)
```

游标机制：`cursor = _cursors.get(key, 0)`，`read_since` 为全量读后切片（无真正增量 IO）。

### 11.5 拓扑特征

- **严格父→子单向**：只有父任务能 pull 子任务结果，无横向（sibling）通信
- **时序保证**：子任务全部 terminal 后父任务 resume，父任务下一轮 Reasoner 才能 pull 到结果
- **幂等性**：游标机制保证同一批结果不被重复注入（但游标不持久化）
- **孙任务不可见**：需子任务在 task.result 里汇总后向上传递

### 11.6 保留 Topic（设计意图，部分尚未实现）

| Topic | 用途 |
|-------|------|
| `_root` | Session root 规划内容（当前有写无读） |
| `_digest` | Session 跨 topic 整合摘要（未来整合机制） |
| `_lifecycle` | Lifecycle Manager agent 状态变更（只读） |

---

## 12. Agent 协作模型（动态多层树）

### 12.1 树形结构

miniAgents 的 Agent 关系形成一棵**动态多层树**。「root」和「sub」不是固定角色，而是相对关系——同一 agent 可以同时是上级的 sub-agent（执行被分配的 Task）和下级的 root-agent（管理自己派生的 sub-agent）。

| 角色 | 定义 |
|------|------|
| Session Root Agent | session 创建时指定的入口 agent（spawn_depth=0），整棵树的根节点 |
| 局部 Root Agent | 通过 spawn 晋升的 agent，在自己的子树中承担规划职责 |
| 叶子 Agent | 没有派生任何 sub-agent 的 agent，只执行 Task，完成即退出 |
| spawn_depth | 层级深度；session root = 0，每晋升一层 +1 |
| has_spawn_permission | agent 模板配置项；false 时 ToolGateway 直接拒绝 spawn |

### 12.2 Spawn 全流程

```
1. agent 调用 submit_plan / submit_task（control_tools）
2. 父 task → SUSPENDED，actor_done = True
3. TM 创建子 task（PENDING），dag_deps 建链
4. TASK_CREATED 事件 → TaskQueue.push
5. TM._dispatch_next → LM.prepare_executor
6. _check_spawn_permission 检查（深度/并发/token）
7. 通过 → _instantiate_sub_agent（parent_id=base_executor，spawn_depth+1）
8. LM.run_agent(sub_agent_id, child_task_id)
9. 子 task 全部 terminal → TM._try_resume_parent
10. 父 task SUSPENDED → PENDING → 入栈 → ACTIVE
11. Agent（WAITING → RUNNING）在 resume 路径读取子任务结果继续执行
```

### 12.3 深度约束

不设硬性 max_depth——深度本身不是问题，资源消耗才是：
- `token_budget`（硬）：树越深，总 token 消耗越快
- `max_concurrent_agents`（硬）：每个 WAITING agent 占一个槽，max=4 时理论最大树深=4
- `token_budget × 80% 阈值`：余量不足时 spawn 被拒绝
- `max_spawn_tasks`（模板）：控制每层宽度膨胀

---

## 13. Human-in-the-Loop（HITL）

### 13.1 触发场景

- **用户输入请求**：Agent 调用 `request_human_input(prompt, context)` 主动暂停
- **任务完成确认**：Observer 裁决 `needs_user_input`，系统无法自动判定任务完成状态
- **失败阈值暂停**：session.failure_counter >= failure_threshold（Phase 2 实现）

### 13.2 HITL 流程（当前实现）

```
1. Agent 调用 request_human_input 或 Observer 裁决 needs_user_input
2. Session.transition → WAITING_INPUT
3. SSE 推送 waiting_input 事件（含 prompt）给前端
4. hitl_store.wait() 阻塞当前 agent 线程（无超时，持续等待）
5. 用户通过 POST /sessions/{id}/input 提交应答
6. hitl_store 解除阻塞，将应答内容作为 ToolResult 返回给 agent
7. Session.transition → RUNNING，agent 继续执行
```

**needs_user_input 路径（任务完成确认）：**
- Agent 调用 `submit_task_assessment(needs_user_input, ...)`
- Session → WAITING_INPUT，SSE 推送完成确认提示
- 用户应答"已完成" → task.finish()；应答"未完成" → task.fail()

### 13.3 HITL API

| 端点 | 说明 |
|------|------|
| POST /sessions/{id}/input | 提交用户应答，解除 WAITING_INPUT 阻塞 |
| POST /sessions/{id}/messages | 续话（用户发送新消息，session 从 SUCCEEDED/FAILED 恢复） |

> **Phase 2 扩展：** 正式的 HITL 审批 API（GET /hitl/pending, POST /hitl/{id}/approve|reject|modify）、独立的 HitlApproval 实体、超时自动取消、工具风险双阶段审批，留待后续实现。

---

## 14. Memory 管理

### 14.1 Memory 三层架构

| 层次 | 存储 | 内容 | 检索方式 |
|------|------|------|----------|
| **短期上下文** | messages.jsonl（Redis 缓存） | 最近 N 轮消息（window=20） | 顺序读取 |
| **长期记忆** | PostgreSQL + pgvector | 压缩摘要、关键 facts | 语义检索（cosine）+ 时间过滤 |
| **滚动摘要** | messages.jsonl 重写 | 每次 compact 后的 [Context so far] | 直接作为最早消息 |

### 14.2 Memory 写入流程

- `append_message`：追加到 messages.jsonl，累加 token_count 到 session.token_used
- **阈值检测**：messages 数量 >= summary_threshold（默认 20）或 context_tokens >= 80% 时触发 compact
- **extract_facts**（可选）：从 Task 结果中提取关键 facts，生成 embedding，写入 memories（type=fact）
- **冲突处理**：同类 fact 冲突时保留多版本（打时间戳），由上层 LLM 在拼装时自行处理

### 14.3 Metadata Filler 与 Session Goal

**update_task_metadata 工具** 用于更新 task 元数据和 session goal：

```python
update_task_metadata(
    title="...",
    description="...",
    session_goal="对整个 session 整体目的的理解（≤60字）"
)
```

**Session Goal 更新规则：**
- 首次（session.goal == session.user_prompt）：metadata_filler agent 从当前指令和历史中提炼目的，写入 session_goal
- 续话（session.goal != session.user_prompt）：仅在用户方向根本性转变时才填新值，否则留空

metadata_filler daemon task 设置 `inherit_memory=True`，继承 root agent 的历史记忆，从而能理解对话上下文。

---

## 15. LLM 适配层

屏蔽不同 LLM 供应商 API 差异，向上层提供统一接口；支持运行时动态注册与切换。

### 15.1 架构

```
Agent.call_llm(LLMRequest) → LLMClient（统一门面）→ BaseAdapter → Transport（httpx）
```

- **OpenAIAdapter**：system_prompt 插入为 system role 消息
- **AnthropicAdapter**：system_prompt 使用独立 system 字段，max_tokens 默认 4096
- **MockAdapter**：测试/离线用途

### 15.2 供应商兼容性

| 能力 | OpenAI | Anthropic |
|------|--------|-----------|
| system_prompt | 插入为 system role 消息 | 使用独立 system 字段 |
| Tool Calling | tool_calls + tool role | tool_use + tool_result |
| max_tokens | 可选 | 必填（适配层默认 4096） |
| 流式输出 | 支持（待接入） | 支持（待接入） |

---

## 16. REST API 设计

### 16.1 通用约定

- **Base URL**：/api/v1
- **鉴权**：Authorization: Bearer \<token\>
- **幂等**：写接口支持 Idempotency-Key（推荐 UUID）
- **追踪**：X-Request-Id 透传到日志/trace
- **时间格式**：统一 ISO-8601 UTC
- **分页**：默认 page=1、page_size=20、page_size<=100
- **统一返回**：`{ "code": "OK", "message": "success", "data": {}, "request_id": "req_01H..." }`

### 16.2 核心接口

| 端点 | 说明 |
|------|------|
| **Session** | |
| POST /sessions | 202 ses_id（异步启动） |
| GET /sessions | 会话列表 |
| GET /sessions/{id} | session 详情 |
| DELETE /sessions/{id} | 删除 session 及其所有数据 |
| POST /sessions/{id}/cancel | 取消 session |
| POST /sessions/{id}/messages | 续话（用户发送新消息） |
| POST /sessions/{id}/input | 提交 HITL 应答（解除 WAITING_INPUT） |
| GET /sessions/{id}/stream | SSE 推送（事件实时订阅） |
| GET /sessions/{id}/tasks | 查询 session 下任务列表 |
| **Task** | |
| GET /tasks/{id} | task 详情 |
| **Memory** | |
| GET /memories | 查询 agent 消息历史 |
| **Agent Template** | |
| GET /agent-templates | 模板列表 |
| **LLM Provider** | |
| POST /llms | 注册供应商 |
| GET /llms | 已注册供应商列表 |
| DELETE /llms/{name} | 移除供应商 |
| **MCP Server** | |
| POST /mcp-servers | 注册 MCP server |
| GET /mcp-servers | 已注册 MCP server 列表 |
| DELETE /mcp-servers/{name} | 移除 MCP server |
| **Tools** | |
| GET /tools | 列出所有已注册工具（builtin + control） |
| **Remote Skill Sources** | |
| POST /remote-skill-sources | 注册远程 Skill 源 |
| GET /remote-skill-sources | 列表 |
| DELETE /remote-skill-sources/{name} | 移除 |

> **Phase 2 扩展接口（待实现）：** GET /hitl/pending, POST /hitl/{id}/approve|reject|modify, GET /sessions/{id}/tool-calls, PATCH /sessions/{id}/guard-config 等

---

## 17. 数据库 Schema 汇总

> **Phase 1 说明：** 当前实现使用 **文件存储**（JSON/JSONL），按 `{data_dir}/{entity_type}/{id}.json` 组织；内存和摘要存储在 `{data_dir}/memories/{agent_id}/messages.jsonl` 和 `summaries.json` 中。本节描述 **Phase 2 PostgreSQL** 目标 schema，供后续迁移参考。

所有表含 `created_at`（TIMESTAMPTZ NOT NULL DEFAULT now()）和 `updated_at`（TIMESTAMPTZ）。

| 表名 | 核心字段 |
|------|----------|
| **sessions** | id, user_prompt, goal, status, template_id, root_agent_id, token_budget, token_used, root_max_turns, failure_counter, failure_threshold, metadata |
| **tasks** | id, session_id, creator_agent_id, assigned_agent_id, parent_task_id, status, dag_deps, user_prompt, title, description, settings, result, outputs, error, retry_count |
| **agents** | id, session_id, template_id, name, status, soul_md, role_md, act_tool_list, observe_tool_list, mcp_act_servers, mcp_observe_servers, skill_list, loop_guard, has_spawn_permission, spawn_depth, inherit_memory, llm_provider, llm_model, settings |
| **agent_templates** | id, name, version, description, act_tool_list, observe_tool_list, mcp_act_servers, mcp_observe_servers, has_spawn_permission, source_dir |
| **session_events** | id, session_id, event_type, payload, occurred_at |
| **messages** | id, agent_id, session_id, task_id, role, content, tool_call_id, tool_calls, created_at |
| **memories** | id, agent_id, session_id, type（summary/fact）, content, embedding（vector 1536）, created_at |
| **blackboard_entries** | id, session_id, topic, publisher_id, content, created_at |
| **tool_calls** | id, session_id, task_id, agent_id, tool_name, status, arguments, result, error, started_at, finished_at |
| **llm_providers** | id, name, style（openai/anthropic）, base_url, model, timeout_sec |
| **hitl_approvals** | id, session_id, task_id, trigger_reason, task_snapshot, status, modified_inputs, expire_at（Phase 2） |

---

## 18. 事件类型全集

EventBus 为内部异步通信总线，以下为当前已实现的事件类型：

| 事件类型 | 触发时机 |
|----------|----------|
| **Session 事件** | |
| SESSION_CREATED | 会话创建成功 |
| SESSION_STARTED | Session 从 QUEUED 进入 RUNNING |
| SESSION_SUCCEEDED | Session 正常结束 |
| SESSION_FAILED | Session 失败 |
| SESSION_CANCELED | Session 被取消 |
| TOKEN_BUDGET_EXCEEDED | token_used >= token_budget，session 终止 |
| **Task 事件** | |
| TASK_CREATED | task 创建，推入 TaskQueue |
| TASK_STARTED | task 进入 ACTIVE |
| TASK_FINISHED | task 完成（FINISHED） |
| TASK_FAILED | task 失败（FAILED） |
| TASK_EXECUTION_FINISHED | AgentLoop 执行完成，TM 驱动下一调度 |
| TASK_EXECUTION_FAILED | AgentLoop 执行失败，TM 驱动重试或失败处理 |
| **Agent 事件** | |
| AGENT_STARTED | agent 开始执行 |
| AGENT_WAITING | agent 调用 spawn_agents 进入 WAITING |
| AGENT_RESUME | 子任务全部完成，agent 被唤醒继续执行 |
| SPAWN_APPROVED | LM 批准 spawn sub-agent |
| SPAWN_REJECTED | LM 检查 spawn 权限/并发/token 不足，降级 inline |
| **Lifecycle Manager 内部事件** | |
| LIFECYCLE_TASK_READY | DAG 依赖满足，task 进入调度队列 |
| LIFECYCLE_AGENT_SCHEDULED | agent 被分配任务，启动线程 |
| LIFECYCLE_AGENT_RECYCLED | sub-agent 完成后被回收 |
| **Tool 事件** | |
| TOOL_CALL_STARTED | tool 调用开始 |
| TOOL_CALL_FINISHED | tool 调用成功完成 |
| TOOL_CALL_FAILED | tool 调用失败 |
| **Guard 事件** | |
| TOKEN_BUDGET_EXCEEDED | token 预算耗尽 |
| MAX_TURNS_EXCEEDED | root_max_turns 超限 |

> **SSE 推送事件**（前端可见，通过 sse_bus 独立推送）：`llm_prompt`, `text_delta`, `reasoning_delta`, `tool_call`, `text_done`, `session_update`, `task_created`, `task_updated`, `waiting_input`, `session_goal_updated`, `message`, `history`, `init`, `done`

---

## 19. 可观测性与非功能设计

### 19.1 可观测性

**指标（Prometheus）：**
- api.qps、api.p95_latency、api.5xx_rate
- worker.queue_depth、worker.success_rate、worker.retry_rate、worker.timeout_rate
- tool.call_count、tool.error_rate、tool.latency_p95
- llm.prompt_tokens、llm.completion_tokens、llm.cost_per_session
- agent.spawn_count、agent.waiting_gauge、compact.trigger_count

**日志（结构化 JSON）：** 最小字段集 `timestamp / level / request_id / task_id / session_id / event / error_code`

**链路追踪（OpenTelemetry）：** API → SM → TM → LM → AgentLoop → Tool Gateway 全链路 trace；spawn 树以 parent_span_id 关联

### 19.2 初始 SLO

- 会话创建成功率 >= 99.9%
- API p95 延迟 < 300ms（不含长轮询/SSE）
- 异步任务最终完成率 >= 99%

### 19.3 安全

- 鉴权：JWT/API Key（服务间建议 mTLS）
- 鉴权后细粒度授权（RBAC/ABAC）
- 输入校验：JSON Schema + 长度限制 + 字符过滤
- 防注入：命令、SQL、模板注入防护
- 敏感信息脱敏：tool_calls 输入输出强制脱敏；api_key 加密存储（KMS）
- tool_calls 审计记录不可删除，保留 90 天

### 19.4 高可用与性能

- API/Worker 无状态，支持水平扩展
- Redis/PostgreSQL 主从与备份恢复
- LM 状态持久化（lifecycle_manager_state 表），崩溃重启后可从 DB 恢复
- **关键优化**：messages 查询索引化（session_id, agent_id, created_at）；热 session agent prompt 缓存（Redis）；批量写事件日志；blackboard_entries embedding 索引（ivfflat）

---

## 20. 技术选型

| 层次 | 选型 | 备注 |
|------|------|------|
| **语言** | Python 3.11+ | 主栈 |
| **API 框架** | FastAPI + Uvicorn | WebSocket + SSE 原生支持 |
| **ORM** | SQLAlchemy 2.0 + Alembic | async 模式；Alembic 管理迁移 |
| **数据库** | PostgreSQL 16 + pgvector | pgvector 用于 embedding 语义检索 |
| **缓存/队列** | Redis 7 | 短期上下文缓存、分布式锁；TaskQueue 为内存态（per-session） |
| **HTTP 客户端** | httpx（async） | LLM 适配层 Transport |
| **Observability** | OpenTelemetry + Prometheus + Grafana | 链路追踪 + 指标 + 可视化 |
| **对象存储（可选）** | MinIO / S3 兼容 | 大文本 Artifact 存储 |
| **容器化** | Docker + Docker Compose | V1 单机部署；后续 K8s 扩展 |

---

## 21. 核心设计原则

**清晰优先于完备**：V1 先跑通核心 Loop，Guard 机制先于 Observability 落地。

**职责三层分离**：SM 只创建资源；TM 只做 task 层决策；LM 只管 agent tree。三者通过明确接口通信，不越界。

**事件驱动调度**：TASK_EXECUTION_FINISHED/FAILED 驱动全部 task 调度决策，无轮询。

**深度优先执行**：LIFO TaskQueue 使子任务优先于兄弟任务执行，自然产生深度优先顺序。

**安全内置而非外挂**：token_budget / concurrent 限制在 Loop 最内层检查，不可绕过；Tool Gateway 双阶段授权。

**协议对齐降低接入成本**：Tool 走 MCP 协议；Skill 走 SKILL.md 形式；LLM 走统一 Adapter。

**状态可恢复**：所有 task/session 状态持久化到 PostgreSQL；LM 状态持久化到 DB；崩溃后可断点续跑。

**Human-in-the-Loop 是一等公民**：V1 就内置，而不是后续补丁；三种触发场景统一处理。

**Blackboard 与 agent 实例解耦**：agent 退出后 topic 数据持续有效，所有权在 topic 而不在 agent。

**动态树深度自然约束**：WAITING agent 占槽 + token_budget 双重机制，无需 max_depth 参数。

---

> **已知限制与未来扩展点：**
>
> - Blackboard 游标不持久化（进程重启后归零），未来需落地到 DB
> - _root topic 有写无读，未来可作为全局执行流水使用
> - TaskQueue 为单路 LIFO，并行调度需改造（pop_batch + 并发 dispatch）
> - Session 级 Blackboard 整合（_digest topic）设计完备但尚未实现
> - auto-dream 机制（自动整理项目背景的 sub-agent）待规划
> - 实时multi-agent 协调机制扩展：当前session只有一个root-agent长期存在，后续可扩展root agent list，实现实时的multi-agent协作机制

---

*miniAgents Design Document · 2026-04-28*
