# miniAgents 模块结构与编码指南（V1）

本文档用于指导 `miniAgents` 项目的模块划分与编码落地，基于 `readme.md` 的领域模型与运行流程。

---

## 1. 模块结构总览

建议采用“分层 + 领域模块”的混合结构：

- **API/Gateway**：REST/WS 接入、鉴权、参数校验、限流、请求编排
- **Orchestrator**：会话与任务调度、状态机、重试/超时
- **Runtime/Worker**：Agent Loop、Task 执行、工具调用
- **Domain**：Task/Agent/Session/Memory 等核心领域模型与服务
- **Storage**：PostgreSQL/Redis/对象存储访问层
- **Observability**：日志、指标、追踪、审计
- **Tool/Skill**：MCP Tools 接入、Skill Router

---

## 2. 建议目录结构

```
miniAgents/
  app/
    api/
      v1/
        routes/
          tasks.py
          sessions.py
          memories.py
          tools.py
        schemas/
          task.py
          session.py
          memory.py
          tool.py
        deps.py
        middleware.py
    orchestrator/
      session_manager.py
      task_manager.py
      task_queue.py
      scheduler.py
      state_machine.py
    runtime/
      agent_loop.py
      task_executor.py
      skill_router.py
      tool_gateway.py
      policy_engine.py
    domain/
      models/
        task.py
        agent.py
        session.py
        tool_call.py
        memory.py
        blackboard.py
      services/
        task_service.py
        session_service.py
        memory_service.py
        blackboard_service.py
      events/
        event_types.py
        event_bus.py
    storage/
      db/
        base.py
        session_repo.py
        task_repo.py
        memory_repo.py
        tool_call_repo.py
      cache/
        redis_client.py
      artifact/
        artifact_store.py
    llm/
      provider_registry.py
      openai_adapter.py
      mock_adapter.py
    observability/
      logging.py
      metrics.py
      tracing.py
      audit.py
    config/
      settings.py
      logging.yaml
    common/
      errors.py
      utils.py
      idempotency.py
  migrations/
  tests/
  docs/
    architecture.md
```

说明：
- `app/domain` 只包含领域模型与领域服务，不依赖具体框架。
- `app/runtime` 是 Agent Loop 的核心执行层，依赖 `domain` 与 `storage`。
- `app/orchestrator` 负责会话/任务队列与状态机，不直接调用外部 Tools。
- `app/api` 只做请求入口和编排，不包含业务实现。

---

## 3. 核心模块职责

### 3.1 API 层（`app/api`）

职责：
- REST 接口与参数校验
- 请求幂等（`Idempotency-Key`）
- 统一响应格式与错误码
- 透传 `request_id` 并写入日志/trace

只允许调用：
- `domain/services` 或 `orchestrator` 的公开接口

### 3.2 Orchestrator（`app/orchestrator`）

职责：
- Session 生命周期管理
- 任务队列与状态机调度
- 超时、取消、重试、失败传播策略

关键组件：
- `session_manager`：创建/恢复/取消会话
- `task_queue`：会话任务入队/出队
- `scheduler`：并发/优先级控制

### 3.3 Runtime（`app/runtime`）

职责：
- Agent Loop 执行
- Task 执行与结果回写
- Tool 调用与 Skill 路由

关键组件：
- `agent_loop`：Observe/Plan/CreateTask/Schedule/Execute/UpdateMemory
- `task_executor`：按 Task 类型执行（LLM、Tool、子 Agent）
- `tool_gateway`：MCP Tools 调用，审计与脱敏
- `skill_router`：匹配/加载 Skill 并输出执行计划

### 3.4 Domain（`app/domain`）

职责：
- 领域模型与状态机定义
- 领域服务（Task/Session/Memory/Blackboard）
- 事件定义与发布

规则：
- 不直接依赖 API 或具体数据库实现
- 领域服务通过 `storage` 接口访问持久化

### 3.5 Storage（`app/storage`）

职责：
- DB/Cache/Object Storage 的数据访问
- Repository/DAO 实现
- 读写脱敏、序列化、迁移

规则：
- 不包含业务逻辑，仅提供数据读写能力

### 3.6 Observability（`app/observability`）

职责：
- 统一日志结构
- 指标埋点
- 链路追踪
- 审计事件写入

---

## 4. Agent Loop Runtime 代码骨架

`app/runtime/agent_loop.py` 的核心接口建议：

```python
class AgentLoop:
    def run_once(self, session_id: str, task_id: str, agent_id: str) -> None:
        context = self.observe(session_id, task_id, agent_id)
        plan = self.plan(context)
        tasks = self.create_tasks(plan)
        self.schedule(tasks)
        result = self.execute(tasks)
        self.update_memory(session_id, task_id, result)
```

约束：
- 所有外部副作用必须通过 `tool_gateway`
- 结果写入必须幂等（基于 `task_id` 或 `idempotency_key`）
- 任意阶段失败必须产出 `session_events`

---

## 5. 数据模型与仓储映射

推荐仓储命名：
- `TaskRepo`, `TaskEdgeRepo`, `SessionTaskQueueRepo`
- `SessionRepo`, `SessionEventRepo`
- `MessageRepo`, `MemoryRepo`, `BlackboardRepo`
- `ToolCallRepo`, `ToolSpecRepo`, `ArtifactRepo`

说明：
- Repo 层仅返回领域对象或 Pydantic DTO
- 所有写操作统一通过服务层触发，避免绕开业务校验

---

## 6. 关键调用链

### 6.1 创建会话

API → SessionService → Orchestrator 入队 → `session_events`

### 6.2 执行任务

Task Queue → Runtime Agent Loop → Task Executor → Tool Gateway → 回写 Memory/Events

### 6.3 Memory 拼装

MemoryService → MessageRepo + MemoryRepo + BlackboardRepo → Prompt Context

---

## 7. 配置与运行参数

建议统一配置入口 `app/config/settings.py`：

- DB/Redis 连接
- Worker 并发与队列
- Agent 默认模型/上下文
- Tool 白名单、超时、输出上限
- 事件与日志级别

---

## 8. 实现优先级建议

1. Domain + Storage（Task/Session/Memory）
2. Orchestrator（Session/Task Executor）
3. Runtime（Agent Loop + Tool Gateway）
4. API 层（REST）
5. Observability（Metrics/Tracing）

---

## 9. 风险与注意事项

- 避免 API 直接调用 Tool（必须通过 Runtime/Policy）
- Task 执行要保证幂等，否则重试会放大副作用
- Memory 更新与事件写入需保证顺序一致性

---

本文件为 V1 版本，后续可根据测试反馈与性能瓶颈继续细化。
