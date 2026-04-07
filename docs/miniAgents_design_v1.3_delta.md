 # Lifecycle Manager 设计                                                                                                                                   
                                                                                                                                                           
  一、定位与职责边界                                                                                                                                       
                                                                                                                                                           
  LM 是一个全局单例 + 按 session 分片状态的编排器，运行在 Orchestrator 层。

  ┌───────────────────────────────────┬───────┬─────────┐
  │               职责                │ LM 做 │ LM 不做 │
  ├───────────────────────────────────┼───────┼─────────┤
  │ sub-agent 实例化与回收            │ ✅    │         │
  ├───────────────────────────────────┼───────┼─────────┤
  │ DAG 依赖解析与调度                │ ✅    │         │
  ├───────────────────────────────────┼───────┼─────────┤
  │ 并发计数（agents / tasks）        │ ✅    │         │
  ├───────────────────────────────────┼───────┼─────────┤
  │ WAITING agent 唤醒                │ ✅    │         │
  ├───────────────────────────────────┼───────┼─────────┤
  │ 失败重试 / failure_threshold 检查 │ ✅    │         │
  ├───────────────────────────────────┼───────┼─────────┤
  │ 调用 LLM                          │       │ ❌      │
  ├───────────────────────────────────┼───────┼─────────┤
  │ 执行 Task 业务逻辑                │       │ ❌      │
  ├───────────────────────────────────┼───────┼─────────┤
  │ 写 Blackboard 业务内容            │       │ ❌      │
  └───────────────────────────────────┴───────┴─────────┘

  ---
  二、需要扩展的现有模型

  Task 新增字段

  # app/domain/models/task.py

  # 状态新增: SUSPENDED（agent 调用 spawn_agents 后父 task 挂起）
  # PENDING | ACTIVE | SUSPENDED | FINISHED | FAILED | CANCELED

  dag_deps: list[str] = field(default_factory=list)  # 依赖的 task_id 列表
  parent_task_id: str | None = None                  # 所属 SUSPENDED 祖先 task
  retry_count: int = 0
  max_retries: int = 0
  output_topic: str = ""                             # 产出发布到哪个 Blackboard topic

  Agent 新增字段

  # app/domain/models/agent.py

  # 状态新增: WAITING（spawn_agents 后等待子任务完成）
  # IDLE | RUNNING | WAITING | FINISHED | FAILED

  spawn_depth: int = 0                               # root=0, 第一层 sub-agent=1 ...
  parent_task_id: str | None = None                  # 本 agent 正在执行的 Task（root agent 为 None）
  spawned_task_ids: list[str] = field(default_factory=list)  # WAITING 状态时挂起等待的 task_id

  ---
  三、新增事件类型

  # app/domain/events/event_types.py 追加

  # Spawn 事件（spawn_agents 工具触发）
  SPAWN_REQUESTED         = "SPAWN_REQUESTED"
  SPAWN_APPROVED          = "SPAWN_APPROVED"
  SPAWN_REJECTED          = "SPAWN_REJECTED"

  # Agent 状态事件
  AGENT_WAITING           = "AGENT_WAITING"    # agent 进入 WAITING
  AGENT_RESUME            = "AGENT_RESUME"     # 子任务全部完成，agent 可继续

  # LM 内部调度事件（_lifecycle topic 用）
  LIFECYCLE_TASK_READY    = "LIFECYCLE_TASK_READY"      # DAG 依赖满足，进入调度队列
  LIFECYCLE_AGENT_SCHEDULED  = "LIFECYCLE_AGENT_SCHEDULED"
  LIFECYCLE_AGENT_RECYCLED   = "LIFECYCLE_AGENT_RECYCLED"

  ---
  四、LM 内部状态

  # app/orchestrator/lifecycle_manager.py

  @dataclass
  class AgentMeta:
      """agent_registry 中每个存活 agent 的元数据。"""
      agent_id: str
      task_id: str | None          # root agent 无绑定 task，为 None
      spawn_depth: int
      status: str                  # RUNNING | WAITING
      # WAITING 时填充：等待这些 task_id 全部完成后唤醒
      waiting_for: set[str] = field(default_factory=set)


  @dataclass
  class LMState:
      """单个 session 的 LM 运行时状态。"""
      session_id: str
      max_concurrent_agents: int = 5
      max_concurrent_tasks: int = 10
      failure_threshold: int = 3
      max_retries: int = 1

      concurrent_agents: int = 0
      concurrent_tasks: int = 0
      failure_counter: int = 0

      # agent_id -> AgentMeta（含 root agent）
      agent_registry: dict[str, AgentMeta] = field(default_factory=dict)

      # task_id -> 剩余未完成依赖数
      dep_counter: dict[str, int] = field(default_factory=dict)
      # finished_task_id -> 下游 task_id 集合（反向依赖索引）
      downstream: dict[str, set[str]] = field(default_factory=dict)
      # 依赖已满足但并发余量不足，排队等待
      pending_queue: list[str] = field(default_factory=list)

  ---
  五、LifecycleManager 类接口

  class LifecycleManager:
      def __init__(
          self,
          session_svc: SessionService,
          task_svc: TaskService,
          agent_store: AgentStore,
          template_svc: AgentTemplateService,
          event_bus: EventBus,
          sub_agent_runner: SubAgentRunner,   # 见第六节
          settings: Settings,
      ) -> None:
          self._states: dict[str, LMState] = {}
          # agent_id -> threading.Event，用于唤醒 WAITING agent 线程
          self._resume_events: dict[str, threading.Event] = {}
          # agent_id -> ResumePayload，唤醒时传递子任务摘要
          self._resume_payloads: dict[str, ResumePayload] = {}
          self._lock = threading.Lock()

          event_bus.subscribe(AGENT_FINISHED, self._on_agent_finished)
          event_bus.subscribe(AGENT_FAILED,   self._on_agent_failed)
          event_bus.subscribe(TASK_FAILED,    self._on_task_failed)

      # ── 对外接口 ─────────────────────────────────────────────

      def init_session(self, session_id: str, **limits) -> None:
          """SessionManager 创建 session 后调用，初始化 LMState。"""

      def register_root(self, session_id: str, agent_id: str) -> None:
          """注册 root agent（concurrent_agents += 1，加入 registry）。"""

      def handle_spawn_requested(
          self,
          session_id: str,
          requesting_agent_id: str,
          requesting_task_id: str,
          plan: list[SpawnPlanItem],
          resume_hint: str = "",
      ) -> SpawnResult:
          """
          spawn_agents 工具同步调用此方法。
          APPROVED → agent 进入 WAITING，方法内阻塞等待子任务完成，返回汇总结果。
          REJECTED → 立即返回，agent 在同一轮 Loop 降级处理。
          """

      # ── 私有事件处理 ─────────────────────────────────────────

      def _on_agent_finished(self, event_type: str, payload: dict) -> None: ...
      def _on_agent_failed(self, event_type: str, payload: dict) -> None: ...
      def _on_task_failed(self, event_type: str, payload: dict) -> None: ...

      # ── 私有调度方法 ─────────────────────────────────────────

      def _drain_queue(self, session_id: str) -> None:
          """从 pending_queue 中取出 task，在并发余量允许时实例化 agent 并启动。"""

      def _instantiate_sub_agent(
          self, session_id: str, task_id: str,
          spawn_depth: int, template_id: str | None,
      ) -> str:
          """创建 Agent 实例，写盘，异步启动 SubAgentRunner。返回 agent_id。"""

      def _try_resume_waiting_agent(self, session_id: str, finished_task_id: str) -> None:
          """检查 finished_task_id 是否让某个 WAITING agent 的 waiting_for 集合清空。"""

      def _check_session_done(self, session_id: str) -> None:
          """若无存活 agent 且 pending_queue 为空，转换 session → SUCCEEDED。"""

  ---
  六、SubAgentRunner（sub-agent 执行单元）

  sub-agent 只执行一个 Task，不需要完整的 Reason→Plan 循环：

  SubAgentLoop（单 Task）：
    1. 加载 Task.description 作为执行目标
    2. 调用 Actor.act(task, ctx, agent)
    3. 完成 → 发布 AGENT_FINISHED 事件
    4. 失败 → 发布 AGENT_FAILED 事件

  class SubAgentRunner:
      """在 asyncio thread pool 中运行 sub-agent（执行单个 Task）。"""

      def run_async(self, session_id: str, agent_id: str, task_id: str) -> None:
          """从异步上下文启动（asyncio.create_task + run_in_executor）。"""
          asyncio.create_task(
              asyncio.get_event_loop().run_in_executor(
                  None, self._run_sync, session_id, agent_id, task_id
              )
          )

      def _run_sync(self, session_id: str, agent_id: str, task_id: str) -> None:
          """线程内同步执行：Actor.act() → 发布事件。"""
          try:
              agent = self._load_agent(agent_id)
              agent.status = "RUNNING"
              self._agent_store.save(agent.to_dict())

              task = self._task_svc.get(task_id)
              ctx = self._build_ctx(session_id, agent)     # 构建 ReasoningContext
              result = self._actor.act(task, ctx, agent)

              self._bus.publish(AGENT_FINISHED, {
                  "session_id": session_id,
                  "agent_id": agent_id,
                  "task_id": task_id,
                  "success": result.success,
                  "output": result.output,
              })
          except Exception as e:
              self._bus.publish(AGENT_FAILED, {
                  "session_id": session_id,
                  "agent_id": agent_id,
                  "task_id": task_id,
                  "error": str(e),
              })

  ---
  七、关键流程

  7.1 AGENT_FINISHED 处理逻辑

  _on_agent_finished(payload):
    with lock(session):
      meta = agent_registry.pop(agent_id)
      concurrent_agents -= 1
      if meta.task_id:
          concurrent_tasks -= 1
          task_svc.finish(meta.task_id, output)   # 若 Actor 还没 finish

      # DAG：遍历 downstream[task_id]，对每个下游 dep_counter[t] -= 1
      for downstream_task in downstream.get(meta.task_id, []):
          dep_counter[downstream_task] -= 1
          if dep_counter[downstream_task] == 0:
              pending_queue.append(downstream_task)

      failure_counter = 0   # 成功清零

      _try_resume_waiting_agent(session_id, meta.task_id)
      _drain_queue(session_id)
      _check_session_done(session_id)

  7.2 handle_spawn_requested 流程

  handle_spawn_requested(session_id, requesting_agent_id, requesting_task_id, plan):
    with lock(session):
      # 1. 权限 & 余量检查
      agent = load_agent(requesting_agent_id)
      if not agent.has_spawn_permission:
          return REJECTED("NO_PERMISSION")
      if concurrent_agents + len(plan) > max_concurrent_agents:
          return REJECTED("CONCURRENT_LIMIT")
      if session.token_used > session.token_budget * 0.9:
          return REJECTED("TOKEN_BUDGET")

      # 2. 创建子 Task 列表，解析 DAG（name→id 映射）
      task_ids = []
      name_to_id = {}
      for item in plan:
          sub_task = task_svc.create(session_id, requesting_agent_id,
                                     "atomic", item.title, item.description,
                                     parent_task_id=requesting_task_id,
                                     dag_deps=[name_to_id[d] for d in item.deps])
          name_to_id[item.title] = sub_task.id
          task_ids.append(sub_task.id)

      # 3. 建立 DAG 索引（dep_counter + downstream）
      for task_id in task_ids:
          task = task_svc.get(task_id)
          dep_counter[task_id] = len(task.dag_deps)
          for dep_id in task.dag_deps:
              downstream.setdefault(dep_id, set()).add(task_id)

      # 4. 挂起请求 task，agent → WAITING
      task_svc.transition(requesting_task_id, "SUSPENDED")
      agent.status = "WAITING"
      agent.spawned_task_ids = task_ids
      agent_store.save(agent.to_dict())
      agent_registry[requesting_agent_id].status = "WAITING"
      agent_registry[requesting_agent_id].waiting_for = set(task_ids)

      # 5. 创建恢复事件
      resume_event = threading.Event()
      resume_events[requesting_agent_id] = resume_event

      # 6. 调度无依赖的子 Task
      ready = [t for t in task_ids if dep_counter[t] == 0]
      pending_queue.extend(ready)
      _drain_queue(session_id)

    # 7. 释放锁后阻塞等待（最多 1 小时）
    resume_event.wait(timeout=3600)
    payload = resume_payloads.pop(requesting_agent_id, None)
    return APPROVED_WITH_RESULTS(payload)

  7.3 _try_resume_waiting_agent

  _try_resume_waiting_agent(session_id, finished_task_id):
    for agent_id, meta in agent_registry.items():
      if meta.status != "WAITING":
          continue
      meta.waiting_for.discard(finished_task_id)
      if len(meta.waiting_for) == 0:
          # 收集子任务结果
          results = [task_svc.get(t).result for t in agent.spawned_task_ids]
          resume_payloads[agent_id] = ResumePayload(results=results, ...)

          # 恢复 Task + Agent 状态
          task_svc.transition(agent.parent_task_id, "ACTIVE")
          agent.status = "RUNNING"
          agent.spawned_task_ids = []
          agent_store.save(agent.to_dict())

          bus.publish(AGENT_RESUME, {"agent_id": agent_id, ...})
          resume_events[agent_id].set()   # 唤醒阻塞线程

  ---
  八、与现有代码的集成点

  ┌─────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────────────┐
  │              位置               │                                          变更                                           │
  ├─────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ SessionManager.create_session() │ 调用 lm.init_session() + lm.register_root()                                             │
  ├─────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ SessionManager.schedule_loop()  │ 改为 lm.register_root() 内部调度，SessionManager 不再直接 asyncio.create_task           │
  ├─────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ AgentLoop.run() finally 块      │ 发布 AGENT_FINISHED / AGENT_FAILED 事件（LM 订阅后处理，不再由 loop 转换 session 状态） │
  ├─────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ tools/builtins.py               │ 新增 spawn_agents 工具，调用 lm.handle_spawn_requested()                                │
  ├─────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ TaskStateMachine                │ 加入 SUSPENDED 状态（ACTIVE → SUSPENDED，SUSPENDED → ACTIVE / FAILED）                  │
  ├─────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ Agent.status                    │ 加入 WAITING（RUNNING → WAITING → RUNNING）                                             │
  ├─────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ deps.py                         │ 注入 LM 单例，传入 SubAgentRunner                                                       │
  └─────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  九、线程安全模型

  主线程（asyncio event loop）
    └── create_task → run_in_executor
          ├── root agent thread（AgentLoop.run）
          │     └── spawn_agents → lm.handle_spawn_requested() → resume_event.wait() [阻塞]
          ├── sub-agent thread 1（SubAgentRunner._run_sync）
          │     └── 完成 → bus.publish(AGENT_FINISHED) → lm._on_agent_finished() [with lock]
          │           └── resume_event.set() → 唤醒 root agent thread
          └── sub-agent thread 2 ...

  - LMState 用 单个 threading.Lock 保护（per session）
  - EventBus 回调在调用方线程中同步执行，LM handler 获锁后操作状态
  - resume_event.wait() 在 lock 释放后调用，不会死锁

  ---
  十、V1 范围与延后事项

  V1 实现：
  - 单层 spawn（root→sub，sub 不再 spawn）
  - 无 DAG 重试（max_retries=0）
  - LM 状态内存 only，崩溃不恢复
  - failure_threshold 触发直接 FAILED（不做 PAUSED_HITL）

  延后到 V2：
  - 多层嵌套 spawn
  - pending_queue 优先级排序
  - LM 状态持久化到 Redis（崩溃恢复）
  - failure_threshold → PAUSED_HITL + HITL Manager 整合
  - spawn_agents 的 token 余量精细预算


  AgentLoop Mode Flag 设计文档                                                                                                                    
                                                                                                                                                  
  一、动机                                                                                                                                        
                                                                                                                                                  
  当前 AgentLoop 每轮固定执行 Reason → Plan → Act → Observe 四个阶段。这带来两个问题：                                                            
                                                                                                                                                  
  1. Planning 和 Acting 耦合：一旦 Plan 产出，立即执行，无法在中间插入审核（HITL at plan level）或保留上下文
  2. 无法单独重试：Act 失败后下一轮必须重新 Plan，即使任务列表本身没问题

  引入 loop_mode flag 后，每次迭代只做一件事：要么 Plan，要么 Act，两者交替进行。

  ---
  二、Flag 定义

  # app/domain/models/agent.py — LoopGuard 新增字段

  loop_mode: Literal["PLAN", "ACT"] = "PLAN"

  存储位置：LoopGuard（已持久化在 agents/{id}.json），与 turns_used、max_turns 同级。

  初始值："PLAN"（agent 刚创建时，第一轮必须先规划）。

  ---
  三、状态机

             ┌─────────────────────────────────────────┐
             │                                         │
             ▼                                         │
    ┌──────────────┐   Plan 产出非空任务              ┌──────────────┐
    │   PLAN 模式   │ ─────────────────────────────▶ │   ACT 模式   │
    └──────────────┘                                 └──────────────┘
           │                                                │
           │ Plan 返回空列表                                 │ Observe.done=True
           ▼                                               ▼
       SUCCEEDED                                       SUCCEEDED

    PLAN 模式异常 / Guard 触发 → FAILED
    ACT 模式任务全失败 / Guard 触发 → FAILED
    ACT 模式 HITL 触发 → WAITING_INPUT（loop 暂停，等待用户输入后从 ACT 继续）
    ACT 模式 Observe.done=False → 切回 PLAN 模式（下轮重新规划）

  ---
  四、各模式行为

  4.1 PLAN 模式

  Guard 检查（token_budget、turns_used）
    │
  Reason()  →  构建 ReasoningContext（Memory + Blackboard + Tools + Skills）
    │
  Plan()    →  LLM 调用，产出 PlannedTask[]
    │
    ├─ tasks 为空  →  session SUCCEEDED，结束
    │
    └─ tasks 非空  →  批量创建 Task 记录（status=PENDING）
                      turns_used += 1          ← 只在 PLAN 轮计数
                      loop_mode = "ACT"
                      持久化 Agent
                      本轮结束（不执行 Act）

  关键变化：
  - Plan 产出后立即写入 TaskService（status=PENDING），形成可查的任务队列
  - 本轮不调用 Actor，Task 创建即停
  - turns_used 只在 PLAN 轮递增，保持 max_turns 语义不变（= 规划轮数上限）

  4.2 ACT 模式

  Guard 检查（token_budget；turns_used 不递增）
    │
  Reason()  →  构建 ReasoningContext（刷新上下文，感知新消息/Blackboard 变化）
    │
  Act()     →  顺序执行所有 PENDING tasks（与现行 _act_all 逻辑一致）
    │           每个 task: PENDING → ACTIVE → FINISHED/FAILED
    │
    ├─ HITL 触发  →  loop 暂停（return），session=WAITING_INPUT
    │                用户回答后从 ACT 继续（loop_mode 保持 "ACT"）
    │
    └─ Act 完成   →  Observe()  →  ObserverVerdict
                      ├─ done=True   →  session SUCCEEDED
                      └─ done=False  →  loop_mode = "PLAN"  ← 切回规划
                                        写摘要到 Memory + Blackboard
                                        持久化 Agent
                                        本轮结束

  关键变化：
  - ACT 模式不调用 Planner
  - Observe 依然在 ACT 模式末尾执行（评估整体进度）
  - Act 失败不立即 FAILED，而是继续 Observe，由 Observer 决定是否 done

  ---
  五、pending task 的持久化方案

  PLAN 模式直接调用 task_svc.create()，所有计划任务以 status=PENDING 写盘。

  ACT 模式通过 task_svc.list_by_session() 查询当前 session 下所有 PENDING tasks，按 created_at 排序后执行。

  PLAN 模式：  task_svc.create() × N  →  [PENDING, PENDING, PENDING]
                ↓
  ACT 模式：   list_by_session(filter=PENDING) → 顺序执行
                每个：PENDING → ACTIVE → FINISHED/FAILED

  优点：
  - 不需要在 Agent/Session 上额外存储任务列表字段
  - PENDING tasks 天然可通过 API 查询（任务审核、HITL at plan level）
  - crash 重启后 loop_mode="ACT" + 存在 PENDING tasks → 直接从 Act 恢复，无需重新 Plan

  ---
  六、对现有代码的影响

  ┌────────────────────────────────┬─────────────────────────────────────────────────────────────┐
  │              位置              │                            变更                             │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ LoopGuard                      │ 新增 loop_mode: Literal["PLAN","ACT"] = "PLAN"              │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ AgentLoop.run()                │ 主循环按 loop_mode 分支，移除固定四阶段流水                 │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ AgentLoop._act_all()           │ 入参从 plan: TaskPlan 改为从 TaskService 查询 PENDING tasks │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ TaskStateMachine               │ 无变化（PENDING→ACTIVE 路径已存在）                         │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ SessionManager.answer_input()  │ 无变化（HITL 恢复后 loop_mode 保持 "ACT"，直接继续）        │
  ├────────────────────────────────┼─────────────────────────────────────────────────────────────┤
  │ SubAgentRunner._run_sub_safe() │ 无变化（sub-agent 直接调用 Actor，不走 AgentLoop）          │
  └────────────────────────────────┴─────────────────────────────────────────────────────────────┘

  ---
  七、边界情况

  ┌─────────────────────────────────────────────────┬───────────────────────────────────────────────────────────────┐
  │                      场景                       │                             处理                              │
  ├─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┤
  │ ACT 模式所有 task 均 FAILED，Observe 认为未完成 │ loop_mode → PLAN，下轮重新规划                                │
  ├─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┤
  │ PLAN 模式产出 tasks，但 HITL 需要在 ACT 前审批  │ PENDING tasks 可见，人工审核后触发 ACT（Phase 2 扩展点）      │
  ├─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┤
  │ crash 发生在 PLAN 之后、ACT 之前                │ 重启时 loop_mode="ACT"，存在 PENDING tasks，直接进入 ACT 恢复 │
  ├─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┤
  │ crash 发生在 ACT 中途（部分 tasks FINISHED）    │ 重启时查询 PENDING tasks，跳过已完成的，从剩余继续            │
  ├─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┤
  │ spawn_agents 在 ACT 中触发                      │ 无变化，Actor 内处理，loop_mode 不影响                        │
  ├─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────┤
  │ turns_used 含义                                 │ = PLAN 轮执行次数，max_turns 仍为规划次数上限                 │
  └─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────┘

  ---
  八、不改动的部分

  - Reason 在两种模式下均执行（刷新上下文是必要的）
  - Observe 保持在 ACT 末尾（不在 PLAN 末尾执行）
  - sub-agent 路径（SubAgentRunner）不涉及此 flag
  - Guard 检查在每轮开头执行，不区分模式


  AgentLoop 模式切换设计文档（task type 驱动）                                                                                                    
                                                                                                                                                  
  一、核心思路                                                                                                                                    
                                                                                                                                                  
  用 Task 类型 替代 flag 字段来决定当前迭代执行哪个分支。Loop 每次迭代先取队列中第一个 PENDING task，根据其 type 分流：                           
                                                                                                                                                  
  type == "plan"   →  执行 Plan 分支                                                                                                            
  type == "atomic" →  执行 Act 分支（含 Observe）

  "plan" task 的角色：既是一个可审计的 Task 记录，也是驱动 Planner 执行的信号。Planner 运行的输出不再是直接执行，而是批量创建 atomic 类型的子
  Task。

  ---
  二、Task 类型扩展

  新增合法 task type 值（现有 atomic / user_input 不变）：

  type = "plan"    ← 新增：触发 Planner；产出若干 atomic task
  type = "atomic"  ← 现有：触发 Actor 执行
  type = "user_input" ← 现有：HITL 等待用户输入

  "plan" task 的字段语义：

  ┌─────────────┬───────────────────────────────────────────────────────┐
  │    字段     │                         含义                          │
  ├─────────────┼───────────────────────────────────────────────────────┤
  │ title       │ "Plan: {session.goal[:50]}"                           │
  ├─────────────┼───────────────────────────────────────────────────────┤
  │ description │ 本轮规划的背景说明（可为空）                          │
  ├─────────────┼───────────────────────────────────────────────────────┤
  │ result      │ Planner 产出的任务数量摘要，如 "Created 3 tasks"      │
  ├─────────────┼───────────────────────────────────────────────────────┤
  │ outputs     │ {"planned_task_ids": [...]} 记录派生的 atomic task_id │
  └─────────────┴───────────────────────────────────────────────────────┘

  ---
  三、"plan" task 的创建时机

  ┌──────────────────┬───────────────────────────────────┬───────────────────────────────────────────────────────┐
  │       场景       │              创建者               │                       触发条件                        │
  ├──────────────────┼───────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ Session 初始创建 │ SessionManager.create_session()   │ 固定在创建 session 后写入一个 plan task（PENDING）    │
  ├──────────────────┼───────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ 重新规划         │ AgentLoop（Observe 后）           │ Observe 返回 done=False 时，loop 自动创建新 plan task │
  ├──────────────────┼───────────────────────────────────┼───────────────────────────────────────────────────────┤
  │ Session 恢复     │ SessionManager.continue_session() │ 用户发送新消息重启 loop，写入新 plan task             │
  └──────────────────┴───────────────────────────────────┴───────────────────────────────────────────────────────┘

  SessionManager 不再由 Planner 直接产出任务，初始任务队列只有一个 plan task。

  ---
  四、AgentLoop 主循环结构

  while True:
      ① Guard check（token_budget、turns_used）

      ② 取队列：
         pending = task_svc.list_pending(session_id)   # 按 created_at 升序
         if not pending:
             session → SUCCEEDED（无任务可执行）
             break

      ③ 分支判断：
         next_task = pending[0]

         ┌─ type == "plan"   →  PLAN 分支（见第五节）
         └─ type == "atomic" →  ACT 分支（见第六节）

  ---
  五、PLAN 分支

  task_svc.transition(plan_task.id, "ACTIVE")

  Reason()  →  构建 ReasoningContext

  Plan()    →  LLM 调用 submit_plan → PlannedTask[]

  turns_used += 1；AgentStore.save()

  if PlannedTask[] 为空：
      task_svc.finish(plan_task.id, result="Goal complete, no tasks needed")
      session → SUCCEEDED
      break

  for each PlannedTask:
      task_svc.create(type="atomic", title=..., description=..., inputs={skill_name})

  task_svc.finish(
      plan_task.id,
      result=f"Created {n} tasks",
      outputs={"planned_task_ids": [...]},
  )

  # 本轮结束，不执行 Act，不执行 Observe
  # 下次迭代 pending[0] 变为第一个 atomic task → 进入 ACT 分支
  continue

  注意：PLAN 分支不调用 Observe。turns_used 只在 PLAN 轮递增，保持 max_turns = 规划轮数上限的语义不变。

  ---
  六、ACT 分支

  pending_atomic = [t for t in pending if t.type == "atomic"]

  Reason()  →  构建 ReasoningContext（刷新上下文）

  Act()     →  对 pending_atomic 中每个 task 顺序执行：
                 task PENDING → ACTIVE → FINISHED/FAILED
               返回 (results, hitl_triggered)

  if hitl_triggered:
      return   # loop 暂停，session = WAITING_INPUT
               # loop_mode 由任务队列决定，无需 flag

  Observe()  →  ObserverVerdict { done, summary, reasoning }

  写摘要 → Memory + Blackboard
  触发滚动摘要检查

  if done:
      session → SUCCEEDED
      break
  else:
      # 创建下一轮 plan task，驱动重新规划
      task_svc.create(
          type="plan",
          title=f"Re-plan (turn {turns_used})",
          description="Previous tasks completed; re-evaluate and plan next steps.",
      )
      # 本轮结束，下次迭代进入 PLAN 分支
      continue

  ---
  七、任务队列的时序示意

  Session 创建
    └─ [plan_0: PENDING]

  Loop 第 1 轮（PLAN）
    └─ plan_0: PENDING→ACTIVE→FINISHED
       atomic_1, atomic_2, atomic_3: 创建为 PENDING
    → [atomic_1: PENDING, atomic_2: PENDING, atomic_3: PENDING]

  Loop 第 2 轮（ACT）
    └─ atomic_1, 2, 3: PENDING→ACTIVE→FINISHED
       Observe: done=False → 创建 plan_1
    → [plan_1: PENDING]

  Loop 第 3 轮（PLAN）
    └─ plan_1: PENDING→ACTIVE→FINISHED
       atomic_4, atomic_5: 创建为 PENDING
    → [atomic_4: PENDING, atomic_5: PENDING]

  Loop 第 4 轮（ACT）
    └─ atomic_4, 5: FINISHED
       Observe: done=True → session SUCCEEDED

  ---
  八、对现有代码的影响

  ┌───────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────┐
  │               位置                │                                            变更                                            │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ Task.type                         │ 合法值新增 "plan"（文档注释）                                                              │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ TaskService                       │ 新增 list_pending(session_id) 方法，返回 PENDING tasks 按 created_at 排序                  │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ SessionManager.create_session()   │ 末尾调用 task_svc.create(type="plan", ...) 写入初始 plan task                              │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ SessionManager.continue_session() │ 重启 loop 前写入新 plan task                                                               │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ AgentLoop.run()                   │ 主循环改为：取 pending → 分支 → PLAN 或 ACT                                                │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ AgentLoop._act_all()              │ 入参从 TaskPlan 改为 list[Task]（已有 PENDING task 列表）；移除内部 task_svc.create() 调用 │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ LoopGuard                         │ 不新增任何字段（mode 完全由任务队列推导）                                                  │
  ├───────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────┤
  │ SubAgentRunner                    │ 不变（sub-agent 直接调 Actor，不走此 loop）                                                │
  └───────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  九、崩溃恢复

  ┌────────────────────────────────────────────┬──────────────────────────┬──────────────────────────────────────────────────────────────────┐
  │                 崩溃发生在                 │      重启后队列状态      │                            Loop 行为                             │
  ├────────────────────────────────────────────┼──────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ PLAN 分支中途（plan task ACTIVE，无        │ plan task 卡在 ACTIVE    │ 需要重置为 PENDING 或跳过；可在 loop 启动时检查 ACTIVE task      │
  │ atomic）                                   │                          │ 并重置                                                           │
  ├────────────────────────────────────────────┼──────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ PLAN 完成、ACT 未开始                      │ atomic tasks 全为        │ 直接进入 ACT 分支，无需重新 Plan                                 │
  │                                            │ PENDING                  │                                                                  │
  ├────────────────────────────────────────────┼──────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ ACT 中途（部分 atomic FINISHED）           │ 剩余 atomic 为 PENDING   │ 继续执行剩余 PENDING tasks                                       │
  ├────────────────────────────────────────────┼──────────────────────────┼──────────────────────────────────────────────────────────────────┤
  │ Observe 后创建 plan task 前                │ 无 PENDING tasks         │ pending 为空 → session SUCCEEDED（可接受，或加兜底逻辑）         │
  └────────────────────────────────────────────┴──────────────────────────┴──────────────────────────────────────────────────────────────────┘