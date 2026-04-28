 Task 全生命周期                                                                                                                  
                                                                                                                                   
  1. 产生（创建）                                                                                                                  
                                                                                                                                   
  两条路径：                                                                                                                       
                                                                                                                                   
  - Session 启动时：SessionManager 创建初始 task（通常是 plan 类型），状态 PENDING                                                 
  - Spawn 时（control_tools.py）：Agent 执行期间调用 spawn_agents / create_task 工具，创建子 task，parent_task_id 指向当前         
  SUSPENDED 的父 task                                                                                                              
                                                                                                                                   
  Task 创建后发布 TASK_CREATED 事件 → TaskManager.on_task_created 将其 push 进 TaskQueue。                                         
                                                                                                                                   
  ---                                                                                                                              
  2. 入队（TaskQueue）                                                                                                             

  TaskQueue.push 检查 dag_deps：

  - deps 已满足 → 进 _ready（栈，LIFO）
  - deps 未满足 → 进 _blocked（集合，等待）

  ---
  3. 调度执行

  触发点有三个：
  1. Session 启动：TaskManager.start_session → queue.pop()
  2. 上一个 task 完成：on_task_finished → queue.pop()
  3. 上一个 task 失败：on_task_failed → 重试或 queue.pop()

  pop() 拿到就绪 task 后，_dispatch_next 依次：
  1. task_svc.transition(task_id, "ACTIVE")
  2. lifecycle_manager.prepare_executor()（复用或 spawn agent）
  3. lifecycle_manager.run_agent() → 起线程调用 AgentLoop.run()

  ---
  4. 执行（AgentLoop）

  Reasoner.reason()   →  构建 ctx（system prompt、历史记忆、task 信息）
  Actor.act()         →  执行（plan task → Planner；atomic task → 多轮 tool use）

  执行期间 task 可能变为 SUSPENDED（spawn 子任务后父 task 挂起，AgentLoop 直接 return）。

  否则 task 进入 TO_BE_OBSERVED，由 Observer 裁决：

  ┌──────────────────┬───────────┬───────────────────────────────────────────────┐
  │  Observer 裁决   │ task 状态 │                     后续                      │
  ├──────────────────┼───────────┼───────────────────────────────────────────────┤
  │ success          │ FINISHED  │ 结果写 Blackboard，发 TASK_EXECUTION_FINISHED │
  ├──────────────────┼───────────┼───────────────────────────────────────────────┤
  │ failed           │ FAILED    │ 抛 AppError，发 TASK_EXECUTION_FAILED         │
  ├──────────────────┼───────────┼───────────────────────────────────────────────┤
  │ active           │ PENDING   │ 任务重新入队，下一轮 Actor 继续               │
  ├──────────────────┼───────────┼───────────────────────────────────────────────┤
  │ needs_user_input │ PENDING   │ HITL 暂停                                     │
  └──────────────────┴───────────┴───────────────────────────────────────────────┘

  ---
  5. 完成后的调度决策（on_task_finished）

  notify_completed(finished_task_id)
    → _blocked 中 deps 已满足的 task 提升到 _ready

  _try_resume_parent()
    → 若当前 task 有 parent_task_id：
        所有兄弟 task 全部 terminal 且无失败 → parent RESUMED，重新入队
        有失败 → cascade fail parent

  queue.pop() 取下一个 task → _dispatch_next()
    → 若队列空：session → SUCCEEDED
    → 若仍有 blocked task：等待其他 active task 完成后触发

  ---
  6. 失败处理（on_task_failed）

  1. task_svc.fail() 标记 FAILED
  2. _cascade_fail：递归 FAILED 所有依赖它的 PENDING task
  3. retry_count < max_retries：task_svc.retry() 重置状态，重新 push 入队
  4. 超重试 / 队列空 → session → FAILED

  ---
  状态流转总览

  PENDING
    ↓ pop + dispatch
  ACTIVE
    ↓ AgentLoop.run()
  TO_BE_OBSERVED ─→ Observer
    ├─ success    → FINISHED  ──→ session SUCCEEDED
    ├─ active     → PENDING   ──→ 重新入队
    ├─ failed     → FAILED    ──→ 重试 or cascade
    └─ suspended  → SUSPENDED ──→ 等子任务完成后 RESUME → PENDING → 重新入队

  Task 状态变更的全部触发点                                                                                                        
                                                                                                                                   
  Tool 执行期间（control_tools.py）                                                                                                
                  
  这是最主要的「额外」变更路径，Agent 调用控制工具时直接写 task 状态：

  ┌───────────────────────────────────────────┬──────────────────────────────────────────────────────────────────────┐
  │                   工具                    │                            触发的状态变更                            │
  ├───────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ submit_plan                               │ 父 task → SUSPENDED；批量创建子 task（PENDING）                      │
  ├───────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ submit_task                               │ 父 task → SUSPENDED；创建单个子 task（PENDING）                      │
  ├───────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ submit_task_assessment                    │ → FINISHED / FAILED / PENDING（active 路径）                         │
  ├───────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ submit_task_assessment (needs_user_input) │ 阻塞等用户确认 → FINISHED 或 FAILED                                  │
  ├───────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ replan                                    │ 当前 task → FINISHED；批量 cancel_pending（所有 PENDING → CANCELED） │
  ├───────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────┤
  │ _apply_reviews（Observer 内嵌）           │ 兄弟 task → PENDING（reopen）或 FINISHED（skip）                     │
  └───────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────┘

  ---
  Actor 阶段（actor.py:53）

  if task.status == "PENDING":
      self._task_svc.transition(task.id, "ACTIVE")

  task 在 active 路径重新入队后状态是 PENDING，下一轮 Actor 开始时再次将其激活为 ACTIVE。

  ---
  Observer 降级规则（observer.py:_rule_observe）

  当 Observer LLM 不可用时走规则兜底，直接写 task 状态：
  - token 用量 > 90% → FINISHED
  - result.success → FINISHED
  - 否则 → FAILED

  ---
  TaskManager 调度层（task_manager.py）

  ┌───────────────────────┬─────────────────────────────────────────────────────────────────┐
  │         场景          │                              变更                               │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ _dispatch_next        │ PENDING → ACTIVE                                                │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ on_task_failed + 重试 │ FAILED → PENDING（retry）                                       │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ _try_resume_parent    │ SUSPENDED → PENDING（resume）；或 SUSPENDED → FAILED（cascade） │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ _cascade_fail         │ 递归将依赖失败 task 的所有 PENDING → FAILED                     │
  ├───────────────────────┼─────────────────────────────────────────────────────────────────┤
  │ spawn_daemon_task     │ PENDING → ACTIVE（daemon 直接激活，不走队列）                   │
  └───────────────────────┴─────────────────────────────────────────────────────────────────┘

  ---
  AgentLoop（agent_loop.py）

  Actor 完成后若 task 未被工具改变状态：
  self._task_svc.to_be_observed(task_id)  # → TO_BE_OBSERVED

  ---
  完整状态机合法转换

  PENDING        → ACTIVE, CANCELED, FINISHED, TO_BE_OBSERVED
  ACTIVE         → FINISHED, FAILED, CANCELED, SUSPENDED, TO_BE_OBSERVED
  SUSPENDED      → ACTIVE, PENDING, FAILED, CANCELED
  TO_BE_OBSERVED → SUSPENDED, FINISHED, FAILED, PENDING
  FINISHED       → PENDING   （reopen）
  FAILED         → PENDING   （retry）
  CANCELED       → （终态）

  最关键的一点：task 状态最主要由 submit_task_assessment 这个控制工具在 Actor 运行期间写入，AgentLoop 本身只负责转
  TO_BE_OBSERVED，并在之后读取结果做分发决策——它不直接决定 FINISHED/FAILED。