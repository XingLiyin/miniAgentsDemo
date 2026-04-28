● Agent 生命周期管理机制总结                                                                                                       
                                                                                                                                   
  核心数据结构                                                                                                                     
                                                                                                                                   
  AgentMeta（内存，per-session）                                                                                                   
  agent_id, parent_id, task_id, spawn_depth, status                                                                                
  每个活跃 agent 在 LMState.agent_registry 中有一条 meta，是生命周期决策的依据。
                                                                                                                                   
  LMState（内存，per-session）                              
  root_agent_id, agent_registry, concurrent_agents, concurrent_tasks

  Agent 持久化（agent_store）
  存 soul/role/工具配置、status 等，loop 运行时读写，AgentMeta 里的 status 在每次 loop 结束后从此同步。

  ---
  Agent Status 流转

  IDLE ──→ RUNNING ──→ FINISHED   （正常完成）
                   └──→ WAITING    （task SUSPENDED，等子任务）
                          └──→ RUNNING  （task 恢复，重新执行）
                                 └──→ FINISHED

  状态机已有 WAITING → RUNNING 这条边，重入合法。

  ---
  Root Agent 的创建与注册

  API 请求
    → SessionManager.create_session()
        → Agent(name="root", status="IDLE",
                spawn_depth=0, has_spawn_permission=True)
        → agent_store.save()
        → lifecycle_manager.init_session()        # 初始化 LMState
        → lifecycle_manager.register_root_agent() # 写入 registry，status="RUNNING"
        → task_manager.init_session()             # 初始化 TaskQueue
        → task_svc.create(初始 task)             # 发 TASK_CREATED → 入队
        → schedule_loop() → task_manager.start_session()

  Root agent 是唯一 spawn_depth=0 的 agent，整个 session 期间不被回收，仅在 release 时清理。

  ---
  Sub-Agent 的 Spawn 全流程

  触发点：task_manager._dispatch_next() → prepare_executor(use_subagent=True)

  prepare_executor()
    1. _settle_executor(finished_agent_id)
         ├─ depth=0（root）→ 直接返回
         ├─ status=WAITING → 直接返回（保留，作为 parent）
         └─ status=FINISHED → 回收，返回 meta.parent_id

    2. base_meta.status == WAITING 分支
         ├─ task_id == base_meta.task_id → 恢复自己的 suspended task
         │    base_meta.status = RUNNING，直接返回 base_executor
         └─ 否则 → 强制 use_subagent=True（子任务必须 spawn）

    3. _check_spawn_permission(base_executor)
         ├─ spawn_depth >= max_spawn_depth → 拒绝，inline fallback
         ├─ concurrent_agents 超限 → 拒绝
         └─ token > 90% → 拒绝

    4. spawn_depth = base_meta.spawn_depth + 1

    5. _instantiate_sub_agent()
         ├─ 从 template 或 parent 继承配置
         ├─ Agent(status="IDLE",
                 spawn_depth=N,
                 has_spawn_permission=(N < max_spawn_depth))
         ├─ agent_store.save()
         └─ _copy_memory()（inherit_memory=True 时）

    6. registry[sub_agent_id] = AgentMeta(parent_id=base_executor, depth=N, status=RUNNING)
       concurrent_agents += 1

  ---
  Loop 执行与状态同步

  lifecycle_manager.run_agent(session_id, agent_id, task_id)
    → registry[agent_id].task_id = task_id
    → daemon thread → _execute()
        → agent_loop.run()
            ├─ agent.status = "RUNNING"（loop 开始）
            ├─ Reasoner.reason() → Actor.act() → Observer.observe()
            └─ finally:
                 task = task_svc.get(task_id)
                 agent.status = "WAITING"   if task.SUSPENDED
                              = "FINISHED"  otherwise
                 agent_store.save()

        → _sync_meta_status()              # 同步 agent_store.status → meta.status
        → publish TASK_EXECUTION_FINISHED  # 触发 TM 调度下一个 task

  ---
  树状结构下的完整流程示例

  root → spawn A(depth=1) 执行 T0（plan）
    A 调用 submit_plan → T0 SUSPENDED
    finally: A.status = WAITING
    _sync_meta: meta[A].status = WAITING

    TASK_EXECUTION_FINISHED(A, T0)
      queue.pop() → T1
      prepare_executor(finished=A, task=T1)
        _settle_executor(A): WAITING → 返回 A
        A.status=WAITING, T1≠T0 → 强制 spawn
        spawn B(depth=2, parent=A)
      run_agent(B, T1)

    B 完成 T1 → B.status=FINISHED
      prepare_executor(finished=B, task=T2)
        _settle_executor(B): FINISHED → 回收 B，返回 B.parent=A
        A.status=WAITING, T2≠T0 → 强制 spawn
        spawn C(depth=2, parent=A)
      run_agent(C, T2)

    C 完成 T2 → _try_resume_parent → T0 resume → 入队
      prepare_executor(finished=C, task=T0)
        _settle_executor(C): FINISHED → 回收 C，返回 C.parent=A
        A.status=WAITING, T0==meta[A].task_id → 直接恢复
        meta[A].status = RUNNING，返回 A
      run_agent(A, T0)  ← A 重入，执行 observer 阶段

    A 完成 T0 → A.status=FINISHED
      queue 空 → release()
        遍历 registry → 回收 A（FINISHED）、root

  ---
  关键设计约束

  ┌──────────────────────────────────┬─────────────────────────────────────────────────────────┐
  │               规则               │                        实现位置                         │
  ├──────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ Root 永不中途回收                │ _settle_executor: depth=0 直接返回                      │
  ├──────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ WAITING agent 不回收             │ _settle_executor: status=WAITING 直接返回               │
  ├──────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ FINISHED sub-agent 回直接 parent │ _settle_executor: 返回 meta.parent_id                   │
  ├──────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ 子任务必须 spawn（不能 inline）  │ prepare_executor: WAITING branch 强制 use_subagent=True │
  ├──────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ 深度限制控制树的层数             │ _check_spawn_permission + has_spawn_permission          │
  ├──────────────────────────────────┼─────────────────────────────────────────────────────────┤
  │ Session 结束全清                 │ release: 遍历回收所有 registry 条目                     │
  └──────────────────────────────────┴─────────────────────────────────────────────────────────┘

  # 后续扩展点
    场景一：按角色路由任务（改动小）                                                                                                 
                                                                                                                                   
  如果"委派给同事"的含义是 task 指定由哪种模板的 agent 来处理，当前架构已经有钩子：                                                
   
  task.settings = {                                                                                                                
      "use_subagent": True,                                 
      "subagent_template": "data_analyst"  # 路由到指定模板
  }

  prepare_executor 按 subagent_template spawn 对应 agent，本质上就是按角色委派。只是每次都重新 spawn，没有复用。

  扩展方向：在 LMState 里加一个 peer pool，按模板名预热 agent，路由时复用而非重建：

  @dataclass
  class LMState:
      peer_pool: dict[str, str] = field(default_factory=dict)
      # template_name → agent_id（长期驻留）

  prepare_executor 先查 pool，命中则复用，未命中则 spawn 并注册进 pool。这个改动相对局部。

  ---
  场景二：真正的并行多 agent（改动大）

  如果"同事协作"意味着 多个 agent 同时活跃、并发执行不同 task，当前架构有几个根本性限制：

  1. TaskQueue 是单路 LIFO

  queue.pop() 一次只取一个 task，整个调度是串行的。要并行，需要 pop_batch() + 并发 dispatch。

  2. on_task_finished 是串行回调

  目前一个 task 完成 → 触发下一个 task，是接力跑而非并跑。并行场景下需要在 session 启动时一次 dispatch 多个
  task，各自跑各自的线程。

  3. root 是单点

  root_agent_id: str 是整棵树的根，并行需要扩展为多个对等 root：

  root_agent_ids: list[str] = []

  但随之而来的问题是：谁来"汇总"结果？谁决定 session 是否结束？目前这个角色由 root agent 的最终 task 承担。

  4. 内存隔离与共享

  现在只有 parent→child 的单向记忆复制。Peer 之间共享上下文需要新机制（blackboard 已有，但没有 agent 主动订阅的路由）。

  ---
  现有的真实扩展点

  ┌───────────────────────────────────────────┬──────────────────────────────────┬──────────────────────────────────────┐
  │                  扩展点                   │               位置               │               支撑什么               │
  ├───────────────────────────────────────────┼──────────────────────────────────┼──────────────────────────────────────┤
  │ subagent_template 路由                    │ task.settings + prepare_executor │ 角色路由、专家分工                   │
  ├───────────────────────────────────────────┼──────────────────────────────────┼──────────────────────────────────────┤
  │ concurrent_agents / concurrent_tasks 计数 │ LMState                          │ 并发上限已预留，只差并发 dispatch    │
  ├───────────────────────────────────────────┼──────────────────────────────────┼──────────────────────────────────────┤
  │ AgentMeta.parent_id 树结构                │ lifecycle_manager                │ 可扩展为 DAG（多父）                 │
  ├───────────────────────────────────────────┼──────────────────────────────────┼──────────────────────────────────────┤
  │ blackboard_svc                            │ 已有                             │ peer 间结果共享的天然载体            │
  ├───────────────────────────────────────────┼──────────────────────────────────┼──────────────────────────────────────┤
  │ dag_deps                                  │ Task                             │ 已支持 task 间依赖，是并行调度的前提 │
  └───────────────────────────────────────────┴──────────────────────────────────┴──────────────────────────────────────┘

  ---
  结论

  按角色路由：当前架构可以平滑扩展，改 LMState 加 peer pool + prepare_executor 加复用逻辑，不动调度主干。

  真正并行：需要改造调度核心——TaskQueue 支持并发 pop、_dispatch_next 支持批量 dispatch、on_task_finished 支持并发收口。dag_deps
  和计数器已经为此预留了语义，但执行层还是串行的，这是最大的改造点。