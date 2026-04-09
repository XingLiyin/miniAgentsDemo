以下为整理后的结构化 Markdown 版本（已去冗余、统一术语与层级，便于直接落库或作为设计文档使用）：

---

# Lifecycle Manager（LM）设计

## 一、定位与职责边界

LM 是运行在 Orchestrator 层的**全局单例 + 按 session 分片状态**的调度与生命周期管理组件。

### 职责划分

| 职责                       | LM 负责 | LM 不负责 |
| ------------------------ | ----- | ------ |
| sub-agent 实例化与回收         | ✅     |        |
| DAG 依赖解析与调度              | ✅     |        |
| 并发控制（agents / tasks）     | ✅     |        |
| WAITING agent 唤醒         | ✅     |        |
| 失败重试 / failure_threshold | ✅     |        |
| 调用 LLM                   |       | ❌      |
| 执行 Task 业务逻辑             |       | ❌      |
| 写 Blackboard 内容          |       | ❌      |

---

## 二、模型扩展

### Task 模型

```python
# 状态新增
# PENDING | ACTIVE | SUSPENDED | FINISHED | FAILED | CANCELED

dag_deps: list[str] = field(default_factory=list)
parent_task_id: str | None = None
retry_count: int = 0
max_retries: int = 0
output_topic: str = ""
```

### Agent 模型

```python
# 状态新增
# IDLE | RUNNING | WAITING | FINISHED | FAILED

spawn_depth: int = 0
parent_task_id: str | None = None
spawned_task_ids: list[str] = field(default_factory=list)
```

---

## 三、事件类型扩展

```python
# Spawn 相关
SPAWN_REQUESTED = "SPAWN_REQUESTED"
SPAWN_APPROVED  = "SPAWN_APPROVED"
SPAWN_REJECTED  = "SPAWN_REJECTED"

# Agent 状态
AGENT_WAITING = "AGENT_WAITING"
AGENT_RESUME  = "AGENT_RESUME"

# LM 内部调度
LIFECYCLE_TASK_READY        = "LIFECYCLE_TASK_READY"
LIFECYCLE_AGENT_SCHEDULED   = "LIFECYCLE_AGENT_SCHEDULED"
LIFECYCLE_AGENT_RECYCLED    = "LIFECYCLE_AGENT_RECYCLED"
```

---

## 四、LM 内部状态

### AgentMeta

```python
@dataclass
class AgentMeta:
    agent_id: str
    task_id: str | None
    spawn_depth: int
    status: str  # RUNNING | WAITING
    waiting_for: set[str] = field(default_factory=set)
```

### LMState（session 级）

```python
@dataclass
class LMState:
    session_id: str

    max_concurrent_agents: int = 5
    max_concurrent_tasks: int = 10
    failure_threshold: int = 3
    max_retries: int = 1

    concurrent_agents: int = 0
    concurrent_tasks: int = 0
    failure_counter: int = 0

    agent_registry: dict[str, AgentMeta] = field(default_factory=dict)

    dep_counter: dict[str, int] = field(default_factory=dict)
    downstream: dict[str, set[str]] = field(default_factory=dict)

    pending_queue: list[str] = field(default_factory=list)
```

---

## 五、LifecycleManager 接口

```python
class LifecycleManager:
    def __init__(...):
        self._states: dict[str, LMState] = {}
        self._resume_events: dict[str, threading.Event] = {}
        self._resume_payloads: dict[str, ResumePayload] = {}
        self._lock = threading.Lock()

        event_bus.subscribe(AGENT_FINISHED, self._on_agent_finished)
        event_bus.subscribe(AGENT_FAILED, self._on_agent_failed)
        event_bus.subscribe(TASK_FAILED, self._on_task_failed)
```

### 对外接口

```python
def init_session(self, session_id: str, **limits) -> None
def register_root(self, session_id: str, agent_id: str) -> None

def handle_spawn_requested(
    session_id: str,
    requesting_agent_id: str,
    requesting_task_id: str,
    plan: list[SpawnPlanItem],
    resume_hint: str = "",
) -> SpawnResult
```

### 私有事件处理

```python
def _on_agent_finished(...)
def _on_agent_failed(...)
def _on_task_failed(...)
```

### 调度方法

```python
def _drain_queue(session_id: str) -> None
def _instantiate_sub_agent(...) -> str
def _try_resume_waiting_agent(...) -> None
def _check_session_done(session_id: str) -> None
```

---

## 六、SubAgentRunner

单 Task 执行模型（无循环）：

```python
class SubAgentRunner:
    def run_async(self, session_id, agent_id, task_id):
        asyncio.create_task(
            loop.run_in_executor(None, self._run_sync, ...)
        )

    def _run_sync(...):
        try:
            result = actor.act(...)
            bus.publish(AGENT_FINISHED, {...})
        except Exception:
            bus.publish(AGENT_FAILED, {...})
```

---

## 七、关键流程

### 7.1 AGENT_FINISHED

```text
1. agent_registry 移除 agent
2. 并发计数递减
3. 标记 task 完成
4. 更新 DAG（dep_counter / downstream）
5. failure_counter 清零
6. 尝试唤醒 WAITING agent
7. 调度 pending_queue
8. 检查 session 结束
```

---

### 7.2 spawn_agents 主流程

```text
1. 权限 / 并发 / token 校验
2. 创建子任务（构建 DAG）
3. 初始化 dep_counter + downstream
4. 父 task → SUSPENDED，agent → WAITING
5. 注册 resume_event
6. 调度无依赖任务
7. 阻塞等待 resume_event
8. 返回子任务结果
```

---

### 7.3 WAITING → RUNNING 恢复

```text
1. 从 waiting_for 移除 finished_task
2. 若集合为空：
   - 汇总子任务结果
   - 恢复 parent task → ACTIVE
   - agent → RUNNING
   - 发布 AGENT_RESUME
   - resume_event.set()
```

---

## 八、系统集成点

| 组件               | 变更                         |
| ---------------- | -------------------------- |
| SessionManager   | 初始化 LM + 注册 root agent     |
| AgentLoop        | 发布 AGENT_FINISHED / FAILED |
| Tools            | 新增 spawn_agents            |
| TaskStateMachine | 新增 SUSPENDED               |
| Agent.status     | 新增 WAITING                 |
| DI（deps.py）      | 注入 LM                      |

---

## 九、线程模型

```text
Main Loop (asyncio)
  ├── root agent thread（可阻塞 WAITING）
  ├── sub-agent thread（执行 Task）
  └── EventBus → LM handler（加锁）
```

关键约束：

* LMState：per-session 单锁
* EventBus：同步回调
* wait() 在释放锁后调用（避免死锁）

---

## 十、V1 范围

### 包含

* 单层 spawn
* 无 DAG retry
* 内存态 LM
* failure_threshold → FAILED

### 延后

* 多层 spawn
* 优先级调度
* Redis 持久化
* HITL
* token 精细预算

---

# AgentLoop 重构设计

## 核心原则

1. 单轮只执行一个 task
2. Actor 统一入口（plan / act 内部分发）
3. Observer 每轮执行
4. TaskManager 控制推进
5. 主循环扁平化

---

## 一、主循环

```python
while True:
    session = get(session_id)

    turns_used += 1
    if turns_used > max_turns:
        break

    task = task_mgr.next_task(session_id)
    if task is None:
        break

    ctx = reasoner.reason(session, agent, task)
    result = actor.act(task, ctx, agent)

    if result.hitl_task_id:
        return

    verdict = observer.observe(session, result, ctx)

    if verdict.done:
        break

    task_mgr.advance(session_id, agent.id, task, result)
```

---

## 二、Reasoner

```python
def reason(session, agent, task):
    if task.type == "plan":
        return _reason_for_plan(...)
    return _reason_for_act(...)
```

### ReasoningContext

```python
@dataclass
class ReasoningContext:
    mode: Literal["plan", "act"]
    current_task: Task | None
```

---

## 三、Actor

```python
def act(task, ctx, agent):
    if task.type == "plan":
        return _act_as_planner(...)
    return _act_as_executor(...)
```

### Planner 分支

```text
- 创建 atomic tasks
- plan_task_count 标记数量
- 0 表示空计划（直接完成）
```

---

## 四、Observer

```python
def observe(session, result, ctx):
```

规则增强：

```text
plan + 0 task → done=True
plan + N task → done=False
```

---

## 五、TaskManager

### next_task

```python
return first_pending_task
```

### advance

```text
1. plan → no-op
2. atomic：
   - 更新失败计数
   - 若无剩余任务 → 创建 re-plan task
```

---

## 六、依赖注入

```python
Actor ← Planner
AgentLoop ← TaskManager
```

---

## 七、执行示例

```text
T1(plan) → 创建 T2/T3/T4
T2 → 执行
T3 → 执行
T4 → 执行
→ 创建 T5(re-plan)
T5 → 空计划 → 结束
```

---