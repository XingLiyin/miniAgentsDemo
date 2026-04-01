# miniAgents 后端服务完整设计文档

**v1.1 Final** · 2026-03-30

---

## 文档涵盖范围

- 系统目标与架构分层（第1-2章）
- 核心领域模型：Session、Task、Agent、Blackboard、Tool（第3章）
- Agent 定义文件系统：SOUL.md / ROLE.md / TOOLS.md / STYLE.md（第3A章）
- Agent Loop 与安全防护 Guard 机制（第4章）
- Agent 协作模型：动态多层树 + spawn_agents 工具（第5章）
- Blackboard 发布订阅系统 + Session 整合机制（第6章）
- Lifecycle Manager：独立调度与回收组件（第7章）
- Human-in-the-Loop：Task 级审批闭环（第8章）
- Memory 管理：三层架构 + pgvector 语义检索（第9章）
- Tool 与 Skill 系统：MCP 协议 + Anthropic Skill 形式（第10章）
- LLM 适配层：多供应商统一接口（第11章）
- REST API 设计草案（第12章）
- 数据库 Schema（第13章）
- 可观测性与非功能设计（第14章）
- 技术选型（第15章）

> **v1.1 相对 v1.0 的变更**：新增第 3A 章（Agent 定义文件系统）；`AgentTemplate` 的 `system_prompt` 字段拆分为四个 MD 内容字段；`tool_list` 改为实例化时懒加载（LLM 从 `tools_md` 提取）；第 9 章上下文拼装补充 system prompt 来源说明；第 13 章 Schema 同步更新。

---

# 1. 项目目标与范围

miniAgents 是一个 **Agent 后端服务**，以学习验证架构思路为首要目标，同时构建稳定、可扩展、可观测的基础设施 Demo。核心理念是：先跑通最小可运行的 Agent Loop，再逐步迭代安全防护与可观测性。

## 1.1 核心能力

- **Agent 级 Memory 管理**：短期消息窗口 + 长期事实摘要 + pgvector 语义检索
- **Session 级调度管理**：Task 栈 + DAG 依赖 + 并发控制 + 完整状态机
- **动态多层 Agent 树**：任意 agent 可通过 `spawn_agents` 晋升为局部 root，形成运行时树形结构
- **Blackboard 发布订阅**：topic 主键、与 agent 实例解耦、Session 事件触发整合
- **内置工具执行**：`bash_exec`、`http_request`，严格遵循 MCP 协议
- **Human-in-the-Loop**：Task 级执行前人工确认，支持 approve / reject / modify
- **Loop 安全防护**：token_budget 硬终止 + failure_threshold 软暂停 + 并发数量上限

## 1.2 V1 范围约束

- 单租户优先，后续平滑扩展为多租户
- Python + FastAPI 主栈，PostgreSQL（pgvector）+ Redis Streams
- 先跑通核心 Loop，Guard 机制先于 Observability 落地
- sub-agent 流式回收：完成即退出，root-agent 无需感知细节（由 Lifecycle Manager 处理）
- 不支持 agent 间实时流式协作（V2 可考虑 streaming entry）

---

# 2. 总体架构

## 2.1 架构分层

| 层次 | 职责 |
|---|---|
| **API 层** | REST/WebSocket 接口；鉴权（JWT/API Key）、参数校验、限流；Idempotency-Key 幂等；X-Request-Id 全链路透传 |
| **Orchestrator 层** | Session 状态机；Task 栈 + DAG 调度；并发上限（max_concurrent_tasks / max_concurrent_agents）；HITL 暂停点；Lifecycle Manager |
| **Worker Runtime 层** | Agent Loop（Observe→Plan→CreateTask→Schedule→Execute→UpdateMemory）；token_budget 硬检查；failure_counter；Skill Router |
| **LLM 适配层** | 统一 LLMClient 门面；OpenAIAdapter / AnthropicAdapter；Tool Calling 格式归一；token 用量统计 |
| **Tool Gateway 层** | MCP tools/list + tools/call；policy_engine 按（user_role, task_type, tool_name）授权；bash/http 安全沙盒；调用全量审计脱敏 |
| **存储层** | PostgreSQL（事务数据 + pgvector 向量索引）；Redis（短期缓存、分布式锁、Redis Streams 队列）；对象存储（可选，大文本 Artifact）|
| **Observability 层** | OpenTelemetry 全链路 Trace；Prometheus 指标（qps/latency/token_cost/tool_error_rate）；结构化 JSON 日志；Event Bus（Redis Streams）|

## 2.2 逻辑组件

| 组件 | 职责简述 |
|---|---|
| **Session Manager** | 创建/恢复/终止会话；维护状态机与超时；触发 SESSION_* 事件 |
| **Task Manager** | 入栈出栈 + DAG 边维护；优先级队列；幂等提交（Idempotency-Key）|
| **Agent Registry** | 管理 Agent 模板与运行实例；soul_md / role_md / tools_md / style_md / tool_list / has_spawn_permission |
| **Agent Runtime** | 驱动 Agent Loop；持有 Guard（token_budget、max_turns、failure_counter）|
| **Lifecycle Manager** | 监听 AGENT_FINISHED / TASK_FAILED / SPAWN_REQUESTED 事件；回收 sub-agent；操作 task_stack；维护并发计数器；处理 SUSPENDED/WAITING 恢复 |
| **HITL Manager** | 管理待确认 Task 队列；提供 approve/reject/modify API；超时自动取消 |
| **Memory Service** | 消息写入；滚动摘要；pgvector 语义检索；Token 预算拼装 |
| **Blackboard Service** | 管理 topic 注册；publish/subscribe/pull 接口；触发整合阈值检查 |
| **Tool Gateway** | MCP 协议执行；policy_engine 授权；bash/http 沙盒；调用脱敏审计 |
| **Skill Router** | description 匹配 + 显式触发；按需加载 SKILL.md；生成执行计划 |
| **LLM Registry** | 供应商注册/切换；运行时动态注册（/api/v1/llms）|
| **Event Bus** | Redis Streams 发布订阅；SESSION_* / TASK_* / AGENT_* / HITL_* / BLACKBOARD_* 事件 |
| **Artifact Store** | 大文本/文件产物存储；uri 引用；与 Blackboard entry 联动 |

---

# 3. 核心领域模型

## 3.1 Session

一次目标导向的 LLM 会话容器，持有完整的调度上下文和 Guard 参数。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | ses_ULID |
| `goal` | TEXT | 会话目标，注入 session root agent 的 system prompt |
| `status` | VARCHAR | QUEUED → RUNNING → SUCCEEDED / FAILED / TIMEOUT / CANCELED / RETRYING / PAUSED_HITL |
| `root_agent_id` | VARCHAR FK | 会话入口 Agent（spawn_depth=0）|
| `token_budget` | INTEGER DEFAULT 200000 | 全局 token 上限（硬限制），所有 agent 共享 |
| `token_used` | INTEGER DEFAULT 0 | 已消耗 token，实时累加 |
| `max_concurrent_tasks` | INTEGER DEFAULT 8 | 同时 ACTIVE 的 Task 上限（SUSPENDED 不计入）|
| `max_concurrent_agents` | INTEGER DEFAULT 4 | 同时存活 Agent 上限（含 WAITING）|
| `failure_counter` | INTEGER DEFAULT 0 | 连续失败次数；任意 Task FAILED +1，成功归零 |
| `failure_threshold` | INTEGER DEFAULT 3 | 连续失败软暂停阈值 |
| `consolidation_threshold` | INTEGER DEFAULT 15 | Blackboard topic 条目数超过此值触发整合 |
| `digest_token_budget` | INTEGER DEFAULT 20000 | 预留给 Blackboard 整合的 token 配额 |
| `root_max_turns` | INTEGER DEFAULT 20 | Session root agent 的 Loop 轮次上限 |
| `sub_max_turns` | INTEGER DEFAULT 10 | 子 agent 的 Loop 轮次上限 |
| `runtime_summary` | JSONB | 执行统计：重试次数、token 成本、工具调用统计 |
| `created_at / updated_at` | TIMESTAMPTZ | UTC ISO-8601 |

> **Session 状态机说明**
> - PAUSED_HITL：failure_counter >= failure_threshold 时自动进入，等待人工介入后 resume
> - RETRYING 从断点恢复，不重置 token_used（防止通过重试绕过 token_budget）
> - token_budget 耗尽 → 立即 FAILED，error_code = TOKEN_BUDGET_EXCEEDED

## 3.2 Task

会话内的最小工作单元，具有完整的状态机、HITL 支持和派生关系追踪。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | tsk_ULID |
| `session_id` | VARCHAR FK | 所属会话 |
| `type` | ENUM | reasoning / tool-call / sub-agent / skill / hitl |
| `assigned_agent_id` | VARCHAR FK | 执行此 Task 的 Agent |
| `creator_agent_id` | VARCHAR FK | 创建此 Task 的 Agent（通常是 root 或局部 root）|
| `parent_task_id` | VARCHAR | 派生关系：此 Task 是哪个 SUSPENDED Task 的子任务 |
| `spawned_by_tool_call_id` | VARCHAR | 创建此 Task 的 spawn_agents 调用记录 id |
| `status` | ENUM | PENDING → PENDING_APPROVAL → ACTIVE → SUSPENDED → ACTIVE（恢复）→ FINISHED / FAILED / CANCELED / REJECTED |
| `requires_approval` | BOOLEAN DEFAULT false | true 时执行前必须经过 HITL Manager 确认 |
| `approval_id` | VARCHAR | 关联的 HitlApproval 记录 id |
| `output_topic` | VARCHAR | 派生 agent 产出应写入的 Blackboard topic 名称 |
| `inputs / outputs / result` | JSONB | 结构化输入输出，可引用 Artifact uri |
| `retry_count / max_retries` | INTEGER | 当前重试次数 / 上限（默认 3）|
| `timeout_ms` | INTEGER DEFAULT 60000 | 执行超时 |
| `compensation` | JSONB | 有副作用工具调用的回滚动作 |
| `priority` | INTEGER DEFAULT 5 | 任务优先级（0-10，越大越优先）|
| `error / error_code` | TEXT / VARCHAR | 失败时的错误信息和错误码 |

## 3.3 Agent

上下文管理容器，角色为相对概念（root/sub），由 spawn_depth 和 parent_agent_id 描述树形关系。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | agt_ULID |
| `template_id` | VARCHAR FK | 关联的 AgentTemplate |
| `session_id` | VARCHAR FK | 所属会话 |
| `spawn_depth` | INTEGER DEFAULT 0 | 在 agent 树中的层级深度，session root = 0 |
| `parent_agent_id` | VARCHAR | 上级 agent id；session root 为 NULL |
| `active_task_id` | VARCHAR FK | 当前处理的 Task |
| `spawned_task_ids` | JSONB DEFAULT [] | 通过 spawn_agents 创建的子 Task id 列表 |
| `resume_hint` | TEXT | spawn_agents 时传入的恢复提示，恢复时注入 Observe 阶段 |
| `status` | ENUM | IDLE → RUNNING → WAITING → RUNNING（恢复）→ FINISHED / FAILED |
| `loop_guard` | JSONB | { max_turns, turns_used }，WAITING 期间 turns_used 不增加 |
| `subscribed_topics` | JSONB DEFAULT [] | agent 声明的订阅 topic 列表，重建时使用 |
| `model` | VARCHAR | 使用的 LLM 供应商名称（关联 LLMRegistry）|
| `created_at / updated_at` | TIMESTAMPTZ | UTC ISO-8601 |

## 3.4 AgentTemplate

AgentTemplate 存储 agent 的完整定义。身份相关内容（SOUL.md / ROLE.md / TOOLS.md / STYLE.md）由用户在本地以 Markdown 文件维护，通过导入接口写入对应字段。`tool_list` 在 agent 实例化时从 `tools_md` 懒加载提取（见第 3A 章）。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | tpl_ULID |
| `name` | VARCHAR UNIQUE | 模板名称 |
| `description` | TEXT | 一句话描述，从 SOUL.md Frontmatter 提取 |
| `soul_md` | TEXT | SOUL.md 正文内容（Frontmatter 之后的部分）|
| `role_md` | TEXT | ROLE.md 正文内容 |
| `tools_md` | TEXT | TOOLS.md 正文内容（含工具使用指南）|
| `style_md` | TEXT | STYLE.md 正文内容（可选）|
| `tool_list` | JSONB DEFAULT [] | 工具白名单，agent 实例化时从 tools_md 懒加载提取；权限控制的权威来源 |
| `tool_list_ready` | BOOLEAN DEFAULT false | tool_list 是否已完成懒加载提取 |
| `skill_list` | JSONB DEFAULT [] | 可用 Skill 名称列表 |
| `memory_config` | JSONB | { short_window_size, max_summary_tokens, use_vector_search } |
| `inject_style` | BOOLEAN DEFAULT false | 是否将 style_md 注入 system prompt |
| `has_spawn_permission` | BOOLEAN DEFAULT false | 是否允许调用 spawn_agents；false 时 Tool Gateway 直接拒绝 |
| `max_spawn_tasks` | INTEGER DEFAULT 8 | 单次 spawn_agents 最多派生的子 Task 数 |
| `default_model` | VARCHAR | 默认 LLM 供应商名称 |
| `created_at / updated_at` | TIMESTAMPTZ | |

## 3.5 HitlApproval

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | hal_ULID |
| `session_id / task_id` | VARCHAR FK | 关联的 session 和 task |
| `trigger_reason` | ENUM | explicit / failure_threshold / tool_risk |
| `task_snapshot` | JSONB | 待审批 Task 的完整快照（含 inputs）|
| `status` | ENUM | PENDING / APPROVED / REJECTED / MODIFIED / TIMEOUT |
| `decision_by` | VARCHAR | 操作者标识 |
| `modified_inputs` | JSONB | modify 操作时的替换 inputs（null 表示未修改）|
| `decided_at` | TIMESTAMPTZ | 操作时间 |
| `expire_at` | TIMESTAMPTZ | 超时时间（默认 created_at + 30 min）|

---

# 3A. Agent 定义文件系统

Agent 的身份定义以 **Markdown 文件**的形式由用户在本地维护，通过导入接口写入 `agent_templates` 表的对应字段。这四个文件是**创作物（source of truth）**，而不是数据库字段本身。

## 3A.1 四个文件的职责

| 文件 | 定义什么 | 注入时机 |
|---|---|---|
| **SOUL.md** | agent 是谁：人格、价值观、行为风格、边界原则 | 每轮 Loop 始终注入，优先级最高 |
| **ROLE.md** | agent 做什么：职责范围、能力边界、不做什么 | 每轮 Loop 始终注入 |
| **TOOLS.md** | agent 用什么：工具使用指南，什么情况用什么工具 | tool_list 非空时注入；tool_list 本身在实例化时从此文件懒加载提取 |
| **STYLE.md** | agent 怎么输出：输出语言、格式偏好、禁止事项 | `inject_style=true` 时注入，默认不注入 |

**SOUL.md 和 ROLE.md 不需要任何语义理解**，内容直接拼入 system prompt。TOOLS.md 有两个用途：正文作为工具使用指南直接拼入 system prompt；另外需要在首次实例化时由 LLM 语义理解后提取工具名称列表写入 `tool_list`（懒加载）。STYLE.md 同样直接拼入，无需额外处理。

## 3A.2 文件格式规范

每个文件使用 YAML Frontmatter + Markdown 正文的结构。

**SOUL.md 示例**

```yaml
---
name: research-agent
version: 1.2.0
description: 专注于信息收集与分析的研究型 agent
author: team-ai
---

## 核心性格
你是一个追求信息准确性的研究者。你对模糊的结论感到不安，
对未经验证的断言会主动质疑。你不会为了让对方满意而妥协准确性。

## 行为边界
- 不确定时明确说不确定，而不是猜测
- 引用来源比提供结论更重要
- 不做超出信息收集范围的判断
```

**TOOLS.md 示例**

```yaml
---
name: research-agent
version: 1.0.0
tools:
  - http_request
  - bash_exec
---

## 工具使用指南

### http_request
用于抓取网页内容和调用外部 API。
- 优先用于公开数据源，不用于内网地址
- 响应超过 100KB 时只取前 50KB

### bash_exec
用于本地文件处理和数据转换脚本。
- 仅在 /tmp 目录操作，不读写项目文件
- 超时设为 10s，避免长时间阻塞
```

**Frontmatter 必填字段说明**

- `name`：与 AgentTemplate.name 对应，导入时用于 upsert 匹配
- `version`：语义化版本号（semver），写入 `agent_templates` 表，便于变更追踪
- `description`（SOUL.md）：写入 `agent_templates.description`
- `tools`（TOOLS.md，可选）：工具名称列表。若 Frontmatter 中已声明，导入时直接写入 `tool_list`，跳过懒加载；若未声明，留待实例化时 LLM 提取

## 3A.3 导入流程

用户通过 `POST /agent-templates/import` 提交文件，系统按以下步骤处理：

1. **接收文件**：接受四个 MD 文件（至少提供 SOUL.md + ROLE.md）
2. **Frontmatter 解析**：提取 name / version / description 等元数据
3. **正文写入**：将四个文件的 Markdown 正文（Frontmatter 之后的部分）分别写入 `agent_templates` 的 `soul_md` / `role_md` / `tools_md` / `style_md` 字段
4. **tool_list 快速路径**：若 TOOLS.md 的 Frontmatter 中包含 `tools` 字段，直接写入 `tool_list`，并将 `tool_list_ready` 设为 `true`
5. **生成/更新 AgentTemplate**：在 `agent_templates` 表中 upsert 记录（按 name 匹配）
6. **返回结果**：包含 template_id、tool_list（若已提取）、解析警告

> **设计意图**
> - 用户用熟悉的 Markdown 编辑器维护 agent 定义，不需要操作数据库
> - 导入是轻量操作：SOUL.md / ROLE.md / STYLE.md 无需任何语义理解，直接存储正文
> - TOOLS.md 的 Frontmatter `tools` 字段提供了无需 LLM 的快速路径；只有在未声明时才走懒加载

## 3A.4 tool_list 懒加载

当 TOOLS.md 的 Frontmatter 中没有 `tools` 字段时（`tool_list_ready=false`），`tool_list` 在 **agent 首次实例化时**由 LLM 从 `tools_md` 正文中提取：

1. Agent Registry 检测到 `tool_list_ready=false`
2. 调用 LLM，读取 `tools_md` 正文，提取工具名称列表
3. 写入 `agent_templates.tool_list`，将 `tool_list_ready` 设为 `true`
4. 后续所有基于该模板的 agent 实例直接使用缓存的 `tool_list`，不再重复提取

> **懒加载只针对 tool_list**。其余三个字段（soul_md / role_md / style_md）的内容无需语义理解，直接在 Observe 阶段按规则拼入 system prompt，不需要任何额外的加载或解析步骤。

## 3A.5 system prompt 组装顺序

Agent 实例化后，四个文件内容按以下顺序拼装为完整 system prompt（每轮 Loop 固定使用）：

| # | 来源 | 条件 | 说明 |
|---|---|---|---|
| S1 | `soul_md` 正文 | 始终 | agent 的人格与价值观，最高优先级 |
| S2 | `role_md` 正文 | 始终 | 当前角色的职责边界 |
| S3 | `tools_md` 正文 | `tool_list` 非空时 | 工具使用指南；权限控制由 `tool_list` 字段负责，此处只是行为指导 |
| S4 | `style_md` 正文 | `inject_style=true` 时 | 输出格式偏好，默认不注入 |

**注意**：Skill 内容**不在** system prompt 里。Skill 在 Observe 阶段按当前 Task 匹配后作为独立 context block 注入（见第 9 章上下文拼装 C2）。

---

# 4. Agent Loop 与 Guard 机制

## 4.1 root-agent Loop 阶段

| 阶段 | 核心动作 | 关键约束 |
|---|---|---|
| **Observe** | 从 `_digest` 拉取全局摘要；从订阅 topic 拉取增量；检查 token_budget 余量 | 剩余 < 10% 写入 TOKEN_BUDGET_WARNING 事件；首轮拉取全量 |
| **Plan** | 调用 LLM 生成执行计划；决定创建哪些 Task 和 sub-agent 角色；发布规划摘要到 `_root` topic | turns_used += 1；超过 root_max_turns 写警告事件 |
| **CreateTask** | 创建 Task，设置 requires_approval / DAG 依赖 / output_topic；HITL Task 直接进入 PENDING_APPROVAL | spawn_agents 调用在 Execute 阶段，不在此处 |
| **Schedule（委托）** | 将 Task 提交给 Lifecycle Manager；LM 负责实例化 sub-agent 和调度 | root-agent 不直接操作 task_stack |
| **Execute** | 等待「本轮提交的 Task」全部进入 FINISHED / FAILED / REJECTED / SUSPENDED | 不监听 AGENT_FINISHED；Task 状态由 LM 驱动 |
| **UpdateMemory** | 写入消息和产物；检查是否触发滚动摘要；触发 TASK_FINISHED 事件 | 摘要触发阈值：summary_threshold（默认 20 条消息）|

## 4.2 sub-agent Loop 阶段

sub-agent 的 Loop 比 root-agent 轻量，最多运行 `sub_max_turns`（默认 10）轮：

| 阶段 | 动作 |
|---|---|
| **Observe** | 调用 `blackboard.pull(agent_id)` 拉取所有订阅 topic 的增量 Entry；读取 `active_task` 的 inputs；检查 turns_used < max_turns |
| **Plan（可选）** | 若 Task 复杂，生成本轮子计划；简单 Task 直接进入 Execute |
| **Execute** | 执行 Task（reasoning / tool-call / skill）；如需拆解可调用 `spawn_agents` 工具 |
| **Publish** | 将产出 publish 到 `output_topic`（由 Task.output_topic 指定，或 agent 自行决定）|
| **Finish** | Task 完成 → status=FINISHED → 触发 AGENT_FINISHED 事件 → 退出 Loop；Lifecycle Manager 接管回收 |

## 4.3 Guard 机制

**硬限制（超出立即 FAILED，error_code 标注原因）**

- **token_budget**（Session 级）：session.token_used >= session.token_budget → TOKEN_BUDGET_EXCEEDED → session FAILED
- **max_concurrent_tasks**（Session 级）：同时 ACTIVE Task 数上限；超出时 Schedule 阶段阻塞，不报错
- **max_concurrent_agents**（Session 级）：同时存活 Agent 数上限（含 WAITING）；spawn_agents 时 LM 检查，超限返回 REJECTED
- **max_spawn_tasks**（模板级）：单次 spawn_agents 最多派生子 Task 数；超限返回 PLAN_TOO_LARGE
- **token_budget × 80% 阈值**：spawn_agents 时余量不足返回 TOKEN_BUDGET_LOW

**软限制（警告 + 暂停，等待人工介入）**

- **failure_threshold**（Session 级）：连续失败 >= 3 次 → session.status = PAUSED_HITL → 创建 HitlApproval → 等待 resume
- **max_turns（root）**：session root agent 的 Loop 轮次超过 root_max_turns → 写警告事件，请求人工决策
- **max_turns（sub）**：sub-agent turns_used >= sub_max_turns → 当前 Task FAILED → 触发 TASK_FAILED 处理流程
- **工具连续失败**：同一工具连续失败 2 次 → 写警告事件，HITL Manager 推送通知

## 4.4 并发计数规则

| 状态 | 计入 concurrent_tasks | 计入 concurrent_agents |
|---|---|---|
| Task: PENDING / PENDING_APPROVAL | ✗ 否 | — |
| Task: ACTIVE | ✓ 是 | — |
| Task: SUSPENDED（agent 晋升后挂起）| ✗ 否（挂起不占槽）| — |
| Agent: RUNNING | — | ✓ 是 |
| Agent: WAITING（spawn 后等待子任务）| — | ✓ 是（仍占槽，防无限派生）|
| Agent: FINISHED（待回收）| — | ✗ 否 |

---

# 5. Agent 协作模型

## 5.1 动态多层树

miniAgents 的 Agent 关系形成一棵**动态多层树**。「root」和「sub」不是固定角色，而是相对关系——同一 agent 可以同时是上级的 sub-agent（执行被分配的 Task）和下级的 root-agent（管理自己派生的 sub-agent）。树的形状由 agent 的运行时决策决定，不在 session 创建时预先定义。

| 角色 | 定义 |
|---|---|
| **Session Root Agent** | session 创建时指定的入口 agent（spawn_depth=0），是整棵树的根节点 |
| **局部 Root Agent** | 通过 spawn_agents 晋升的 agent，在自己的子树中承担规划职责，对上级仍是 sub-agent |
| **叶子 Agent** | 没有派生任何 sub-agent 的 agent，只执行 Task，完成即退出 |
| **spawn_depth** | 在树中的层级深度；session root = 0，每晋升一层 +1 |
| **has_spawn_permission** | agent 模板配置项；false 时调用 spawn_agents 被 Tool Gateway 直接拒绝 |

## 5.2 职责边界

| 角色 | 核心职责 | 不做什么 |
|---|---|---|
| **root-agent / 局部 root** | Plan（分解目标、创建 Task）；Execute（等待当前轮 Task）；调用 spawn_agents 晋升 | 不监听 AGENT_FINISHED；不直接操作 task_stack；不回收资源 |
| **sub-agent / 叶子 agent** | 执行单一 Task；向 Blackboard 发布产出；完成即退出 | 不创建新 Task（除非调用 spawn_agents）；不感知全局状态 |
| **Lifecycle Manager** | 监听 AGENT_FINISHED / TASK_FAILED / SPAWN_REQUESTED；回收资源；操作 task_stack；维护并发计数器 | 不调用 LLM；不执行 Task；不写 Blackboard 业务内容 |
| **Session** | 拥有 Blackboard；事件触发时做跨 topic 关联整合与摘要 | 不执行 Task；整合时调用 LLM 但计入 digest_token_budget |

## 5.3 spawn_agents 工具

agent 通过调用 `spawn_agents` 工具主动发起晋升申请：

**inputSchema**

| 字段 | 必填 | 说明 |
|---|---|---|
| `reason` | 是 | 申请拆解的原因，供 LM 审批判断，写入审计日志 |
| `plan` | 是 | 子任务列表，每项含 title / description / output_topic / requires_approval / depends_on |
| `plan[].depends_on` | 否 | 依赖的子任务序号（0-based），用于构建子 DAG |
| `resume_hint` | 否 | 所有派生任务完成后，agent 恢复时注入 Observe 阶段的上下文提示 |

**返回值**

| 字段 | 说明 |
|---|---|
| `status: APPROVED` | 晋升成功；当前 Task → SUSPENDED；agent → WAITING；子 Task 已创建并入队 |
| `status: REJECTED + reject_reason` | NO_PERMISSION / CONCURRENT_LIMIT / TOKEN_BUDGET_LOW / PLAN_TOO_LARGE |
| `spawned_task_ids` | APPROVED 时返回，已创建的子 Task id 列表 |

> **为什么用工具调用而不是 Plan 阶段输出**
> - 工具调用走 Tool Gateway：权限检查、审计、HITL 确认逻辑统一复用
> - REJECTED 对 agent 透明：同一轮 Loop 内即可降级处理，不需要等待下一轮
> - 审计可追溯：spawn_agents 调用记录进入 tool_calls 表，含 reason 字段

## 5.4 晋升流程

1. **agent 调用 spawn_agents**：在 Execute 阶段通过 Tool Gateway 调用
2. **Tool Gateway 权限检查**：验证 `agent.has_spawn_permission=true`；否则返回 REJECTED(NO_PERMISSION)
3. **Lifecycle Manager 审批**：检查并发余量 / token 余量 / plan 大小；任一不满足返回 REJECTED
4. **Task → SUSPENDED，Agent → WAITING**：SUSPENDED Task 从 concurrent_tasks 移除；WAITING agent 仍占 concurrent_agents 槽
5. **创建子 Task + 子 DAG**：按 plan 构建依赖边，parent_task_id 指向 SUSPENDED Task
6. **调度第一批子 Task**：无依赖的子 Task 入队，实例化 sub-agent（spawn_depth+1）
7. **子 Task 全部完成 → AGENT_RESUME**：LM 触发 AGENT_RESUME；SUSPENDED → ACTIVE；agent → RUNNING
8. **Agent 恢复**：Observe 阶段注入 resume_hint + 子 Task output_topic 的 `_digest` 内容；继续原 Task

> **子 Task 全部失败的兜底**
> - 所有派生 Task FAILED 时：LM 触发 SPAWN_ALL_FAILED 事件
> - SUSPENDED Task 转为 FAILED；WAITING agent 转为 FAILED；failure_counter += 1
> - 不会出现 Task 永久挂起的情况

## 5.5 深度约束（为什么不设 max_depth）

不设硬性 max_depth——深度本身不是问题，资源消耗才是。以下机制协同自然约束树深度：

- **token_budget（硬）**：树越深，总 token 消耗越快；耗尽时整个 session 终止
- **max_concurrent_agents（硬）**：每个 WAITING agent 占一个槽；max_concurrent_agents=4 时，理论最大树深=4
- **token_budget × 80% 阈值**：余量不足时 spawn_agents 被拒绝，阻止深层继续派生
- **max_spawn_tasks（模板）**：控制每层的宽度膨胀

---

# 6. Blackboard 发布订阅系统

## 6.1 设计原则

Blackboard 以 **topic** 为主键组织，与 agent 实例解耦。agent 退出后其发布的内容依然有效，所有权在 topic 而不在 agent。

- **topic**：Blackboard 的组织单元，由 agent 自由命名（如 research/findings、code/errors）
- **entry**：发布到某 topic 的一条内容记录，含 content / type / publisher_agent_id / embedding
- **subscription**：agent 声明对某 topic 感兴趣，Observe 阶段拉取增量（基于 last_read_cursor）
- **digest**：Session 整合后生成的摘要条目，发布到 `_digest` topic

## 6.2 保留 Topic（系统自动创建，agent 只读）

| Topic | 用途 |
|---|---|
| `_root` | Session root agent 发布规划内容；所有 sub-agent 默认订阅；spawn 后局部 root 也可发布到此 |
| `_digest` | Session 发布跨 topic 整合摘要；root-agent 在每轮 Observe 开始时读取，获得全局视图 |
| `_lifecycle` | Lifecycle Manager 发布 agent 状态变更（WAITING / FINISHED / RESUME 等）；只读 |

## 6.3 Publish / Subscribe 接口

| 接口 | 说明 |
|---|---|
| `blackboard.publish(topic, type, content, agent_id, task_id?)` | 发布一条 Entry；topic 不存在时自动创建；同步更新 entry_count；触发整合阈值检查 |
| `blackboard.subscribe(topic, agent_id)` | 声明订阅；初始化 last_read_cursor=null（拉取全量）|
| `blackboard.pull(agent_id) → list[Entry]` | 拉取所有订阅 topic 的增量 Entry（cursor 之后），返回后更新 cursor |
| `blackboard.get_topic(topic) → list[Entry]` | 按 topic 全量查询（不依赖订阅，root-agent 临时查阅用）|
| `blackboard.unsubscribe(topic, agent_id)` | sub-agent 退出时由 LM 调用，清理订阅记录 |

## 6.4 Session 整合机制

**触发条件（满足任一即触发）**

- **条目阈值**：任意 topic 的 entry_count >= consolidation_threshold（默认 15）
- **Task 完成**：Lifecycle Manager 发布 TASK_FINISHED 事件后，Session 检查是否需要整合

**整合流程**

1. 读取所有 topic 的未整合 Entry（is_superseded=false）
2. 用 pgvector 计算 Entry 间的 embedding 相似度，聚类内容相近的条目
3. **合并规则**：相似度 >= 0.85 的条目合并为一条；同一 topic 内时间连续的同类条目滚动压缩
4. 调用 LLM 生成跨 topic 关联摘要（消耗 digest_token_budget 配额），发布到 `_digest` topic（type=digest）
5. 被整合的原始条目标记 is_superseded=true，保留原始数据不删除
6. 更新各 topic 的 last_digest_at 和 entry_count

## 6.5 Blackboard Schema

**blackboard_topics**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | top_ULID |
| `session_id` | VARCHAR FK | 所属会话 |
| `name` | VARCHAR | topic 名称，session 内唯一 |
| `is_system` | BOOLEAN | true 表示保留 topic（`_root` / `_digest` / `_lifecycle`）|
| `entry_count` | INTEGER DEFAULT 0 | 当前未归档条目数，用于触发整合 |
| `last_digest_at` | TIMESTAMPTZ | 上次整合时间 |

**blackboard_entries**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | bbe_ULID |
| `topic_id` | VARCHAR FK | 所属 topic |
| `session_id` | VARCHAR FK | 冗余字段，便于查询 |
| `type` | ENUM | note / fact / plan / artifact_ref / digest / error |
| `content` | JSONB | 结构化内容 |
| `publisher_agent_id` | VARCHAR | 发布者 agent id（digest 时为 null）|
| `source_task_id` | VARCHAR | 产出此条目的 Task id（可选）|
| `embedding` | vector(1536) | pgvector 向量，用于整合时相似度计算 |
| `is_superseded` | BOOLEAN DEFAULT false | 被后续摘要覆盖时标记为 true |
| `expired_at` | TIMESTAMPTZ | TTL，null 表示永久 |

**blackboard_subscriptions**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR PK | bbs_ULID |
| `agent_id` | VARCHAR FK | 订阅者 |
| `topic_id` | VARCHAR FK | 订阅的 topic |
| `last_read_cursor` | VARCHAR | 最后读取的 entry id，Observe 阶段从此游标拉取增量 |

---

# 7. Lifecycle Manager

Lifecycle Manager 是从 agent Loop 中剥离出来的独立组件，承担所有 agent 生命周期管理和 task_stack 操作职责。**它不调用 LLM，所有决策基于规则**（DAG 依赖、并发计数、重试策略）。

## 7.1 内部状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | VARCHAR | 所属会话 |
| `concurrent_agents` | INTEGER | 当前存活 sub-agent 数量（含 WAITING）|
| `concurrent_tasks` | INTEGER | 当前 ACTIVE Task 数量（SUSPENDED 不计入）|
| `failure_counter` | INTEGER | 连续失败次数（任意 Task FAILED +1，成功归零）|
| `agent_registry` | dict[agent_id → AgentMeta] | 存活 agent 的元数据（task_id、subscriptions、started_at）|
| `pending_schedule_queue` | list[task_id] | 依赖已满足但并发余量不足的 Task 队列 |

## 7.2 事件处理

**AGENT_FINISHED**

1. 从 agent_registry 移除 agent 元数据；调用 `blackboard.unsubscribe_all(agent_id)`
2. concurrent_agents -= 1；将绑定 Task → FINISHED；concurrent_tasks -= 1
3. 检查 DAG：找出所有依赖已完成的 Task，加入 pending_schedule_queue
4. 检查 SUSPENDED Task：若 spawned_task_ids 全部 FINISHED → 触发 AGENT_RESUME
5. 从 pending_schedule_queue 调度新 Task（若并发余量允许）
6. 通知 Session 检查 Blackboard 整合条件

**TASK_FAILED**

1. failure_counter += 1；检查 >= failure_threshold → session.status = PAUSED_HITL
2. 将失败 Task 的 compensation 动作写入 pending_compensations
3. concurrent_tasks -= 1；concurrent_agents -= 1（若有绑定 agent）
4. 评估重试：retry_count < max_retries → 重新入 pending_schedule_queue；否则 → FAILED

**SPAWN_REQUESTED**

1. 权限检查：has_spawn_permission / concurrent_agents 余量 / token 余量 / plan 大小
2. 全部通过 → 创建子 Task + 子 DAG；Task → SUSPENDED；Agent → WAITING
3. 更新 agent.spawned_task_ids；调度第一批无依赖子 Task；返回 APPROVED
4. 任一不通过 → 返回 REJECTED + reject_reason；agent 在同一轮 Loop 内降级处理

**SPAWN_CHILD_ALL_FINISHED / SPAWN_ALL_FAILED**

- 所有子 Task FINISHED → 触发 AGENT_RESUME：SUSPENDED → ACTIVE；agent → RUNNING；注入 resume_hint + digest
- 所有子 Task FAILED → SPAWN_ALL_FAILED：SUSPENDED Task → FAILED；WAITING Agent → FAILED；failure_counter += 1

---

# 8. Human-in-the-Loop（HITL）

## 8.1 触发场景

- **显式标记**：Task.requires_approval=true（agent 模板或 Plan 中指定）
- **失败阈值**：session.failure_counter >= failure_threshold，系统自动暂停
- **工具风险**：bash_exec / http_request 写操作默认 requires_approval=true（可配置关闭）

## 8.2 HITL 流程

1. Task 进入 PENDING_APPROVAL 状态；HITL Manager 创建 HitlApproval 条目
2. Event Bus 推送 HITL_APPROVAL_REQUIRED 事件（SSE / Webhook）
3. 人工通过 `POST /hitl/{approval_id}/approve|reject|modify` 操作
4. approve → Task 转为 ACTIVE；reject → Task 转为 REJECTED（Loop 跳过）；modify → 更新 inputs 后 approve
5. 超时未操作（默认 30 min）→ Task 自动 CANCELED，写入 HITL_TIMEOUT 事件

## 8.3 HITL API

| 端点 | 说明 |
|---|---|
| `GET /hitl/pending` | 查询当前等待人工确认的 HitlApproval 列表 |
| `GET /hitl/{approval_id}` | 获取单条 HitlApproval 详情（含 task_snapshot）|
| `POST /hitl/{approval_id}/approve` | 审批通过，Task 转为 ACTIVE |
| `POST /hitl/{approval_id}/reject` | 拒绝，Task 转为 REJECTED |
| `POST /hitl/{approval_id}/modify` | 修改 inputs 后批准（body: { modified_inputs }）|
| `POST /sessions/{id}/resume` | Session 从 PAUSED_HITL 手动恢复（需所有 pending approval 已处理）|

---

# 9. Memory 管理

## 9.1 Memory 三层架构

| 层次 | 存储 | 内容 | 检索方式 |
|---|---|---|---|
| **短期上下文** | Redis + DB | 最近 N 轮消息（window=20）| 顺序读取，O(1) |
| **长期记忆** | PostgreSQL + pgvector | 压缩摘要、关键 facts | 语义检索（cosine）+ 时间过滤 |
| **滚动摘要** | PostgreSQL | 每 20 条消息触发压缩 | 直接拼入 system prompt |

## 9.2 上下文拼装优先级

`build_prompt_context` 分两段：**system prompt**（固定部分）和**消息上下文**（动态部分），总量不超过 `token_budget × 0.6`。

**第一段：system prompt（每轮固定注入，来源于 AgentTemplate 字段）**

| # | 来源 | 条件 | 说明 |
|---|---|---|---|
| S1 | `soul_md` 正文 | 始终 | agent 人格与价值观 |
| S2 | `role_md` 正文 | 始终 | 职责边界 |
| S3 | `tools_md` 正文 | `tool_list` 非空 | 工具使用指南 |
| S4 | `style_md` 正文 | `inject_style=true` | 输出格式偏好 |

**第二段：消息上下文（每轮 Observe 阶段动态拼装）**

| # | 来源 | 条件 | 说明 |
|---|---|---|---|
| C1 | 当前 Task 描述 + inputs | 始终 | 必填，当前要做什么 |
| C2 | 匹配到的 Skill 内容 | Skill Router 有匹配结果时 | 按需加载 SKILL.md 正文，不在 system prompt 里 |
| C3 | `_digest` 最新内容 | root-agent | 全局视图（Session 整合后的 Blackboard 摘要）|
| C4 | 订阅 topic 的增量 Entry | sub-agent | blackboard.pull() 拉取，按 cursor 增量 |
| C5 | 最近消息窗口 | 始终 | short_window_size 条，按 seq_no 倒序 |
| C6 | 滚动摘要 | memories 表有 summary 记录时 | 最新一条，压缩历史 |
| C7 | 语义检索 facts | use_vector_search=true 时 | top_k=5，score > 0.75 才纳入 |

> **Skill 不放入 system prompt 的原因**
> - system prompt 在每轮 LLM 调用时都要传入，是刚性 token 成本；Skill 内容通常较长，按需加载才合理
> - 大多数 Skill 在大多数 Task 里用不上，全量注入是噪声
> - Skill 描述的是「当前该怎么做」，system prompt 描述的是「agent 是谁」，两者语义层级不同

## 9.3 Memory 写入流程

- **append_message**：写入 messages 表，累加 token_count 到 session.token_used
- **阈值检测**：messages 数量 >= summary_threshold（默认 20）时，触发 summarize_memory
- **summarize_memory**：LLM 压缩最早 N 条消息，写入 memories（type=summary），旧消息标记 is_archived=true
- **extract_facts**：从 Task 结果中提取关键 facts，生成 embedding，写入 memories（type=fact）
- **冲突处理**：同类 fact 冲突时保留多版本（打时间戳），由上层 LLM 在拼装时自行处理

---

# 10. Tool 与 Skill 系统

## 10.1 Tool 设计（MCP 协议）

严格遵循 **MCP（Model Context Protocol）** 规范：

- **发现**：通过 tools/list 获取可用工具（name / description / inputSchema / annotations）
- **执行**：通过 tools/call 调用，返回 content（可读）+ structuredContent（结构化）
- **错误**：isError=true 时附标准错误码（TOOL_TIMEOUT / TOOL_FORBIDDEN / TOOL_EXEC_ERROR）

**内置 MCP Tools（MVP）**

| 工具 | 参数 | 安全约束 |
|---|---|---|
| `bash_exec` | command, cwd, timeout_ms, env_whitelist | 命令黑名单；超时上限 30s；输出 ≤ 64KB；目录白名单；requires_approval=true（默认）|
| `http_request` | method, url, headers, body, timeout_ms | 域名白名单/黑名单；SSRF 内网拦截；响应体 ≤ 512KB；写操作 requires_approval=true |
| `spawn_agents` | reason, plan[], resume_hint | has_spawn_permission 权限检查；max_spawn_tasks 上限；Token 余量检查；调用全量审计 |

## 10.2 policy_engine 授权

- 按 `(user_role, task_type, tool_name)` 三元组授权，V1 简化为白名单配置
- 全部调用写入 tool_calls 表，输入输出必须脱敏（api_key、密码等替换为 ***）
- tool_calls 审计记录不可删除，保留 90 天
- compensation 字段记录有副作用工具的回滚动作

## 10.3 Skill 设计（Anthropic Skill 形式）

- **目录结构**：每个 Skill 为独立目录，主文件 SKILL.md，支持 scripts/ / assets/ / references/
- **Frontmatter**：YAML 格式，含 name / description / triggers（触发关键词）/ tools（依赖工具列表）
- **触发方式**：显式（用户/Agent 指定 skill 名称）或隐式（Skill Router 基于 description 匹配）
- **执行边界**：Skill 只定义流程和检查单，实际动作必须通过 Tool Gateway 执行
- **审计**：skill 命中记录进入 session_events（含 skill 版本、触发原因、执行步骤）

---

# 11. LLM 适配层

屏蔽不同 LLM 供应商 API 差异，向上层提供统一接口；支持运行时动态注册与切换。

## 11.1 架构分层

`Agent.call_llm(LLMRequest)` → `LLMClient（统一门面）` → `BaseAdapter` → `Transport（httpx）`

- **OpenAIAdapter**：适配 OpenAI Chat Completions API，system_prompt 插入为 system role 消息
- **AnthropicAdapter**：适配 Anthropic Messages API，system_prompt 使用独立 system 字段，max_tokens 默认 4096
- **MockAdapter**：测试/离线用途

## 11.2 统一数据结构

**LLMRequest**

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string | 模型名称（关联 LLMRegistry 的 name 字段）|
| `messages` | LLMMessage[] | 对话消息列表（role + content）|
| `system_prompt` | string \| None | 系统提示，适配层自动注入各供应商正确位置 |
| `tools` | LLMTool[] | 可用工具列表，适配层转换为供应商格式 |
| `temperature` | float | 采样温度 |
| `max_tokens` | int | 最大输出 token 数 |

**ParsedResponse**

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | string \| None | 纯文本输出（无 tool call 时）|
| `tool_calls` | ToolCallBlock[] | 工具调用列表（id / name / arguments）|
| `usage` | LLMUsage | prompt_tokens / completion_tokens / total_tokens |

## 11.3 供应商兼容性

| 能力 | OpenAI | Anthropic |
|---|---|---|
| system_prompt | 插入为 system role 消息 | 使用独立 system 字段 |
| Tool Calling | tool_calls + tool role | tool_use + tool_result |
| max_tokens | 可选 | 必填（适配层默认 4096）|
| 流式输出 | 支持（待接入）| 支持（待接入）|

---

# 12. REST API 设计

## 12.1 通用约定

- **Base URL**：/api/v1
- **鉴权**：`Authorization: Bearer <token>`
- **幂等**：写接口支持 Idempotency-Key（推荐 UUID）
- **追踪**：X-Request-Id 透传到日志/trace
- **时间格式**：统一 ISO-8601 UTC，如 2026-03-09T09:00:00Z
- **分页**：默认 page=1、page_size=20、page_size<=100

**统一返回格式**：`{ "code": "OK", "message": "success", "data": {}, "request_id": "req_01H..." }`

**错误返回格式**：`{ "code": "INVALID_ARGUMENT", "message": "...", "request_id": "...", "details": {} }`

## 12.2 核心接口

| 端点 | 说明 |
|---|---|
| **Session** | |
| `POST /sessions` | 201 ses_id |
| `GET /sessions/{id}` | session 详情 |
| `POST /sessions/{id}/cancel` | 200 |
| `POST /sessions/{id}/retry` | 202 |
| `GET /sessions/{id}/events` | 分页事件流 |
| `GET /sessions/{id}/stream` | SSE 推送 |
| `GET /sessions/{id}/guard-status` | token/并发/失败计数 |
| `PATCH /sessions/{id}/guard-config` | 热更新 Guard 参数 |
| **Task** | |
| `POST /sessions/{id}/tasks` | 201 task |
| `GET /tasks/{id}` | task 详情 |
| `GET /sessions/{id}/tasks` | 任务列表（分页）|
| **Memory** | |
| `POST /tasks/{id}/messages` | 追加消息 |
| `GET /tasks/{id}/context` | 拼装后的完整上下文 |
| `GET /tasks/{id}/memories` | 语义检索记忆（query / type / top_k）|
| **HITL** | |
| `GET /hitl/pending` | 待确认列表 |
| `GET /hitl/{id}` | 详情（含 task_snapshot）|
| `POST /hitl/{id}/approve` | 批准 |
| `POST /hitl/{id}/reject` | 拒绝 |
| `POST /hitl/{id}/modify` | 修改 inputs 后批准 |
| `POST /sessions/{id}/resume` | 从 PAUSED_HITL 恢复 |
| **Tool 审计** | |
| `GET /sessions/{id}/tool-calls` | 工具调用列表 |
| `GET /tool-calls/{id}` | 调用明细（脱敏后）|
| **LLM Provider** | |
| `POST /llms` | 注册供应商（name / style / api_key / base_url）|
| `GET /llms` | 已注册供应商列表 |
| `DELETE /llms/{name}` | 移除供应商 |
| **Agent Template** | |
| `POST /agent-templates/import` | 导入四个 MD 文件，写入 AgentTemplate |
| `GET /agent-templates/{id}` | 模板详情 |

---

# 13. 数据库 Schema 汇总

以下为 V1 最小表集，所有表含 `created_at`（TIMESTAMPTZ NOT NULL DEFAULT now()）和 `updated_at`（TIMESTAMPTZ）。

| 表名 | 核心字段（补充说明）|
|---|---|
| **sessions** | id, goal, status, root_agent_id, token_budget, token_used, max_concurrent_tasks, max_concurrent_agents, failure_counter, failure_threshold, consolidation_threshold, digest_token_budget, root_max_turns, sub_max_turns, runtime_summary |
| **session_task_queue** | id, session_id, task_id, status, enqueue_at, dequeue_at |
| **task_edges** | id, session_id, from_task_id, to_task_id（DAG 依赖边）|
| **tasks** | id, session_id, type, assigned_agent_id, creator_agent_id, parent_task_id, spawned_by_tool_call_id, status, requires_approval, approval_id, output_topic, inputs, outputs, result, retry_count, max_retries, timeout_ms, compensation, priority, error, error_code |
| **agent_templates** | id, name, description, **soul_md**, **role_md**, **tools_md**, **style_md**, **tool_list**, **tool_list_ready**, skill_list, memory_config, inject_style, has_spawn_permission, max_spawn_tasks, default_model |
| **agents** | id, template_id, session_id, spawn_depth, parent_agent_id, active_task_id, spawned_task_ids, resume_hint, status, loop_guard, subscribed_topics, model |
| **session_events** | id, session_id, event_type, payload, occurred_at |
| **messages** | id, task_id, session_id, role, content, metadata, token_count, seq_no, is_archived |
| **memories** | id, task_id, type（message/summary/fact）, content, embedding（vector 1536）, source_message_id |
| **blackboard_topics** | id, session_id, name, is_system, entry_count, last_digest_at |
| **blackboard_entries** | id, topic_id, session_id, type, content, publisher_agent_id, source_task_id, embedding, is_superseded, expired_at |
| **blackboard_subscriptions** | id, agent_id, topic_id, last_read_cursor |
| **hitl_approvals** | id, session_id, task_id, trigger_reason, task_snapshot, status, decision_by, modified_inputs, decided_at, expire_at |
| **tool_specs** | id, tool_name, input_schema, annotations, updated_at |
| **tool_calls** | id, session_id, task_id, tool_name, input_redacted, output_redacted, status, latency_ms, error, spawned_task_ids（spawn_agents 专用）|
| **artifacts** | id, task_id, type, uri, metadata |
| **llm_providers** | id, name, style（openai/anthropic）, base_url, model, timeout_sec（api_key 加密存储，不返回）|
| **lifecycle_manager_state** | id, session_id, concurrent_agents, concurrent_tasks, failure_counter, pending_schedule_queue（崩溃恢复用）|

---

# 14. 事件类型全集

| 事件类型 | 触发时机 |
|---|---|
| **Session 事件** | |
| `SESSION_CREATED` | 会话创建成功 |
| `SESSION_STATUS_CHANGED` | 状态变更（含转入 PAUSED_HITL / RETRYING）|
| `TOKEN_BUDGET_WARNING` | token_used >= token_budget × 90% |
| `TOKEN_BUDGET_EXCEEDED` | token_used >= token_budget，session 终止 |
| `SESSION_PAUSED_HITL` | failure_counter >= failure_threshold 自动暂停 |
| **Task 事件** | |
| `TASK_STARTED` | Task 进入 ACTIVE |
| `TASK_FINISHED` | Task 完成，触发 Session Blackboard 整合检查 |
| `TASK_FAILED` | Task 失败，LM 处理重试或 failure_counter |
| `TASK_SUSPENDED` | agent 晋升后 Task 挂起 |
| `TASK_RESUMED` | SUSPENDED Task 恢复为 ACTIVE |
| `LIFECYCLE_TASK_READY` | DAG 依赖满足，Task 进入 pending_schedule_queue |
| **Agent 事件** | |
| `LIFECYCLE_AGENT_SCHEDULED` | LM 实例化新 sub-agent |
| `AGENT_FINISHED` | sub-agent 完成（由 LM 回收完成后发布）|
| `LIFECYCLE_AGENT_RECYCLED` | LM 回收 sub-agent 资源 |
| `AGENT_WAITING` | agent 调用 spawn_agents 成功，进入 WAITING |
| `AGENT_RESUME` | 所有派生 Task 完成，agent 恢复 RUNNING |
| **Spawn 事件** | |
| `SPAWN_REQUESTED` | Tool Gateway 收到 spawn_agents 调用，转发给 LM 审批 |
| `SPAWN_APPROVED` | LM 审批通过，子 Task 创建完成 |
| `SPAWN_REJECTED` | LM 审批拒绝，含 reject_reason |
| `SPAWN_CHILD_ALL_FINISHED` | LM 内部：SUSPENDED Task 的所有子 Task 完成 |
| `SPAWN_ALL_FAILED` | 所有子 Task 失败，SUSPENDED Task 转为 FAILED |
| **HITL 事件** | |
| `HITL_APPROVAL_REQUIRED` | Task 进入 PENDING_APPROVAL |
| `HITL_APPROVED / HITL_REJECTED / HITL_MODIFIED` | 人工操作完成 |
| `HITL_TIMEOUT` | approval_id 超时未操作 |
| **Blackboard 事件** | |
| `BLACKBOARD_ENTRY_PUBLISHED` | blackboard.publish() 调用后 |
| `BLACKBOARD_CONSOLIDATION_STARTED` | Session 开始跨 topic 整合 |
| `BLACKBOARD_CONSOLIDATION_FINISHED` | 整合完成，digest 写入 `_digest` topic |
| **Memory 事件** | |
| `MEMORY_SUMMARIZED` | 消息数量达到阈值，触发滚动摘要 |

---

# 15. 可观测性与非功能设计

## 15.1 可观测性

- **指标（Prometheus）**：
  - `api.qps`、`api.p95_latency`、`api.5xx_rate`
  - `worker.queue_depth`、`worker.success_rate`、`worker.retry_rate`、`worker.timeout_rate`
  - `tool.call_count`、`tool.error_rate`、`tool.latency_p95`
  - `llm.prompt_tokens`、`llm.completion_tokens`、`llm.cost_per_session`
  - `agent.spawn_count`、`agent.waiting_gauge`、`blackboard.consolidation_latency`
- **日志（结构化 JSON）**：最小字段集 timestamp / level / request_id / task_id / session_id / event / error_code
- **链路追踪（OpenTelemetry）**：API → Orchestrator → Worker → Tool Gateway 全链路 trace；spawn 树以 parent_span_id 关联

## 15.2 初始 SLO

- 会话创建成功率 >= 99.9%
- API p95 延迟 < 300ms（不含长轮询/SSE）
- 异步任务最终完成率 >= 99%

## 15.3 安全

- 鉴权：JWT/API Key（服务间建议 mTLS）
- 鉴权后细粒度授权（RBAC/ABAC）
- 输入校验：JSON Schema + 长度限制 + 字符过滤
- 防注入：命令、SQL、模板注入防护
- 敏感信息脱敏与加密存储（KMS）；tool_calls 输入输出强制脱敏

## 15.4 高可用与性能

- API/Worker 无状态，支持水平扩展
- Redis/PostgreSQL 主从与备份恢复
- Lifecycle Manager 状态持久化到 Redis（lifecycle_manager_state 表），崩溃重启后可从 DB 恢复
- **关键优化**：Memory 查询索引化（session_id, created_at）；热会话缓存；批量写事件日志；blackboard_entries embedding 索引（ivfflat）
- **初始容量估算**：单实例 API 500~1000 RPS（轻请求）；Worker 并发按 CPU/IO 类型隔离队列

---

# 16. 技术选型

| 层次 | 选型 | 备注 |
|---|---|---|
| **语言** | Python 3.11+ | 主栈 |
| **API 框架** | FastAPI + Uvicorn | WebSocket + SSE 原生支持 |
| **ORM** | SQLAlchemy 2.0 + Alembic | async 模式；Alembic 管理迁移 |
| **数据库** | PostgreSQL 16 + pgvector | pgvector 用于 embedding 语义检索 |
| **缓存/队列** | Redis 7 + Redis Streams | 短期上下文缓存、分布式锁、任务队列 |
| **HTTP 客户端** | httpx（async）| LLM 适配层 Transport；可替换为 aiohttp |
| **Observability** | OpenTelemetry + Prometheus + Grafana | 链路追踪 + 指标 + 可视化 |
| **对象存储（可选）** | MinIO / S3 兼容 | 大文本 Artifact 存储 |
| **容器化** | Docker + Docker Compose | V1 单机部署；后续 K8s 扩展 |

---

> **核心设计原则**
> - 清晰优先于完备：V1 先跑通核心 Loop，Guard 机制先于 Observability 落地
> - 职责分离：root-agent 只管 Plan/Execute；Lifecycle Manager 只管调度/回收；Session 只管 Blackboard 整合
> - 安全内置而非外挂：token_budget / concurrent 限制在 Loop 最内层检查，不可绕过
> - 协议对齐降低接入成本：Tool 走 MCP、Skill 走 Anthropic 形式，便于后续扩展
> - 状态可恢复：所有状态持久化到 PostgreSQL；LM 状态持久化到 Redis；崩溃后可断点续跑
> - Human-in-the-Loop 是一等公民：V1 就内置，而不是后续补丁
> - Blackboard 与 agent 实例解耦：agent 退出后 topic 数据持续有效
> - 动态树深度自然约束：WAITING agent 占槽 + token_budget 双重机制，无需 max_depth 参数
> - Agent 身份定义与运行时分离：MD 文件是创作物，AgentTemplate 是运行时表示，tool_list 懒加载是两者之间唯一需要语义理解的桥梁

*miniAgents Design Document v1.1 Final · 2026-03-30*