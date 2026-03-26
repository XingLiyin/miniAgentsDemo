# miniAgents 模块结构与编码指南（Phase 1）

本文档指导 `miniAgents` Phase 1 的模块划分与编码落地。
Phase 1 目标：**跑通最小可运行的单 Agent Loop，存储用本地 JSON 文件替代数据库。**
完整系统设计见 [miniAgents_design_FINAL.docx](miniAgents_design_FINAL.docx)。

---

## 1. 模块职责分层

| 层 | 模块 | Phase 1 职责 |
| --- | --- | --- |
| API | `app/api` | REST 接口、参数校验、统一响应格式 |
| Orchestrator | `app/orchestrator` | Session / Task 状态机；Guard 检查；内联调度 |
| Runtime | `app/runtime` | Agent Loop；Task 执行；Tool 调用 |
| Domain | `app/domain` | 领域模型、领域服务、事件定义 |
| Storage | `app/storage/file` | JSON 文件读写（替代 DB/Redis） |
| LLM | `app/llm` | ✅ 已完成：统一 LLMClient、OpenAI/Anthropic 适配 |
| Observability | `app/observability` | 结构化 JSON 日志（Phase 1 只做日志） |
| Config | `app/config` | 全局配置单例 |
| Common | `app/common` | 错误码、工具函数 |

---

## 2. 目录结构（Phase 1）

```
miniAgents/
  data/                          # 运行时数据目录（.gitignore）
    sessions/                    # {session_id}.json
    tasks/                       # {task_id}.json
    agents/                      # {agent_id}.json
    memory/
      {session_id}/
        messages.jsonl           # 追加写
        summaries.json
    blackboard/
      {session_id}/
        {topic}.jsonl            # 追加写
    tool_calls/
      {session_id}.jsonl         # 追加写，审计日志

  app/
    main.py                      # FastAPI 应用入口

    config/
      settings.py                # 全局配置单例（Pydantic Settings）

    api/
      v1/
        router.py                # 聚合所有路由
        deps.py                  # 依赖注入（获取 service 实例）
        routes/
          sessions.py            # POST/GET /sessions, POST /sessions/{id}/cancel
          tasks.py               # GET /tasks/{id}, GET /sessions/{id}/tasks
          memories.py            # POST/GET /tasks/{id}/messages, /memories
          tools.py               # GET /sessions/{id}/tool-calls, /tool-calls/{id}
          llms.py                # ✅ POST/GET/DELETE /llms
          agent_templates.py     # POST/GET /agent-templates        【新增】
        schemas/
          session.py
          task.py
          memory.py
          tool.py
          llm.py
          agent_template.py                                          【新增】

    orchestrator/
      session_manager.py         # Session 生命周期：创建/恢复/取消；Guard 检查
      task_manager.py            # Task 入队/出队；状态驱动
      state_machine.py           # Session / Task 合法状态转换校验

    runtime/
      agent_loop.py              # Agent Loop 主逻辑：Observe→Plan→CreateTask→Execute→UpdateMemory
      task_executor.py           # 按 Task.type 分发执行（reasoning / tool-call）
      tool_gateway.py            # Tool 调用：白名单授权 + 执行 + 审计脱敏
      policy_engine.py           # 工具白名单校验

    domain/
      models/
        session.py               # Session dataclass（含 Guard 字段）
        task.py                  # Task dataclass
        agent.py                 # Agent dataclass（含 loop_guard）
        agent_template.py        # AgentTemplate dataclass              【新增】
        memory.py                # MemoryItem dataclass
        blackboard.py            # BlackboardEntry dataclass
        tool_call.py             # ToolCall dataclass（审计）
      services/
        session_service.py       # Session CRUD + 状态机调用
        task_service.py          # Task CRUD + 状态机调用
        memory_service.py        # 消息追加、上下文拼装、触发摘要
        blackboard_service.py    # publish / subscribe / pull
        agent_template_service.py                                       【新增】
      events/
        event_types.py           # Phase 1 事件常量
        event_bus.py             # 内存 EventBus（publish/subscribe）

    storage/
      file/                                                             【Phase 1 存储层】
        base.py                  # 原子写工具（write_json_atomic / read_json / append_jsonl）
        session_store.py         # Session 文件读写
        task_store.py            # Task 文件读写
        agent_store.py           # Agent 文件读写
        agent_template_store.py  # AgentTemplate 文件读写
        memory_store.py          # messages.jsonl / summaries.json 读写
        blackboard_store.py      # {topic}.jsonl 读写
        tool_call_store.py       # tool_calls/{session_id}.jsonl 追加写
      db/                        # Phase 2 占位，当前不使用
      cache/                     # Phase 2 占位，当前不使用

    llm/                         # ✅ 已完成
      llm_base.py                # LLMRequest / ParsedResponse / BaseAdapter / LLMClient
      provider_registry.py       # ProviderRegistry
      registry.py                # LLMRegistry 全局单例
      openai_adapter.py          # OpenAI Chat Completions 适配
      anthropic_adapter.py       # Anthropic Messages 适配
      mock_adapter.py            # 测试用 Mock
      transport_httpx.py         # httpx HTTP 传输层

    observability/
      logging.py                 # 结构化 JSON 日志初始化（Phase 1 只做日志）
      audit.py                   # 审计事件写入（复用 tool_call_store）

    common/
      errors.py                  # AppError + 业务错误码枚举
      utils.py                   # ULID 生成、时间工具、token 估算

  tests/
  docs/
    architecture.md
    miniAgents_design_FINAL.docx
```

---

## 3. 核心模块职责

### 3.1 API 层（`app/api`）

**允许调用**：`domain/services`、`orchestrator`
**禁止调用**：`runtime`（Agent Loop 通过 orchestrator 触发）、`storage` 直接访问

职责：
- 参数校验（Pydantic Schema）
- 统一响应格式 `{ code, message, data, request_id }`
- 错误统一捕获（`AppError` → HTTP 4xx/5xx）

### 3.2 Orchestrator（`app/orchestrator`）

**允许调用**：`domain/services`、`storage/file`、`runtime`（启动 AgentLoop）
**禁止调用**：LLM 直接调用、Tool 直接执行

职责：
- `session_manager`：创建 Session → 创建 root Agent → 触发 `AgentLoop.start()`
- `task_manager`：Task 入队/出队；状态转换；`failure_counter` 累加
- `state_machine`：校验状态转换合法性（如 `RUNNING → SUCCEEDED` 合法，`QUEUED → FINISHED` 非法）

**Guard 检查位置（必须在 orchestrator 内）**：
```python
# session_manager.py
def check_token_budget(session: Session) -> None:
    if session.token_used >= session.token_budget:
        raise AppError("TOKEN_BUDGET_EXCEEDED", ...)
```

### 3.3 Runtime（`app/runtime`）

**允许调用**：`domain/services`、`llm`、`tool_gateway`、`storage/file`
**禁止调用**：`api`、`orchestrator`（避免循环依赖）

职责：
- `agent_loop`：驱动 Agent Loop 六阶段，持有 turns_used 计数
- `task_executor`：按 `Task.type` 分发：
  - `reasoning` → 调用 LLM，结果写入 `task.result`
  - `tool-call` → 调用 `ToolGateway.call()`
- `tool_gateway`：MCP 标准调用；白名单检查；输入输出脱敏；写审计日志
- `policy_engine`：基于 AgentTemplate 的 `tool_list` 做白名单校验

### 3.4 Domain（`app/domain`）

**规则**：不依赖 `api`、`storage` 具体实现、`llm`
**通过接口访问存储**：领域服务调用 `storage/file` 的 store 类

职责：
- **models**：纯数据结构（dataclass / Pydantic），含状态枚举
- **services**：领域逻辑（状态转换、Guard 累加、上下文拼装）
- **events**：EventBus（内存发布订阅）+ 事件常量

### 3.5 Storage / File（`app/storage/file`）

**规则**：不含业务逻辑，仅提供数据读写
**原子写**：所有 JSON 写入先写 `.tmp` 再 `os.replace()`，防止写损坏

```python
# base.py 核心工具函数
def write_json_atomic(path: Path, data: dict) -> None: ...
def read_json(path: Path) -> dict | None: ...
def append_jsonl(path: Path, record: dict) -> None: ...
def read_jsonl(path: Path) -> list[dict]: ...
```

各 store 职责：

| Store | 读写对象 |
| --- | --- |
| `session_store` | `data/sessions/{id}.json` |
| `task_store` | `data/tasks/{id}.json` |
| `agent_store` | `data/agents/{id}.json` |
| `agent_template_store` | `data/agent_templates/{id}.json` |
| `memory_store` | `data/memory/{session_id}/messages.jsonl` + `summaries.json` |
| `blackboard_store` | `data/blackboard/{session_id}/{topic}.jsonl` |
| `tool_call_store` | `data/tool_calls/{session_id}.jsonl`（追加只写，审计） |

**重启恢复**：启动时扫描 `data/` 目录，将所有 JSON 文件加载回内存，恢复运行时状态。

---

## 4. Agent Loop 代码骨架

```python
# app/runtime/agent_loop.py

class AgentLoop:
    def __init__(
        self,
        session_svc: SessionService,
        task_svc: TaskService,
        memory_svc: MemoryService,
        blackboard_svc: BlackboardService,
        task_executor: TaskExecutor,
        llm_client: LLMClient,
    ): ...

    def run(self, session_id: str, agent_id: str) -> None:
        """驱动 Agent Loop 直到完成或触发 Guard 终止。"""
        while True:
            # Guard 检查
            session = self.session_svc.get(session_id)
            self._check_guard(session, agent)

            # 六阶段
            context = self.observe(session, agent)
            plan    = self.plan(context, agent)
            tasks   = self.create_tasks(session_id, agent_id, plan)
            self.execute(tasks, session_id)
            self.update_memory(session_id, agent_id, tasks)

            # 判断是否完成
            if self._is_done(plan):
                self.session_svc.transition(session_id, "SUCCEEDED")
                break

    def observe(self, session, agent) -> PromptContext:
        """拼装上下文：system_prompt + goal + Blackboard + 消息窗口 + 摘要"""

    def plan(self, context: PromptContext, agent) -> Plan:
        """调用 LLM，解析返回为结构化 Plan（含 task_list）；turns_used += 1"""

    def create_tasks(self, session_id, agent_id, plan: Plan) -> list[Task]:
        """按 Plan 创建 Task，写入文件存储"""

    def execute(self, tasks: list[Task], session_id: str) -> None:
        """按序执行 Task，等待完成；每个 Task 通过 TaskExecutor 分发"""

    def update_memory(self, session_id, agent_id, tasks) -> None:
        """写消息；检查 summary_threshold；publish 产出到 Blackboard"""

    def _check_guard(self, session, agent) -> None:
        """token_budget 硬检查 + turns_used 软检查，违反则抛 AppError"""
```

```python
# app/runtime/task_executor.py

class TaskExecutor:
    def execute(self, task: Task, agent: Agent) -> TaskResult:
        match task.type:
            case "reasoning":
                return self._run_reasoning(task, agent)
            case "tool-call":
                return self._run_tool_call(task, agent)
            case _:
                raise AppError("UNSUPPORTED_TASK_TYPE", task.type)

    def _run_reasoning(self, task, agent) -> TaskResult:
        """构建 LLMRequest → LLMClient.complete() → 写 task.result"""

    def _run_tool_call(self, task, agent) -> TaskResult:
        """从 task.inputs 取 tool_name + arguments → ToolGateway.call()"""
```

```python
# app/runtime/tool_gateway.py

class ToolGateway:
    BUILTIN_TOOLS = {"bash_exec", "http_request"}

    def call(self, tool_name: str, arguments: dict,
             agent: Agent, task_id: str) -> ToolResult:
        # 1. 白名单校验
        self.policy.authorize(agent, tool_name)
        # 2. 执行
        result = self._dispatch(tool_name, arguments)
        # 3. 脱敏 + 审计写文件
        self._audit(tool_name, arguments, result, agent, task_id)
        return result

    def _dispatch(self, tool_name, arguments) -> ToolResult:
        match tool_name:
            case "bash_exec":    return self._bash_exec(arguments)
            case "http_request": return self._http_request(arguments)
```

---

## 5. 关键调用链

### 5.1 创建会话并启动 Agent Loop

```
POST /sessions
  → SessionService.create(goal, template_id, ...)
    → AgentTemplateStore.get(template_id)
    → SessionStore.save(session)
    → AgentStore.save(root_agent)
    → EventBus.publish(SESSION_CREATED)
    → AgentLoop.run(session_id, root_agent_id)   # 异步启动（asyncio.create_task）
  → 返回 202 + session_id
```

### 5.2 Agent Loop 一轮

```
AgentLoop.run()
  → check_guard(session)                          # token_budget / turns_used
  → observe()
      → BlackboardService.pull(agent_id)          # 拉取 _root 增量
      → MemoryService.get_window(session_id)      # 最近 N 条消息
      → MemoryService.get_latest_summary()
  → plan()
      → LLMClient.complete(LLMRequest)            # 调用 LLM
      → 解析为 Plan（task_list）
      → session.token_used += usage.total_tokens
      → SessionStore.save(session)                # 更新 token_used
      → agent.loop_guard.turns_used += 1
  → create_tasks()
      → TaskService.create(task) × N
      → TaskStore.save(task)
  → execute()
      → TaskExecutor.execute(task)
          → ToolGateway.call() 或 LLMClient.complete()
          → TaskStore.save(task, status=FINISHED)
          → EventBus.publish(TASK_FINISHED)
  → update_memory()
      → MemoryService.append_message(...)
      → MemoryStore.append_jsonl(message)
      → 检查 summary_threshold → 触发 summarize_memory()
      → BlackboardService.publish(_root, plan_summary)
      → BlackboardStore.append_jsonl(entry)
```

### 5.3 上下文拼装

```
MemoryService.build_prompt_context(session_id, agent_id, task_id)
  → [1] AgentTemplateStore.get() → system_prompt + goal
  → [2] TaskStore.get(task_id)   → task description + inputs
  → [3] BlackboardService.pull() → _root topic 增量
  → [4] MemoryStore.read_jsonl() → 最近 short_window_size 条消息
  → [5] MemoryStore.get_summary()→ 最新摘要
  → token 估算，超 token_budget × 0.6 时截断低优先级内容
  → 返回 PromptContext
```

---

## 6. 数据流与状态变更规则

1. **所有状态变更必须先通过 `state_machine.transition()` 校验合法性**，再写文件
2. **token_used 累加**必须在 `plan()` 阶段写入 Session，并立即持久化到文件
3. **EventBus** 仅在状态成功落盘后发布，防止事件与实际状态不一致
4. **ToolCall 审计**：`tool_call_store.append()` 在工具执行前后各写一次（RUNNING / SUCCEEDED / FAILED）

```
合法状态转换（Phase 1）：

Session:  QUEUED → RUNNING → SUCCEEDED
                           → FAILED
                           → CANCELED

Task:     PENDING → ACTIVE → FINISHED
                           → FAILED
                           → CANCELED

Agent:    IDLE → RUNNING → FINISHED
                         → FAILED
```

---

## 7. 配置（`app/config/settings.py`）

```python
class Settings(BaseSettings):
    # 数据目录
    data_dir: Path = Path("data")

    # Agent Loop 默认参数
    default_token_budget: int = 200_000
    default_root_max_turns: int = 20
    default_summary_threshold: int = 20   # 消息条数触发摘要
    default_short_window_size: int = 20   # 上下文消息窗口

    # Tool 约束
    bash_exec_timeout_ms: int = 30_000
    bash_exec_output_limit_bytes: int = 64 * 1024
    http_request_timeout_ms: int = 10_000
    http_response_limit_bytes: int = 512 * 1024

    # LLM 默认参数
    default_llm_timeout_sec: int = 60

    # 日志
    log_level: str = "INFO"
```

---

## 8. 编码规范

### 8.1 层间依赖方向

```
api → orchestrator → runtime → domain ← storage/file
                             ↘ llm
```

- `api` 不直接调用 `runtime` 或 `storage`
- `domain` 不依赖 `api`、`llm`、`storage` 具体实现
- `runtime` 不调用 `orchestrator`（避免循环）

### 8.2 错误处理

```python
# 统一使用 AppError，在 API 层捕获转换为 HTTP 响应
raise AppError(code="TOKEN_BUDGET_EXCEEDED", message="token budget exhausted")

# API 层全局捕获
@app.exception_handler(AppError)
async def app_error_handler(request, exc: AppError):
    return JSONResponse(status_code=400, content={"code": exc.code, "message": exc.message})
```

### 8.3 文件写入必须原子化

```python
# ✅ 正确：先写 .tmp 再 rename
def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)

# ❌ 错误：直接写入可能中途崩溃导致文件损坏
path.write_text(json.dumps(data))
```

### 8.4 ULID 生成规范

所有 id 字段使用 `{prefix}_{ulid}` 格式：

| 实体 | 前缀 | 示例 |
| --- | --- | --- |
| Session | `ses_` | `ses_01HV...` |
| Task | `tsk_` | `tsk_01HV...` |
| Agent | `agt_` | `agt_01HV...` |
| AgentTemplate | `tpl_` | `tpl_01HV...` |
| MemoryItem | `mem_` | `mem_01HV...` |
| BlackboardEntry | `bbe_` | `bbe_01HV...` |
| ToolCall | `tlc_` | `tlc_01HV...` |

---

## 9. Phase 1 实现顺序

| 优先级 | 模块 | 关键交付 |
| --- | --- | --- |
| 1 | `storage/file` | `write_json_atomic`、各 store 读写 |
| 2 | `domain/models` | Session / Task / Agent / AgentTemplate / MemoryItem / BlackboardEntry |
| 3 | `domain/services` | SessionService / TaskService / MemoryService / BlackboardService |
| 4 | `orchestrator` | session_manager、task_manager、state_machine |
| 5 | `runtime/tool_gateway` | bash_exec / http_request + 审计 |
| 6 | `runtime/task_executor` | reasoning / tool-call 分发 |
| 7 | `runtime/agent_loop` | 完整六阶段 Loop + Guard |
| 8 | `api` | REST 接口（sessions / tasks / memories / tools / agent-templates / llms） |
| 9 | `observability/logging` | 结构化 JSON 日志 |

---

## 10. Phase 2 延后事项

以下组件已预留目录但 Phase 1 不实现：

| 组件 | 预留位置 | Phase 2 职责 |
| --- | --- | --- |
| DB 仓储 | `storage/db/` | PostgreSQL + SQLAlchemy 替换 file store |
| Redis 缓存 | `storage/cache/` | 短期上下文缓存、分布式锁 |
| Lifecycle Manager | `orchestrator/` 新增 | 多 agent 回收、DAG 调度、并发计数 |
| Skill Router | `runtime/skill_router.py` | SKILL.md 匹配加载 |
| HITL Manager | `orchestrator/` 新增 | 审批队列、approve/reject/modify API |
| spawn_agents 工具 | `runtime/tool_gateway.py` 扩展 | 动态多层 Agent 树 |
| pgvector 语义检索 | `storage/db/` | Memory facts 向量检索 |
| Blackboard 整合 | `domain/services/blackboard_service.py` | LLM 驱动跨 topic 摘要 |
| OpenTelemetry | `observability/tracing.py` | 全链路 trace |
| Prometheus | `observability/metrics.py` | 指标采集 |

---

本文件为 Phase 1 版本，后续随迭代进入 Phase 2 时同步更新。
