# miniAgents 设计文档（V1）

## 1. 项目目标与范围

`miniAgents` 是一个 Agent 后端服务，核心能力包括：

1. 以Agent为维度的 Memory 管理  
2. 以会话（Session）为维度的 Task + Agent 调度管理  
3. 内置工具执行能力（Bash 脚本执行、HTTP API 调用等）

本版本目标：优先构建稳定、可扩展、可观测的后端基础设施demo，先支持单租户，后续平滑扩展为多租户/分布式部署。

---

## 2. 总体架构

### 2.1 架构分层

1. **API 层（Gateway/API Service）**  
   对外提供 REST/WS 接口，负责鉴权、参数校验、限流、请求编排。

2. **会话与任务编排层（Orchestrator）**  
   管理 Session 生命周期、Task 状态机、Agent 调度策略、重试与超时控制。

3. **执行层（Worker Runtime）**  
   负责执行具体任务步骤（调用 LLM、工具调用、脚本执行），并回写结果。

4. **LLM适配层**
   负责适配不同供应商的大模型

5. **存储层（Storage）**  
   - PostgreSQL：事务数据（会话、任务、事件、工具调用记录）  
   - Redis：缓存、分布式锁、队列、短期上下文  
   - 对象存储（可选）：大文本、日志快照、附件

6. **观测与治理层（Observability）**  
   指标（Prometheus）、日志（结构化 JSON）、链路追踪（OpenTelemetry）。

### 2.2 逻辑组件

- **Session Manager**：创建/恢复/终止会话，维护会话状态机与超时。  
- **Task Manager**：创建 Task、维护任务队列、提供任务级幂等与优先级。  
- **Agent Registry**：管理 Agent 模板与实例（system_prompt、tools、skills、memory 配置）。  
- **Agent Runtime**: 管理 Agent 运行，包含完整的Agent loop
    Observe
    Plan
    CreateTask
    Schedule
    Execute
    UpdateMemory
- **Task Executor**：按 Task 类型执行单个任务（reasoning/tool-call/sub-agent/skill）。  
- **Memory Service**：消息写入、摘要更新、检索拼装、TTL 管控。  
- **Tool Gateway**：统一 Tools 调用入口，鉴权、审计、限流、脱敏。  
- **Skill Router**：匹配/加载技能，生成执行计划与步骤模板。  
- **Event Bus**：会话事件、工具调用事件、状态变更事件的发布订阅。  
- **Artifact Store**：大文本/文件/报告产物存储与引用。


---

## 3. 核心领域模型

### 3.1 Task

- **Task**：一次任务/协作的 资源 容器
- **id/title/priority/status**：任务主键、标题、优先级、状态  
- **creator_agent**: 创建任务的 agent 指针  
- **description**：任务描述  
- **type**：`reasoning` / `tool-call` / `sub-agent` / `skill`  
- **assigned_agent**：执行该 Task 的 agent（可为空，默认继承会话 root-agent）  
- **inputs/outputs/result**：任务输入输出与结果（结构化，可引用产物）  
- **executor_type**：任务执行器类型（与 `type` 绑定）  
- **blackboard**: 共享记忆机制。Task 可以通过发布到 blackboard 与其他 agent 共享记忆
- **task_executor**：按 Task 类型执行单个任务；并发/DAG 由 Agent Loop 管理  
- **status**：记录当前task处理状态

**Task 状态建议**：`PENDING` → `ACTIVE` → `FINISHED` / `FAILED` / `CANCELED`  
**Task 重试字段建议**：`retry_count`, `max_retries`, `timeout_ms`, `error`


### 3.2 Agent、Task 与 Memory

- **Agent**：上下文管理容器，每个节点持有：
  - `system_prompt`
  - `memory_item`
  - `tool_list`
  - `skill_list`
  - `active_task`
- **active_task**：当前Agent需要处理的任务
- **session**：所属的会话
- **Memory Item**：会话内可检索记忆单元，支持多类型：
  - `message`：用户/助手消息
  - `summary`：压缩摘要
  - `fact`：结构化事实
- **ArtifactStore**: 外部知识库

**Agent 核心职责**（面向“某段上下文”的管理与执行）：
- **Memory 管理**：读写消息、摘要与 facts；控制上下文长度与预算  
- **Skill 管理**：匹配/加载技能；从技能生成可执行计划  
- **Tool 管理**：工具白名单/权限校验；统一调用与审计  
- **外部知识管理**：检索 Artifact/知识库并注入上下文  
- **Prompt 组织**：系统提示 + 任务目标 + 记忆 + 外部知识的拼装  
- **LLM 调用**：统一请求结构、模型选择与超时控制  
- **Response 解析**：解析模型输出，识别工具调用/结构化结果  
- **Tool 调用编排**：触发工具、回写结果、更新 memory/blackboard

**Agent 主要接口（建议）**：
- `build_context(session_id, task_id)`: 组装 prompt 上下文  
- `select_skills(context)`: 技能匹配  
- `plan(context)`: 输出执行计划/任务列表  
- `call_llm(request)`: 调用 LLM  
- `parse_response(response)`: 解析 LLM 返回  
- `run_tools(calls)`: 执行工具并回写结果  


### 3.3 Session

- **session**：一次目标导向的LLM会话
- **task_queue**：由 agents 动态管理，允许在会话内增删任务
- **agents**: agents[0]为root-agent
- **runtimeSummary**：一次执行实例统计（含重试次数、运行时token成本、工具调用统计等）

**Session 状态建议**：`QUEUED` → `RUNNING` → `SUCCEEDED` / `FAILED` / `TIMEOUT` / `CANCELED` / `RETRYING`

### 3.4 Tool 调用

- **ToolSpec**：工具定义（名称、入参 schema、权限）  
- **ToolCall**：一次工具调用记录（输入、输出、耗时、状态、错误）

**ToolCall 状态建议**：`PENDING` / `RUNNING` / `SUCCEEDED` / `FAILED`

### 3.5 Blackboard（共享记忆）

- **Blackboard Item**：`id`, `task_id`, `scope`(task/session/agent), `type`(note/fact/plan/artifact_ref)  
- **可见性**：默认 task 级可见，按 `scope` 控制  
- **生命周期**：可配置 TTL 或永久保留  
- **引用**：支持 tasks/agents 通过 `item_id` 引用与更新

---

## 4. 模块设计

## 4.1 以Agent为维度的 Memory 管理

### 4.1.1 目标

- 提供长短期记忆能力，控制上下文长度与成本  
- 保证任务级隔离（不同 Agent memory 不串扰）  
- 支持按语义检索与时间检索

### 4.1.2 Memory 分层

1. **短期记忆（Short-term Context）**  
   最近 N 轮消息 + 当前任务相关步骤，放 Redis + DB，低延迟读取。

2. **长期记忆（Long-term Memory）**  
   历史事实、摘要、关键结论存 PostgreSQL；可选向量索引（pgvector）。

3. **压缩摘要（Rolling Summary）**  
   每达到阈值（如 20 条消息或 token 超限）触发摘要更新，减少上下文膨胀。

### 4.1.3 关键流程

1. 写入消息：`append_message(task_id, role, content)`  
2. 判断阈值：超过阈值触发 `summarize_memory`  
3. 查询上下文：`build_prompt_context(task_id, session_id)`  
   - 最近消息窗口
   - 某次对话的结果
   - 语义检索出的历史 facts/summaries

### 4.1.4 数据表建议
建议最小表集（V1）：

- `tasks`：任务主表  
  字段：`id`, `user_id`, `title`, `description`, `type`, `assigned_agent_id`, `executor_type`, `inputs`, `outputs`, `result`, `error`, `priority`, `status`, `retry_count`, `max_retries`, `timeout_ms`, `metadata`, `created_at`, `updated_at`
- `session_task_queue`：会话任务队列  
  字段：`id`, `session_id`, `task_id`, `status`, `enqueue_at`, `dequeue_at`
- `task_edges`：Task DAG 依赖边  
  字段：`id`, `session_id`, `from_task_id`, `to_task_id`
- `agents`：Agent 节点  
  字段：`id`, `task_id`, `node_key`, `name`, `model`, `system_prompt`, `tool_list`, `skill_list`, `memory_config`, `status`, `created_at`, `updated_at`
- `sessions`：会话主表  
  字段：`id`, `task_id`, `type`, `goal`, `status`, `priority`, `deadline_at`, `last_error_code`, `created_at`, `updated_at`, `finished_at`
- `session_events`：事件流水  
  字段：`id`, `session_id`, `event_type`, `payload`, `occurred_at`
- `messages`：消息流水  
  字段：`id`, `task_id`, `session_id`, `role`, `content`, `metadata`, `token_count`, `seq_no`, `created_at`
- `memories`：摘要/事实/检索项  
  字段：`id`, `task_id`, `type`, `content`, `embedding`, `source_message_id`, `created_at`
- `blackboard_items`：共享记忆  
  字段：`id`, `task_id`, `scope`, `type`, `content`, `created_by`, `created_at`, `expired_at`
- `tool_specs`：工具定义缓存  
  字段：`id`, `tool_name`, `input_schema`, `annotations`, `updated_at`
- `tool_calls`：工具调用审计  
  字段：`id`, `session_id`, `task_id`, `tool_name`, `input_redacted`, `output_redacted`, `status`, `latency_ms`, `error`, `created_at`
- `artifacts`：外部产物引用  
  字段：`id`, `task_id`, `type`, `uri`, `metadata`, `created_at`


### 4.1.5 Memory 策略

- **优先级拼装**：系统提示 > 当前任务 > 最近消息 > 关键历史记忆  
- **Token 预算**：按模型设置 max_context，预留工具返回空间  
- **TTL 策略**：低价值临时记忆可过期；高价值 fact 永久保留  
- **冲突处理**：同类 fact 冲突时保留多版本并打时间戳

---

## 4.2 以会话为维度的 Agent 调度管理

### 4.2.1 目标

- 提供可恢复、可重试、可并行的单次会话发起和接收框架  
- 支持任务优先级、超时控制、取消、幂等提交  
- 让 Agent 运行可观测、可审计

### 4.2.2 调度核心逻辑（建议）

1. **入队与幂等**：  
   `POST /tasks/{task_id}/sessions` 通过 `Idempotency-Key` 去重，避免重复入队。
2. **就绪判断**：  
   Agent Loop 在会话内构建 Task DAG，仅调度依赖满足的 Task。
3. **并发与隔离**：  
   按 `task_id` 或 `agent_type` 设并发上限，避免资源争用。
4. **失败传播**：  
   Task `FAILED` 后，默认阻断其所有后继；可配置 `fail_fast=false` 允许继续。
5. **超时处理**：  
   Task 超时 → `FAILED`，会话根据策略转 `RETRYING` 或 `FAILED`。
6. **取消传播**：  
   取消会话会级联取消当前运行 Task 与待运行 Task。

### 4.2.3 重试与回滚策略（建议）

- **重试策略**：`max_retries`, `backoff`（固定/指数），仅对可重试错误生效。  
- **幂等要求**：重复执行步骤必须可重放，输出写入采用幂等写。  
- **补偿动作**：对有副作用的工具调用，记录 `compensation`（如删除/撤回）供失败后执行。

### 4.2.4 会话事件（建议）

事件类型示例：
- `SESSION_CREATED`, `SESSION_STATUS_CHANGED`
- `TASK_STARTED`, `TASK_FINISHED`
- `TOOL_CALL_STARTED`, `TOOL_CALL_FINISHED`
- `MEMORY_SUMMARIZED`, `BLACKBOARD_UPDATED`


## 4.3 Agent Loop Runtime

### 4.3.1 目标

- 统一 Agent 在会话内的运行循环，保证可控、可观测、可重试  
- 清晰划分“思考/规划/执行/记忆更新”的责任边界  
- 把外部副作用收敛到 Tool 调用，便于审计与回放

### 4.3.2 Loop 阶段定义

- **Observe**：拉取 `session` 状态、最新消息、黑板与检索记忆，构建上下文  
- **Plan**：生成本轮意图与下一步行动计划（可为空）  
- **CreateTask**：如需拆解，在会话 `task_queue` 中新增 Task 并构建依赖  
- **Schedule**：将可运行 Task 入队（并发与依赖在 Agent Loop 内管理）  
- **Execute**：等待队列中所有 Task（LLM 推理、Tool 调用、子 Agent）执行完成
- **UpdateMemory**：写入消息、产物、摘要与黑板

### 4.3.3 输入与输出

**输入**：
- `session_id`, `task_id`, 当前 `agent_id`  
- 上下文：`messages`, `summaries`, `facts`, `blackboard_items`  
- 可用能力：`tool_list`, `skill_list`, `policy`

**输出**：
- `tasks` 变更（新增/更新/完成）  
- `tool_calls` 与 `artifacts`  
- `session_events` 与 `messages`  
- `memories` 与 `blackboard_items` 更新

### 4.3.4 关键约束

- **幂等**：同一轮 Loop 必须可重放，输出写入需具备幂等键  
- **副作用隔离**：外部动作必须通过 Tool，禁止直接执行  
- **可中断**：随时可被取消/超时，需保存可恢复状态  
- **权限**：所有 Tool 调用通过 `policy_engine` 授权

### 4.3.5 运行时流程（参考）

1. `build_prompt_context(session_id, task_id)`  
2. `select_skills(context)` → 生成本轮计划  
3. `create_tasks(plan)`  
4. `schedule_ready_tasks(session_id)`  
5. `execute_task(task_id)`  
6. `write_back(task_result)` → `messages/tool_calls/artifacts`  
7. `update_memory(session_id, task_id)`  
8. 产出事件：`SESSION_STATUS_CHANGED/TASK_FINISHED`

### 4.3.6 最小状态机

- **Loop 状态**：`IDLE` → `OBSERVING` → `PLANNING` → `SCHEDULING` → `EXECUTING` → `UPDATING` → `IDLE`  
- **失败处理**：任一阶段异常 → 记录 `error_code` → 按策略 `RETRYING`/`FAILED`



## 4.4 内置能力系统：Tools 与 Skills

### 4.4.1 设计目标与边界

- `Tools`（执行能力）严格遵循 MCP Tool 协议，负责“对外部世界做动作”。  
- `Skills`（策略能力）严格遵循 Anthropic Skill 形式，负责“沉淀可复用做事方法”。  
- 分层原则：Skill 只编排流程，不绕过 Tool 权限；Tool 只执行，不承担业务策略。

### 4.4.2 Tools 设计（严格参考 MCP）

1. 协议层  
   - 采用 MCP Server 暴露工具。  
   - 通过 `tools/list` 发现可用工具，通过 `tools/call` 执行工具。

2. Tool 描述结构  
   - `name`：工具唯一标识。  
   - `title`（可选）、`description`。  
   - `inputSchema`（JSON Schema）。  
   - `annotations`（可选），如 `readOnlyHint` 用于标记只读工具。

3. Tool 返回结构  
   - 返回 `content`（可读输出）与可选 `structuredContent`（结构化输出）。  
   - 错误场景返回 `isError=true`，并附标准错误码与可读信息。

4. 内置 MCP Tools（MVP）  
   - `bash_exec`：受限命令执行（`command`, `cwd`, `timeout_ms`, `env_whitelist`）。  
   - `http_request`：受限 HTTP 调用（`method`, `url`, `headers`, `query`, `body`, `timeout_ms`）。

5. 安全与治理  
   - `policy_engine` 按 `user_role + task_type + tool_name` 授权。  
   - `bash_exec`：命令黑名单、超时上限、输出大小上限、目录白名单。  
   - `http_request`：域名白名单/黑名单、内网访问拦截（SSRF）、响应体大小限制。  
   - 全部调用写入 `tool_calls`，且输入输出必须脱敏。

### 4.4.3 Skills 设计（严格参考 Anthropic Skill）

1. 组织形式  
   - 每个 Skill 是独立目录，主文件为 `SKILL.md`。  
   - 支持 `scripts/`、`assets/`、`references/` 等辅文件。  
   - `SKILL.md` 使用 Frontmatter（如 `name`、`description`）+ 正文指令。

2. 触发与加载  
   - 支持显式触发（用户指定 skill）和隐式触发（基于 description 匹配）。  
   - 运行时按需加载 Skill，不做全量注入，降低上下文噪声。

3. 执行语义  
   - Skill 定义流程、检查单、工具调用建议。  
   - 实际执行动作必须通过 MCP Tools 完成，Skill 不能直接越权执行命令/API。  
   - Skill 目录内相对路径按 Skill 根目录解析，确保可移植。

4. 可审计性  
   - 会话内记录 skill 命中、skill 版本、触发原因、执行步骤和调用的 tools。  
   - 审计事件进入 `session_events`，便于回放与问题定位。

### 4.4.4 Tools 与 Skills 协作流程

1. 接收任务并构建上下文。  
2. Skill Router 匹配 0~N 个 Skills。  
3. Agent 基于 Skills 生成执行计划。  
4. 外部动作全部走 MCP `tools/call`。  
5. 结果回写 `tasks`、`tool_calls`、`session_events`，并更新 Memory。

### 4.4.5 兼容性建议

- Tool 接口保持 MCP 兼容，便于未来接入第三方 MCP Server。  
- Skill 文件结构保持 Anthropic 风格，便于迁移和复用。  
- 新增 Tool 前必须先定义 `inputSchema`、权限策略和审计字段。

### 4.4.6 参考规范

- MCP Tools：<https://modelcontextprotocol.io/specification/2025-06-18/server/tools>  
- MCP `tools/call`：<https://modelcontextprotocol.io/specification/2025-06-18/server/tools#calling-tools>  
- Anthropic Skills（Claude Code）：<https://docs.claude.com/en/docs/claude-code/skills>  
- Anthropic Skill Frontmatter：<https://docs.claude.com/en/docs/claude-code/tutorials/skill-library>

---

## 5. API 设计（REST 草案）

### 5.1 通用约定

- Base URL：`/api/v1`
- 鉴权：`Authorization: Bearer <token>`
- 幂等：写接口支持 `Idempotency-Key`（推荐 UUID）
- 追踪：`X-Request-Id` 透传到日志/trace
- `Content-Type`：`application/json; charset=utf-8`
- 分页：默认 `page=1`、`page_size=20`、`page_size<=100`
- 时间格式：统一 `ISO-8601`（UTC），如 `2026-03-09T09:00:00Z`

统一返回格式：

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "req_01H..."
}
```

错误返回格式：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "priority must be between 0 and 10",
  "request_id": "req_01H...",
  "details": {
    "field": "priority"
  }
}
```

### 5.2 公共数据结构

`TaskObject`

```json
{
  "id": "tsk_01H...",
  "user_id": "usr_01H...",
  "title": "调研 MCP 与 Skills 设计",
  "description": "输出一版设计文档",
  "type": "reasoning",
  "assigned_agent_id": "agn_01H...",
  "executor_type": "reasoning",
  "inputs": {},
  "outputs": {},
  "result": null,
  "error": null,
  "status": "ACTIVE",
  "priority": 5,
  "metadata": {},
  "created_at": "2026-03-09T09:00:00Z",
  "updated_at": "2026-03-09T09:00:00Z"
}
```

`SessionObject`

```json
{
  "id": "ses_01H...",
  "task_id": "tsk_01H...",
  "type": "chat",
  "goal": "补齐接口文档",
  "status": "QUEUED",
  "priority": 5,
  "deadline_at": "2026-03-09T10:00:00Z",
  "created_at": "2026-03-09T09:10:00Z",
  "updated_at": "2026-03-09T09:10:00Z",
  "finished_at": null
}
```

`AgentNodeObject`

```json
{
  "id": "agn_01H...",
  "task_id": "tsk_01H...",
  "node_key": "planner",
  "name": "planner-agent",
  "model": "gpt-4.1-mini",
  "system_prompt": "You are a task-focused coding agent...",
  "tool_list": ["bash_exec", "http_request"],
  "skill_list": ["code-review", "doc-polish"],
  "memory_config": {
    "recent_message_window": 20,
    "summary_threshold": 20,
    "retrieval_top_k": 8
  },
  "status": "PENDING",
  "created_at": "2026-03-09T09:00:00Z",
  "updated_at": "2026-03-09T09:00:00Z"
}
```

`AgentDagObject`

```json
{
  "task_id": "tsk_01H...",
  "nodes": [
    {"id": "agn_01H...", "node_key": "planner"},
    {"id": "agn_02H...", "node_key": "coder"},
    {"id": "agn_03H...", "node_key": "reviewer"}
  ],
  "edges": [
    {"from": "agn_01H...", "to": "agn_02H..."},
    {"from": "agn_02H...", "to": "agn_03H..."}
  ]
}
```

`TaskExecutorObject`

```json
{
  "id": "tex_01H...",
  "task_id": "tsk_01H...",
  "status": "RUNNING",
  "assigned_agent_id": "agn_01H...",
  "last_error_code": null,
  "started_at": "2026-03-09T09:10:00Z",
  "finished_at": null,
  "updated_at": "2026-03-09T09:12:00Z"
}
```

`PaginationObject`

```json
{
  "page": 1,
  "page_size": 20,
  "total": 135
}
```

### 5.3 Task 接口

1. `POST /tasks` 创建任务

请求体：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| title | string | 是 | 任务标题（1~200） |
| description | string | 否 | 任务描述 |
| priority | integer | 否 | 优先级（0~10，默认 5） |
| metadata | object | 否 | 扩展字段 |

成功返回（`201`）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "task": {
      "id": "tsk_01H...",
      "title": "调研 MCP 与 Skills 设计",
      "status": "ACTIVE",
      "priority": 5,
      "created_at": "2026-03-09T09:00:00Z",
      "updated_at": "2026-03-09T09:00:00Z"
    }
  },
  "request_id": "req_01H..."
}
```

2. `GET /tasks/{task_id}` 查询任务详情

路径参数：

| 字段 | 类型 | 描述 |
| --- | --- | --- |
| task_id | string | 任务 ID |

成功返回（`200`）：`data.task = TaskObject`

3. `GET /tasks` 查询任务列表

查询参数：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| status | string | 否 | 按状态过滤 |
| page | integer | 否 | 页码 |
| page_size | integer | 否 | 每页条数 |

成功返回（`200`）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "items": [],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 0
    }
  },
  "request_id": "req_01H..."
}
```

4. `GET /tasks/{task_id}/agents` 查询任务下 Agent 节点列表

路径参数：

| 字段 | 类型 | 描述 |
| --- | --- | --- |
| task_id | string | 任务 ID |

成功返回（`200`）：`data.items = AgentNodeObject[]`

5. `GET /tasks/{task_id}/agent-dag` 查询任务 Agent DAG

路径参数：

| 字段 | 类型 | 描述 |
| --- | --- | --- |
| task_id | string | 任务 ID |

成功返回（`200`）：`data.dag = AgentDagObject`

6. `GET /tasks/{task_id}/executor` 查询 Task Executor 运行状态

路径参数：

| 字段 | 类型 | 描述 |
| --- | --- | --- |
| task_id | string | 任务 ID |

成功返回（`200`）：`data.executor = TaskExecutorObject`

### 5.4 Session 接口

1. `POST /tasks/{task_id}/sessions` 创建会话并入队

请求头：
- `Idempotency-Key`：建议必传

请求体：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| type | string | 是 | `chat`/`plan`/`exec` |
| goal | string | 是 | 会话目标 |
| priority | integer | 否 | 会话优先级（0~10） |
| deadline_at | string(datetime) | 否 | 截止时间 |

成功返回（`202`）：`data.session = SessionObject`

2. `GET /sessions/{session_id}` 查询会话状态与摘要

成功返回（`200`）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "session": {
      "id": "ses_01H...",
      "status": "RUNNING",
      "goal": "补齐接口文档",
      "last_error_code": null,
      "updated_at": "2026-03-09T09:20:00Z"
    },
    "summary": {
      "latest_summary": "已完成 5 个接口补全"
    }
  },
  "request_id": "req_01H..."
}
```

3. `POST /sessions/{session_id}/cancel` 取消会话

请求体：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| reason | string | 否 | 取消原因 |

成功返回（`200`）：`data.session.status = CANCELED`

4. `POST /sessions/{session_id}/retry` 手动重试（仅 `FAILED/TIMEOUT`）

请求体：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| reason | string | 否 | 重试原因 |
| reset_to_task | string | 否 | 从指定任务重新执行 |

成功返回（`202`）：`data.session.status = RETRYING`

5. `GET /sessions/{session_id}/events` 拉取事件流（分页）

查询参数：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| cursor | string | 否 | 游标（建议用最后一个 event_id） |
| limit | integer | 否 | 返回条数（默认 50，最大 200） |

成功返回（`200`）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "items": [
      {
        "id": "evt_01H...",
        "event_type": "SESSION_STATUS_CHANGED",
        "payload": {
          "from": "QUEUED",
          "to": "RUNNING"
        },
        "occurred_at": "2026-03-09T09:11:00Z"
      }
    ],
    "next_cursor": "evt_01H..."
  },
  "request_id": "req_01H..."
}
```

6. `GET /sessions/{session_id}/stream` SSE 推送（可选）

响应头：`Content-Type: text/event-stream`  
事件格式：

```text
event: session.event
id: evt_01H...
data: {"event_type":"SESSION_STATUS_CHANGED","payload":{"from":"QUEUED","to":"RUNNING"}}
```

### 5.5 Memory 接口

1. `POST /tasks/{task_id}/messages` 追加消息

请求体：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| session_id | string | 否 | 来源会话 ID |
| role | string | 是 | `user`/`assistant`/`system`/`tool` |
| content | string | 是 | 消息内容 |
| metadata | object | 否 | 附加信息 |

成功返回（`201`）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "message_id": "msg_01H...",
    "seq_no": 128,
    "token_count": 356
  },
  "request_id": "req_01H..."
}
```

2. `GET /tasks/{task_id}/context` 获取拼装后的上下文

查询参数：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| session_id | string | 否 | 会话 ID |
| max_tokens | integer | 否 | 目标上下文 token 上限 |

成功返回（`200`）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "system_prompt": "You are a helpful agent...",
    "messages": [],
    "retrieved_memories": [],
    "token_estimate": 2048
  },
  "request_id": "req_01H..."
}
```

3. `GET /tasks/{task_id}/memories` 检索记忆

查询参数：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| type | string | 否 | `message/summary/fact/artifact_ref` |
| query | string | 否 | 语义检索查询词 |
| top_k | integer | 否 | 返回条数（默认 10，最大 50） |

成功返回（`200`）：`data.items = task_memories 列表`

### 5.6 Tool 审计接口

1. `GET /sessions/{session_id}/tool-calls` 工具调用列表

查询参数：

| 字段 | 类型 | 必填 | 描述 |
| --- | --- | --- | --- |
| page | integer | 否 | 页码 |
| page_size | integer | 否 | 每页条数 |

成功返回（`200`）：`data.items = tool_calls 列表，data.pagination = PaginationObject`

2. `GET /tool-calls/{tool_call_id}` 工具调用明细（脱敏后）

成功返回（`200`）：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "tool_call": {
      "id": "tlc_01H...",
      "tool_name": "http_request",
      "input_redacted": {},
      "output_redacted": {},
      "status": "SUCCEEDED",
      "latency_ms": 182
    }
  },
  "request_id": "req_01H..."
}
```

### 5.7 状态码建议

- `200` 查询成功
- `201` 创建成功
- `202` 已接收（异步执行）
- `400` 参数错误
- `401/403` 未授权或无权限
- `404` 资源不存在
- `409` 幂等冲突/状态冲突
- `429` 频率限制
- `500` 内部错误

业务错误码建议：

- `INVALID_ARGUMENT` 参数非法
- `UNAUTHORIZED` 未鉴权
- `FORBIDDEN` 无权限
- `NOT_FOUND` 资源不存在
- `IDEMPOTENCY_CONFLICT` 幂等键冲突
- `INVALID_SESSION_STATE` 会话状态不允许当前操作
- `TOOL_CALL_FAILED` 工具调用失败
- `INTERNAL_ERROR` 服务内部异常


## 6. 非功能设计

### 6.1 安全

- 鉴权：JWT/API Key（服务间建议 mTLS）  
- 鉴权后细粒度授权（RBAC/ABAC）  
- 输入校验：JSON Schema + 长度限制 + 字符过滤  
- 防注入：命令、SQL、模板注入防护  
- 敏感信息脱敏与加密存储（KMS）

### 6.2 可观测性与 SLO

- 指标：
  - API：`qps`, `p95/p99 latency`, `5xx rate`
  - Worker：`queue_depth`, `success_rate`, `retry_rate`, `timeout_rate`
  - Tool：`tool_call_count`, `tool_error_rate`, `tool_latency_p95`
  - LLM：`prompt_tokens`, `completion_tokens`, `cost_per_session`
- 日志：结构化 JSON，最小字段集合  
  `timestamp`, `level`, `request_id`, `task_id`, `session_id`, `event`, `error_code`
- 链路追踪：API -> Orchestrator -> Worker -> Tool 全链路 trace
- 初始 SLO（按季度复盘）：
  - 会话创建成功率 >= 99.9%
  - API p95 延迟 < 300ms（不含长轮询/SSE）
  - 异步任务最终完成率 >= 99%


### 6.3 性能与容量（初始估算）

- 单实例 API：500~1000 RPS（轻请求）  
- Worker 并发：按 CPU/IO 类型隔离队列  
- 关键优化：
  - Memory 查询索引化（`session_id, created_at`）
  - 热会话缓存
  - 批量写事件日志

### 6.4 高可用

- API/Worker 无状态，支持水平扩展  
- Redis/PostgreSQL 主从与备份恢复  
- 任务重放机制：Worker 异常退出后可恢复运行

---

## 7. 参考技术选型

- 推荐主栈（V1）：
  - 语言：Python
  - API 框架：FastAPI
  - ORM：SQLAlchemy + Alembic
  - DB：PostgreSQL（可选 pgvector）
  - Queue/Event：Redis Streams（可配 Celery/Arq，后续可演进到 Kafka）
  - Observability：OpenTelemetry + Prometheus + Grafana
- 可替代栈（若团队偏工程化分层）：
  - Django + Django Ninja/FastAPI（其余组件保持一致）

---

