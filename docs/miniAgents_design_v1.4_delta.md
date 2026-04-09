  重新设计方案

  核心原则

  1. 每次循环执行一个 task，loop 计数就是执行次数
  2. Actor 统一入口，plan/atomic 在内部区分
  3. Observer 每轮必跑，plan 阶段也做目标评估
  4. TaskManager 负责推进，决定下一步创建什么 task
  5. AgentLoop 主循环扁平，无嵌套方法

  ---
  一、主循环结构

  while True:
      session = get(session_id)
      token_budget check

      turns_used += 1           ← 统计所有执行次数，不区分 plan/act
      if turns_used > max_turns → MAX_TURNS_EXCEEDED

      task = task_mgr.next_task(session_id)
      if task is None → SUCCEEDED, break

      ctx = reasoner.reason(session, agent, task)
      result = actor.act(task, ctx, agent)

      if result.hitl_task_id → return  (暂停)

      verdict = observer.observe(session, result, ctx)
      memory + blackboard append(verdict.summary)
      if should_summarize → do_summarize

      if verdict.done → SUCCEEDED, break

      task_mgr.advance(session_id, agent.id, task, result)

  主循环完全扁平，无 _do_plan / _do_act / _act_all 分层。

  ---
  二、ReasoningContext / Reasoner 小调整

  reasoner.reason() 当前接收 list[Task]，改为接收单个 task（每轮只处理一个）：

  def reason(self, session: Session, agent: Agent, task: Task) -> ReasoningContext:
      if task.type == "plan":
          return self._reason_for_plan(session, agent, task)
      else:
          return self._reason_for_act(session, agent, task)

  ReasoningContext.atomic_tasks: list[Task] 也随之简化为 current_task: Task（plan/act 统一用同一字段）：

  @dataclass
  class ReasoningContext:
      mode: Literal["plan", "act"]
      goal: str
      recent_messages: list[dict]
      summary_text: str
      blackboard_snippets: list[str]
      relevant_tools: list[Any]       # act 模式加载
      relevant_skills: list[SkillMeta]  # plan 模式加载
      mode_system_prompt: str = ""
      current_task: Task | None = None  # 当前待执行 task（plan or atomic）
      token_estimate: int = 0

  ---
  三、Actor：统一入口，内部区分任务类型

  Actor 新增 planner: Planner 依赖，act() 按 task.type 分发：

  def act(self, task: Task, ctx: ReasoningContext, agent: Agent) -> ActorResult:
      if task.type == "plan":
          return self._act_as_planner(task, ctx, agent)
      return self._act_as_executor(task, ctx, agent)  # 当前逻辑，改名

  _act_as_planner()（吸收当前 AgentLoop._do_plan 核心逻辑）：

  def _act_as_planner(self, task, ctx, agent) -> ActorResult:
      self._task_svc.transition(task.id, "ACTIVE")
      plan = self._planner.plan(ctx, agent)

      if not plan.tasks:
          self._task_svc.finish(task.id, result="Goal already complete, no tasks needed")
          return ActorResult(
              task_id=task.id, success=True,
              output="No further tasks needed — goal may already be achieved.",
              actor_mode="plan", plan_task_count=0,
          )

      atomic_ids = []
      for pt in plan.tasks:
          inputs = {"skill_name": pt.skill_name} if pt.skill_name else {}
          t = self._task_svc.create(
              session_id=task.session_id, agent_id=agent.id,
              task_type="atomic", title=pt.title,
              description=pt.description, inputs=inputs,
          )
          atomic_ids.append(t.id)

      self._task_svc.finish(
          task.id,
          result=f"Created {len(atomic_ids)} atomic tasks",
          outputs={"planned_task_ids": atomic_ids},
      )
      return ActorResult(
          task_id=task.id, success=True,
          output=f"Planned {len(atomic_ids)} tasks: {', '.join(pt.title for pt in plan.tasks)}",
          actor_mode="plan", plan_task_count=len(atomic_ids),
      )

  Actor 不再直接操作 session 状态，由 AgentLoop 通过 Observer 的 verdict.done 判断。

  ActorResult 新增字段：

  actor_mode: Literal["text", "tool_use", "skill", "plan"] = "text"
  plan_task_count: int | None = None   # plan 模式专用；0 = 空计划

  ---
  四、Observer 适配单 task 结果

  入参从 list[ActorResult] 简化为单个结果，同时感知 plan 模式（规则降级路径）：

  def observe(self, session: Session, result: ActorResult, ctx: ReasoningContext) -> ObserverVerdict:

  LLM prompt 保持不变（看到"No further tasks needed"或"Planned N tasks"后能正确评估）。

  规则降级新增 plan 兜底：

  def _rule_observe(self, result, session, ctx) -> ObserverVerdict:
      if result.actor_mode == "plan" and result.plan_task_count == 0:
          return ObserverVerdict(done=True, summary="Goal achieved.", reasoning="rule: empty plan")
      if result.actor_mode == "plan":
          return ObserverVerdict(done=False, summary=result.output, reasoning="rule: plan created tasks")
      # 原有 atomic 规则...

  ---
  五、TaskManager 新增两个方法

  next_task(session_id) → Task | None

  返回第一个 PENDING task（plan 优先，因 created_at 升序已保证顺序）：

  def next_task(self, session_id: str) -> Task | None:
      tasks = self._task_svc.list_pending(session_id)
      return tasks[0] if tasks else None

  advance(session_id, agent_id, task, result) → None

  在 verdict.done=False 时调用，负责决定是否需要创建 re-plan task：

  def advance(self, session_id: str, agent_id: str, task: Task, result: ActorResult) -> None:
      if task.type == "plan":
          return  # Actor 已创建 atomic tasks，无需额外操作

      # atomic task 完成后检查是否还有待执行任务
      if not result.success:
          self._increment_failure_counter(session_id, task)
      else:
          self._reset_failure_counter(session_id)

      remaining = self._task_svc.list_pending(session_id)
      if not remaining:
          # 所有 atomic task 执行完毕，创建新 plan task 评估是否继续
          self._task_svc.create(
              session_id=session_id,
              agent_id=agent_id,
              task_type="plan",
              title="Re-plan: evaluate next steps",
              description="All current tasks completed. Re-evaluate the goal and plan next steps if needed.",
          )

  ---
  六、AgentLoop 构造函数变化

  # 移除
  planner: Planner

  # 新增
  task_manager: TaskManager

  Planner 依赖从 AgentLoop 转移到 Actor。

  ---
  七、deps.py 调整

  # 新增
  @lru_cache
  def get_planner() -> Planner:
      return Planner(llm_client=_get_llm_client())

  # Actor 注入 Planner
  @lru_cache
  def get_actor() -> Actor:
      return Actor(
          ...,
          planner=get_planner(),      # 新增
      )

  # AgentLoop 用 task_manager 替换 planner
  @lru_cache
  def get_agent_loop() -> AgentLoop:
      return AgentLoop(
          ...,
          task_manager=get_task_manager(),  # 替换原 planner
      )

  ---
  八、变更范围

  ┌──────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │             文件             │                                                         改动                                                         │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ types.py                     │ ActorResult 新增 plan_task_count，actor_mode 加 "plan"；ReasoningContext 将 plan_task/atomic_tasks 合并为            │
  │                              │ current_task                                                                                                         │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ reasoner.py                  │ reason() 接收单个 Task；_reason_for_act 不再接收 list                                                                │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ actor.py                     │ 新增 planner 依赖；act() 加 task.type 分发；新增 _act_as_planner()；原逻辑改名 _act_as_executor()                    │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ agent_loop.py                │ 主循环全面简化；删除 _do_plan/_do_act/_act_all；新增 task_manager 依赖；移除 planner 依赖；turns_used                │
  │                              │ 在循环顶部统一递增                                                                                                   │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ orchestrator/task_manager.py │ 新增 next_task()、advance()                                                                                          │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ runtime/observer.py          │ observe() 入参 results: list[ActorResult] → result: ActorResult；规则降级增加 plan 兜底                              │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ api/v1/deps.py               │ 新增 get_planner()；Actor 注入 planner；AgentLoop 注入 task_manager                                                  │
  ├──────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sub_agent_runner.py          │ reasoner.reason(session, agent, [task]) → reason(session, agent, task)                                               │
  └──────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  九、执行时序（一轮完整示例）

  Loop iteration 1（plan task）:
    turns_used: 0 → 1
    task_mgr.next_task() → plan task T1
    reasoner.reason(T1) → plan mode ctx（skills loaded，tools=[]）
    actor.act(T1) → _act_as_planner → Planner.plan() → 创建 T2/T3/T4 → T1 FINISHED
    observer.observe(result) → done=False（刚创建了子任务）
    task_mgr.advance(T1, result) → task.type="plan"，return（无需操作）

  Loop iteration 2（atomic task）:
    turns_used: 1 → 2
    task_mgr.next_task() → atomic task T2
    reasoner.reason(T2) → act mode ctx（tools loaded，skills=[]）
    actor.act(T2) → _act_as_executor → tool calls → mark_task_complete → T2 FINISHED
    observer.observe(result) → done=False（T3/T4 still pending）
    task_mgr.advance(T2, result) → remaining=[T3, T4]，不创建 re-plan

  Loop iteration 4（最后一个 atomic）:
    ...
    task_mgr.advance(T4, result) → remaining=[]，创建 re-plan task T5

  Loop iteration 5（re-plan）:
    actor.act(T5) → _act_as_planner → empty plan → ActorResult(plan_task_count=0)
    observer.observe() → done=True
    主循环 → SUCCEEDED

  ---