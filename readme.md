# miniAgents 开发文档（Phase 1）

> 基于 miniAgents Design Document v1.0 Final（2026-03-26）
> Phase 1 目标：**跑通最小可运行的单 Agent Loop**，存储用本地 JSON 文件替代数据库。

---

## 0. 第一阶段开发范围

### 0.1 Phase 1 目标

在不依赖任何数据库的前提下，跑通完整的 Agent 执行闭环：

1. 创建 Session，指定目标（goal）
2. Session 自动创建 root-agent，启动 Agent Loop
3. Agent Loop 执行：Observe → Plan → CreateTask → Execute → UpdateMemory
4. Execute 阶段支持 LLM 推理调用与工具调用（bash_exec / http_request）
5. Memory 管理：短期消息窗口 + 滚动摘要
6. 基础 Blackboard：topic 发布与增量拉取（内存中）
7. 基础 Guard：token_budget 硬限制
8. 所有状态落盘为本地 JSON 文件

### 0.2 Phase 1 交付清单

| 模块 | Phase 1 交付 |
| --- | --- |
| LLM 适配层 | ✅ 已完成（OpenAI / Anthropic / Mock） |
| 领域模型 | Session / Task / Agent / AgentTemplate / MemoryItem / BlackboardEntry |
| Agent Loop | Observe → Plan → CreateTask → Execute → UpdateMemory |
| 工具执行 | bash_exec / http_request（简化版，无沙盒） |
| Memory | 短期消息窗口 + 触发滚动摘要 |
| Blackboard | 内存中 topic / entry，支持 publish / pull |
| Guard | token_budget 硬限制；max_turns 软警告 |
| 文件存储 | 所有状态写入 `data/` 目录下的 JSON 文件 |
| REST API | Session CRUD、Task CRUD、Tool 审计、LLM Provider 管理 |

### 0.3 Phase 1 明确不做（推迟到 Phase 2+）

| 特性 | 原因 |
| --- | --- |
| PostgreSQL / Redis / pgvector | Phase 1 用文件替代 |
| spawn_agents / 动态多层 Agent 树 | 依赖并发调度与 LM，复杂度高 |
| Human-in-the-Loop（HITL） | 需要独立 HITL Manager 与审批 API |
| Lifecycle Manager（完整版） | Phase 1 用简化内联调度代替 |
| Blackboard 整合（LLM 压缩 + 向量聚类） | 依赖 pgvector |
| failure_threshold 自动暂停 | 依赖 HITL |
| Session PAUSED_HITL 状态 | 依赖 HITL |
| 语义检索（pgvector） | 依赖数据库 |
| OpenTelemetry / Prometheus | Phase 1 用结构化日志替代 |
| SSE 流式推送 | Phase 2 |
| 幂等键强制校验 | Phase 2 |

### 0.4 文件存储约定

```
data/
  sessions/
    {session_id}.json          # Session 完整状态
  tasks/
    {task_id}.json             # Task 完整状态
  agents/
    {agent_id}.json            # Agent 完整状态
  memory/
    {session_id}/
      messages.jsonl           # 追加写，每行一条消息
      summaries.json           # 摘要列表
  blackboard/
    {session_id}/
      {topic}.jsonl            # 追加写，每行一条 Entry
  tool_calls/
    {session_id}.jsonl         # 追加写，审计日志
```

**读写原则**：
- 运行时状态保存在内存中；状态变更后同步写入对应 JSON 文件
- 进程重启时从文件恢复所有状态
- 文件写入使用原子替换（先写 `.tmp` 再 rename）防止写损坏

---

## 1. 项目目标与范围

`miniAgents` 是一个 Agent 后端服务，以学习验证架构思路为首要目标，同时构建稳定、可扩展、可观测的基础设施 Demo。核心理念是：**先跑通最小可运行的 Agent Loop，再逐步迭代安全防护与可观测性**。

### 1.1 核心能力（完整愿景）

1. Agent 级 Memory 管理：短期消息窗口 + 长期事实摘要 + pgvector 语义检索
2. Session 级调度管理：Task 栈 + DAG 依赖 + 并发控制 + 完整状态机
3. 动态多层 Agent 树：任意 agent 可通过 `spawn_agents` 晋升为局部 root，形成运行时树形结构
4. Blackboard 发布订阅：topic 主键、与 agent 实例解耦、Session 事件触发整合
5. 内置工具执行：`bash_exec`、`http_request`，严格遵循 MCP 协议
6. Human-in-the-Loop：Task 级执行前人工确认，支持 approve / reject / modify
7. Loop 安全防护：`token_budget` 硬终止 + `failure_threshold` 软暂停 + 并发数量上限

### 1.2 V1 范围约束

- 单租户优先，后续平滑扩展为多租户
- Python + FastAPI 主栈
- Phase 1：文件存储；Phase 2+：PostgreSQL（pgvector）+ Redis Streams
- 先跑通核心 Loop，Guard 机制先于 Observability 落地
- Phase 1 不支持 sub-agent 与多 agent 协作

---

## 2. 总体架构

### 2.1 架构分层

| 层次 | 职责 | Phase 1 实现 |
| --- | --- | --- |
| API 层 | REST 接口；鉴权、参数校验、限流；Idempotency-Key | FastAPI 路由，基础参数校验 |
| Orchestrator 层 | Session 状态机；Task 调度；Guard 检查；HITL 暂停点 | 简化内联，Session / Task 状态机 |
| Worker Runtime 层 | Agent Loop；Skill Router；token_budget 检查 | Agent Loop 核心逻辑 |
| LLM 适配层 | 统一 LLMClient；OpenAI / Anthropic 适配；Tool Calling | ✅ 已完成 |
| Tool Gateway 层 | MCP tools/call；policy_engine 授权；审计脱敏 | 简化版（白名单 + 审计文件） |
| 存储层 | 会话/任务/记忆持久化 | Phase 1：本地 JSON 文件 |
| Observability 层 | 结构化日志；指标；链路追踪 | Phase 1：结构化 JSON 日志 |

### 2.2 逻辑组件

| 组件 | 职责简述 | Phase 1 |
| --- | --- | --- |
| Session Manager | 创建/恢复/终止会话；维护状态机与超时；触发 SESSION_* 事件 | ✅ 实现 |
| Task Manager | 入栈出栈；优先级队列；DAG 依赖边维护 | ✅ 简化版（无 DAG） |
| Agent Registry | 管理 AgentTemplate 与运行实例 | ✅ 实现 |
| Agent Runtime | 驱动 Agent Loop；持有 Guard（token_budget、max_turns） | ✅ 实现 |
| Lifecycle Manager | 监听事件；回收 sub-agent；操作 task_stack；维护并发计数器 | Phase 2（Phase 1 内联） |
| HITL Manager | 管理待确认 Task 队列；provide approve/reject/modify API | Phase 2 |
| Memory Service | 消息写入；滚动摘要；Token 预算拼装 | ✅ 实现（无向量检索） |
| Blackboard Service | topic 注册；publish/subscribe/pull | ✅ 实现（内存 + 文件） |
| Tool Gateway | MCP 协议执行；policy_engine 授权；调用脱敏审计 | ✅ 简化版 |
| Skill Router | description 匹配；按需加载 SKILL.md；生成执行计划 | Phase 2 |
| LLM Registry | 供应商注册/切换；运行时动态注册 | ✅ 已完成 |
| Event Bus | 事件发布订阅 | Phase 1：内存 EventBus |

---

## 3. 核心领域模型

### 3.1 Session

一次目标导向的 LLM 会话容器，持有完整的调度上下文和 Guard 参数。

| 字段 | 类型 | 说明 | Phase 1 |
| --- | --- | --- | --- |
| id | string | `ses_` + ULID | ✅ |
| goal | string | 会话目标，注入 root agent 的 system_prompt | ✅ |
| status | enum | `QUEUED → RUNNING → SUCCEEDED / FAILED / TIMEOUT / CANCELED` | ✅ |
| root_agent_id | string | 会话入口 Agent（spawn_depth=0） | ✅ |
| token_budget | int | 全局 token 上限（硬限制），默认 200000 | ✅ |
| token_used | int | 已消耗 token，实时累加 | ✅ |
| max_concurrent_tasks | int | 同时 ACTIVE Task 上限，默认 8 | Phase 2 |
| max_concurrent_agents | int | 同时存活 Agent 上限，默认 4 | Phase 2 |
| failure_counter | int | 连续失败次数；任意 Task FAILED +1，成功归零 | ✅ 计数，暂停逻辑 Phase 2 |
| failure_threshold | int | 连续失败软暂停阈值，默认 3 | Phase 2 |
| root_max_turns | int | Session root agent 的 Loop 轮次上限，默认 20 | ✅ |
| sub_max_turns | int | 子 agent 的 Loop 轮次上限，默认 10 | Phase 2 |
| runtime_summary | object | 执行统计：重试次数、token 成本、工具调用统计 | ✅ |
| created_at / updated_at | datetime | UTC ISO-8601 | ✅ |

**Session 状态机（Phase 1）**：

```
QUEUED → RUNNING → SUCCEEDED
                 → FAILED        （token 耗尽 / 超出 max_turns / 未捕获异常）
                 → CANCELED      （用户主动取消）
```

> Phase 2 补充：`PAUSED_HITL`（failure_counter 触发）、`RETRYING`（断点续跑）

**Guard 规则（Phase 1）**：
- `token_used >= token_budget` → 立即 `FAILED`，`error_code = TOKEN_BUDGET_EXCEEDED`
- `turns_used >= root_max_turns` → 写警告事件，Loop 停止，Session `FAILED`

### 3.2 Task

会话内的最小工作单元。

| 字段 | 类型 | 说明 | Phase 1 |
| --- | --- | --- | --- |
| id | string | `tsk_` + ULID | ✅ |
| session_id | string | 所属会话 | ✅ |
| type | enum | `reasoning / tool-call / skill` | ✅ |
| assigned_agent_id | string | 执行此 Task 的 Agent | ✅ |
| creator_agent_id | string | 创建此 Task 的 Agent | ✅ |
| parent_task_id | string | 派生关系（spawn 后父 Task id） | Phase 2 |
| status | enum | `PENDING → ACTIVE → FINISHED / FAILED / CANCELED` | ✅ |
| requires_approval | bool | true 时执行前需 HITL 确认 | Phase 2 |
| output_topic | string | 派生 agent 产出写入的 Blackboard topic | Phase 2 |
| inputs / outputs / result | object | 结构化输入输出，可引用 Artifact | ✅ |
| retry_count / max_retries | int | 当前重试次数 / 上限（默认 3） | ✅ |
| timeout_ms | int | 执行超时，默认 60000 | ✅ |
| priority | int | 优先级（0-10），默认 5 | ✅ |
| error / error_code | string | 失败时的错误信息 | ✅ |

**Task 状态机（Phase 1）**：

```
PENDING → ACTIVE → FINISHED
                 → FAILED
                 → CANCELED
```

> Phase 2 补充：`PENDING_APPROVAL`（HITL 等待）、`SUSPENDED`（spawn 后挂起）、`REJECTED`

### 3.3 AgentTemplate

Agent 的静态配置模板，与运行实例解耦。

| 字段 | 类型 | 说明 | Phase 1 |
| --- | --- | --- | --- |
| id | string | `tpl_` + ULID | ✅ |
| name | string | 模板名称 | ✅ |
| system_prompt | string | Agent 角色与能力描述 | ✅ |
| tool_list | string[] | 白名单工具名称列表 | ✅ |
| skill_list | string[] | 可用 Skill 名称列表 | Phase 2 |
| memory_config | object | `{ short_window_size, max_summary_tokens }` | ✅ |
| has_spawn_permission | bool | 是否允许调用 spawn_agents | Phase 2（固定 false） |
| max_spawn_tasks | int | 单次 spawn_agents 最多派生的子 Task 数 | Phase 2 |
| default_model | string | 默认 LLM 供应商名称 | ✅ |

### 3.4 Agent

上下文管理容器，与 AgentTemplate 关联，代表一次运行实例。

| 字段 | 类型 | 说明 | Phase 1 |
| --- | --- | --- | --- |
| id | string | `agt_` + ULID | ✅ |
| template_id | string | 关联 AgentTemplate | ✅ |
| session_id | string | 所属会话 | ✅ |
| spawn_depth | int | 树中层级深度，session root = 0 | ✅（Phase 1 固定 0） |
| parent_agent_id | string | 上级 agent id；session root 为 null | Phase 2 |
| active_task_id | string | 当前处理的 Task | ✅ |
| status | enum | `IDLE → RUNNING → FINISHED / FAILED` | ✅ |
| loop_guard | object | `{ max_turns, turns_used }` | ✅ |
| model | string | 使用的 LLM 供应商名称 | ✅ |
| created_at / updated_at | datetime | UTC | ✅ |

> Phase 2 补充：`spawned_task_ids`、`resume_hint`、`subscribed_topics`、`WAITING` 状态

### 3.5 MemoryItem

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | `mem_` + ULID |
| session_id | string | 所属会话 |
| task_id | string | 来源 Task（可选） |
| type | enum | `message / summary / fact` |
| role | string | `user / assistant / system / tool`（message 类型） |
| content | string / object | 消息内容或结构化摘要 |
| token_count | int | 内容 token 估算 |
| seq_no | int | 消息序号（message 类型，顺序读取用） |
| is_archived | bool | 被滚动摘要覆盖时标记 true |
| created_at | datetime | UTC |

### 3.6 BlackboardEntry

Phase 1 使用内存 + 文件的简化版 Blackboard，保留 topic 概念但不做 pgvector 整合。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | `bbe_` + ULID |
| session_id | string | 所属会话 |
| topic | string | topic 名称（如 `research/findings`、`_root`） |
| type | enum | `note / fact / plan / artifact_ref / digest / error` |
| content | object | 结构化内容 |
| publisher_agent_id | string | 发布者 agent id |
| source_task_id | string | 产出此条目的 Task id（可选） |
| created_at | datetime | UTC |

**保留 Topic（系统自动创建）**：

| Topic | 用途 |
| --- | --- |
| `_root` | root agent 发布规划内容；所有 sub-agent 默认订阅 |
| `_digest` | Session 发布跨 topic 整合摘要（Phase 2 才有 LLM 压缩；Phase 1 只做条目汇总） |
| `_lifecycle` | Lifecycle Manager 发布 agent 状态变更（只读） |

### 3.7 ToolCall（审计记录）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | `tlc_` + ULID |
| session_id | string | 所属会话 |
| task_id | string | 触发此调用的 Task |
| tool_name | string | 工具名称 |
| input_redacted | object | 脱敏后的入参 |
| output_redacted | object | 脱敏后的出参 |
| status | enum | `PENDING / RUNNING / SUCCEEDED / FAILED` |
| latency_ms | int | 调用耗时 |
| error | string | 失败时的错误信息 |
| created_at | datetime | UTC |

---

## 4. Agent Loop

### 4.1 Loop 阶段（Phase 1 root-agent）

| 阶段 | 核心动作 | Phase 1 实现 |
| --- | --- | --- |
| **Observe** | 读取 session 状态；拉取 Blackboard `_root` / `_digest` 增量；检查 token_budget 余量；读取 active_task inputs | ✅ |
| **Plan** | 调用 LLM 生成执行计划；决定本轮创建哪些 Task；`turns_used += 1` | ✅ |
| **CreateTask** | 按计划创建 Task，写入 task_stack | ✅ |
| **Execute** | 按序执行 Task（reasoning / tool-call）；等待完成 | ✅ |
| **UpdateMemory** | 写入消息和产出；检查是否触发滚动摘要；发布产出到 Blackboard | ✅ |

**Loop 终止条件（Phase 1）**：
- `session.token_used >= session.token_budget` → `TOKEN_BUDGET_EXCEEDED`，Session `FAILED`
- `turns_used >= root_max_turns` → 写 `MAX_TURNS_REACHED` 警告事件，Loop 停止
- 所有 Task 执行完毕且 LLM 判断目标已达成 → Session `SUCCEEDED`

### 4.2 Context 拼装（build_prompt_context）

上下文拼装总量不超过 `token_budget × 0.6`（预留工具返回和响应空间）：

| 优先级 | 内容 | 来源 |
| --- | --- | --- |
| 1 | system_prompt + 会话 goal | AgentTemplate + Session |
| 2 | 当前 Task 描述与 inputs | Task |
| 3 | `_root` topic 最新内容（规划摘要） | Blackboard |
| 4 | 最近消息窗口（short_window_size 条） | memory/messages.jsonl |
| 5 | 滚动摘要 | memory/summaries.json |

> Phase 2 补充：`_digest` 全局摘要、pgvector 语义检索 facts、sub-agent 的 topic 增量

### 4.3 Memory 写入流程

1. `append_message(session_id, role, content)` → 追加写入 `memory/{session_id}/messages.jsonl`，累加 `session.token_used`
2. 消息数量 `>= summary_threshold`（默认 20）时，触发 `summarize_memory`
3. `summarize_memory`：调用 LLM 压缩最早 N 条消息，写入 `summaries.json`，旧消息标记 `is_archived=true`
4. Task 完成后，将关键结论 publish 到 Blackboard

### 4.4 Guard 规则（Phase 1）

**硬限制（超出立即终止）**：
- `token_used >= token_budget` → Session 立即 `FAILED`，`error_code = TOKEN_BUDGET_EXCEEDED`

**软限制（警告）**：
- `token_used >= token_budget × 0.9` → 写入 `TOKEN_BUDGET_WARNING` 事件
- `turns_used >= root_max_turns` → 写入 `MAX_TURNS_REACHED` 事件，Loop 停止，Session `FAILED`
- `failure_counter >= failure_threshold` → Phase 1 仅写 `FAILURE_THRESHOLD_REACHED` 事件，不自动暂停（Phase 2 才接入 HITL）

---

## 5. Tool 系统（Phase 1）

### 5.1 内置工具（MVP）

严格遵循 [MCP 协议](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)格式。

**bash_exec**

```json
{
  "name": "bash_exec",
  "description": "Execute a shell command in a restricted environment",
  "inputSchema": {
    "type": "object",
    "properties": {
      "command":    { "type": "string", "description": "Shell command to execute" },
      "cwd":        { "type": "string", "description": "Working directory" },
      "timeout_ms": { "type": "integer", "description": "Timeout in milliseconds, default 30000" }
    },
    "required": ["command"]
  }
}
```

Phase 1 安全约束（简化）：
- 命令黑名单：`rm -rf /`、`shutdown`、`reboot` 等破坏性命令
- 超时上限：30s（`timeout_ms` 最大值）
- 输出大小上限：64KB

**http_request**

```json
{
  "name": "http_request",
  "description": "Make an HTTP request",
  "inputSchema": {
    "type": "object",
    "properties": {
      "method":     { "type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"] },
      "url":        { "type": "string" },
      "headers":    { "type": "object" },
      "body":       { "type": "object" },
      "timeout_ms": { "type": "integer", "default": 10000 }
    },
    "required": ["method", "url"]
  }
}
```

Phase 1 安全约束（简化）：
- 不访问 `localhost` / `127.x.x.x` / `169.254.x.x`（基础 SSRF 防护）
- 响应体大小上限：512KB

> Phase 2 补充：`spawn_agents`（动态多层 Agent 树）；域名白名单/黑名单；HITL 确认

### 5.2 Tool Gateway 调用流程

```
Agent.run_tools(tool_calls)
  → ToolGateway.call(tool_name, arguments, agent_id, task_id)
    → policy_engine.authorize(agent_id, tool_name)   # 检查白名单
    → 执行工具
    → 脱敏输入输出（api_key / password → "***"）
    → 写入 tool_calls/{session_id}.jsonl（审计）
    → 返回 ToolResult
```

### 5.3 Tool 返回格式（MCP 规范）

```json
{
  "content": [{ "type": "text", "text": "命令输出..." }],
  "isError": false
}
```

错误时：

```json
{
  "content": [{ "type": "text", "text": "Error: command timed out" }],
  "isError": true,
  "errorCode": "TOOL_TIMEOUT"
}
```

---

## 6. Blackboard（Phase 1 简化版）

### 6.1 接口

```python
blackboard.publish(session_id, topic, type, content, agent_id, task_id=None)
# 发布一条 Entry；topic 不存在时自动创建

blackboard.subscribe(session_id, topic, agent_id)
# 声明订阅；初始化 last_read_cursor=None（首次拉取全量）

blackboard.pull(session_id, agent_id) -> list[BlackboardEntry]
# 拉取所有订阅 topic 的增量 Entry（cursor 之后）；返回后更新 cursor

blackboard.get_topic(session_id, topic) -> list[BlackboardEntry]
# 按 topic 全量查询（不依赖订阅）
```

### 6.2 Phase 1 存储

- 运行时：内存字典 `{session_id: {topic: [entries]}}`
- 持久化：追加写入 `data/blackboard/{session_id}/{topic}.jsonl`
- 重启恢复：读取 `.jsonl` 文件重建内存状态

### 6.3 Phase 2 延后

- pgvector embedding 计算
- LLM 驱动的跨 topic 整合（生成 `_digest`）
- `consolidation_threshold` 触发逻辑
- `is_superseded` 标记

---

## 7. LLM 适配层

### 7.1 架构

```
Agent.call_llm(LLMRequest)
        ↓
  LLMClient（统一门面）
        ↓
  BaseAdapter（抽象适配器接口）
  ├── OpenAIAdapter    →  OpenAI Chat Completions API
  ├── AnthropicAdapter →  Anthropic Messages API
  └── MockAdapter      →  测试/离线用途
        ↓
  HttpxTransport（基于 httpx）
```

**注册与发现**：

```
LLMRegistry（全局单例）
  └── ProviderRegistry
        ├── register(name, style, adapter_instance)
        └── get(name) → BaseAdapter
```

### 7.2 核心数据结构

**LLMRequest**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| model | string | LLMRegistry 中注册的 name |
| messages | LLMMessage[] | 对话消息列表（role + content） |
| system_prompt | string \| None | 系统提示，适配层自动注入供应商正确位置 |
| tools | LLMTool[] | 可用工具列表，适配层转换为供应商格式 |
| temperature | float | 采样温度 |
| max_tokens | int | 最大输出 token 数 |

**ParsedResponse**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| text | string \| None | 纯文本输出（无 tool call 时） |
| tool_calls | ToolCallBlock[] | 工具调用列表（id / name / arguments） |
| usage | LLMUsage | prompt_tokens / completion_tokens / total_tokens |

### 7.3 供应商兼容性

| 能力 | OpenAI | Anthropic |
| --- | --- | --- |
| system_prompt | 插入为 `system` role 消息 | 使用独立 `system` 字段 |
| Tool Calling | `tool_calls` + `tool` role | `tool_use` + `tool_result` |
| max_tokens | 可选 | 必填（适配层默认 4096） |

### 7.4 Tool Calling 流程

1. Agent 将 `tool_list` 转换为 `LLMTool[]`（含 `InputSchema`）
2. `LLMClient.complete(request)` 将 tools 传入 Adapter
3. Adapter 转换为供应商格式：OpenAI `function` / Anthropic `tools`
4. 解析响应中的 `ToolCallBlock`，返回给 Agent
5. Agent 调用 `ToolGateway.call()` 执行工具
6. 将工具结果追加为 `tool` role 消息，继续对话

---

## 8. REST API（Phase 1）

### 8.1 通用约定

- Base URL：`/api/v1`
- `Content-Type`：`application/json; charset=utf-8`
- 时间格式：ISO-8601 UTC，如 `2026-03-09T09:00:00Z`
- 分页：`page=1`、`page_size=20`

统一返回格式：

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "req_01H..."
}
```

错误格式：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "goal is required",
  "request_id": "req_01H...",
  "details": { "field": "goal" }
}
```

### 8.2 Session 接口

**`POST /api/v1/sessions`** — 创建会话并启动

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| goal | string | 是 | 会话目标（注入 root-agent system_prompt） |
| template_id | string | 是 | AgentTemplate id |
| token_budget | int | 否 | token 上限，默认 200000 |
| root_max_turns | int | 否 | Loop 轮次上限，默认 20 |

返回（`202`）：`data.session = SessionObject`

**`GET /api/v1/sessions/{session_id}`** — 查询会话状态

返回（`200`）：`data.session` + `data.guard_status`（token_used / turns_used / failure_counter）

**`POST /api/v1/sessions/{session_id}/cancel`** — 取消会话

**`GET /api/v1/sessions/{session_id}/events`** — 拉取事件流（分页，`cursor` + `limit`）

### 8.3 Task 接口

**`GET /api/v1/sessions/{session_id}/tasks`** — 任务列表（分页）

**`GET /api/v1/tasks/{task_id}`** — 任务详情

**`GET /api/v1/tasks/{task_id}/context`** — 获取拼装后的完整上下文

### 8.4 Memory 接口

**`POST /api/v1/tasks/{task_id}/messages`** — 追加消息

请求体：`role`（必填）、`content`（必填）、`session_id`（选填）

**`GET /api/v1/tasks/{task_id}/memories`** — 检索记忆

查询参数：`type`（message/summary/fact）、`top_k`（默认 10）

### 8.5 Tool 审计接口

**`GET /api/v1/sessions/{session_id}/tool-calls`** — 工具调用列表

**`GET /api/v1/tool-calls/{tool_call_id}`** — 调用明细（脱敏后）

### 8.6 LLM Provider 管理接口

**`POST /api/v1/llms`** — 注册 LLM 供应商

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| name | string | 是 | 唯一名称（如 `gpt-4o`） |
| style | string | 是 | `openai` / `anthropic` |
| api_key | string | 是 | 鉴权密钥（不回传） |
| base_url | string | 否 | 自定义 API 地址 |
| model | string | 否 | 默认模型名称 |
| timeout_sec | int | 否 | 超时秒数，默认 60 |

返回（`201`）：配置信息（不含 api_key）

**`GET /api/v1/llms`** — 列举已注册供应商

**`DELETE /api/v1/llms/{name}`** — 移除供应商

### 8.7 AgentTemplate 接口

**`POST /api/v1/agent-templates`** — 创建模板

**`GET /api/v1/agent-templates`** — 列举模板

**`GET /api/v1/agent-templates/{template_id}`** — 获取模板详情

### 8.8 状态码

| HTTP | 含义 |
| --- | --- |
| 200 | 查询成功 |
| 201 | 创建成功 |
| 202 | 已接收（异步执行） |
| 400 | 参数错误 |
| 404 | 资源不存在 |
| 409 | 状态冲突 |
| 500 | 内部错误 |

业务错误码：`INVALID_ARGUMENT` / `NOT_FOUND` / `INVALID_SESSION_STATE` / `TOOL_CALL_FAILED` / `TOKEN_BUDGET_EXCEEDED` / `LLM_PROVIDER_NOT_FOUND` / `INTERNAL_ERROR`

---

## 9. 事件类型（Phase 1）

| 事件类型 | 触发时机 |
| --- | --- |
| `SESSION_CREATED` | 会话创建成功 |
| `SESSION_STATUS_CHANGED` | 状态变更 |
| `TOKEN_BUDGET_WARNING` | `token_used >= token_budget × 90%` |
| `TOKEN_BUDGET_EXCEEDED` | token 耗尽，session 终止 |
| `MAX_TURNS_REACHED` | `turns_used >= root_max_turns` |
| `FAILURE_THRESHOLD_REACHED` | `failure_counter >= failure_threshold`（Phase 1 仅记录） |
| `TASK_STARTED` | Task 进入 ACTIVE |
| `TASK_FINISHED` | Task 完成 |
| `TASK_FAILED` | Task 失败 |
| `TOOL_CALL_STARTED` | 工具调用开始 |
| `TOOL_CALL_FINISHED` | 工具调用完成 |
| `MEMORY_SUMMARIZED` | 消息数达到阈值，触发滚动摘要 |
| `BLACKBOARD_ENTRY_PUBLISHED` | `blackboard.publish()` 调用后 |
| `AGENT_LOOP_STARTED` | Agent Loop 启动 |
| `AGENT_LOOP_FINISHED` | Agent Loop 完成 |

> Phase 2 补充：`SESSION_PAUSED_HITL`、`TASK_SUSPENDED / RESUMED`、`AGENT_WAITING / RESUME`、`SPAWN_*`、`HITL_*`、`BLACKBOARD_CONSOLIDATION_*`

---

## 10. 非功能设计（Phase 1）

### 10.1 日志

结构化 JSON 日志，最小字段集：

```json
{
  "timestamp": "2026-03-26T10:00:00Z",
  "level": "INFO",
  "request_id": "req_01H...",
  "session_id": "ses_01H...",
  "task_id": "tsk_01H...",
  "event": "TASK_FINISHED",
  "error_code": null
}
```

### 10.2 安全（Phase 1 简化版）

- 工具调用输入输出强制脱敏（`api_key` / `password` / `token` 替换为 `***`）
- bash_exec 命令黑名单
- 基础 SSRF 防护（拦截 localhost / 内网地址）

### 10.3 初始 SLO

- 会话创建成功率 >= 99.9%
- API p95 延迟 < 300ms（不含 LLM 调用）

---

## 11. 技术选型

| 层次 | Phase 1 | Phase 2+ |
| --- | --- | --- |
| 语言 | Python 3.11+ | — |
| API 框架 | FastAPI + Uvicorn | WebSocket + SSE |
| 存储 | 本地 JSON 文件（`data/` 目录） | PostgreSQL 16 + pgvector |
| 缓存/队列 | 内存（asyncio.Queue） | Redis 7 + Redis Streams |
| ORM | 无 | SQLAlchemy 2.0 + Alembic |
| HTTP 客户端 | httpx | httpx（async） |
| Observability | 结构化 JSON 日志 | OpenTelemetry + Prometheus + Grafana |
| 容器化 | 无 | Docker + Docker Compose |

---

## 12. 核心设计原则

- **清晰优先于完备**：Phase 1 先跑通核心 Loop，Guard 机制先于 Observability 落地
- **职责分离**：root-agent 只管 Plan/Execute；Session 管 Guard；ToolGateway 管授权与审计
- **安全内置而非外挂**：token_budget 在 Loop 最内层检查，不可绕过
- **协议对齐降低接入成本**：Tool 走 MCP、LLM 适配走统一接口，便于后续扩展
- **状态可恢复**：所有状态写入文件，进程重启后可从文件恢复

---

## 13. 完整设计参考

完整系统设计（含数据库 Schema、动态多层 Agent 树、HITL、Blackboard 整合、全量事件类型等）见：[docs/miniAgents_design_FINAL.docx](docs/miniAgents_design_FINAL.docx)
