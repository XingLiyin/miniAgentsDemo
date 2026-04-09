# 新增 mark_task_complete
app/tools/builtins.py

  新增 mark_task_complete 工具（不注册到 BuiltinToolProvider），两个参数：
  - summary: str — 完成情况说明
  - success: bool = True — 成功/失败标志

  app/runtime/actor.py

  - act()：mark_task_complete 和 request_human_input 始终加入工具列表（不再受 ctx.relevant_tools    
  为空的约束）
  - LLM 无 tool calls（纯文本响应）→ 触发 _handle_task_completion_hitl（而非原来的直接 finish）     
  - mark_task_complete(success=True) → _finish_task
  - mark_task_complete(success=False) → _fail_task，立即返回失败结果
  - max_rounds 耗尽也触发 _handle_task_completion_hitl
  - 新增 _handle_task_completion_hitl：创建 type=task_completion_confirm 的 user_input
  任务，把任务标题和执行输出存入 inputs
  - 新增 _fail_task：调用 task_svc.fail() 并返回 success=False 的 ActorResult

  app/runtime/agent_loop.py

  _act_all 中 result.success=False 时 break，结束本轮任务循环，进入全局 Observe

  frontend/src/pages/SessionsPage.tsx

  新增 TaskCompletionConfirm 组件，检测到 task_completion_confirm 类型时渲染：
  - 展示任务标题 + LLM 执行输出
  - "已完成"按钮 → 发送确认消息，Planner 继续推进
  - "未完成，需重试" → 展开文本框让用户填写反馈 → 发送
  "用户表示任务未完成，请重试。用户补充说明：…"，写入对话历史，下一轮 Actor 带着新上下文重试该任务 

 
 # 新增 session_delete
 存储层（各加 delete 方法）：session_store、agent_store、task_store、tool_call_store（unlink）、memory_store、blackboard_store（rmtree）  

  服务层：SessionService.delete()、MemoryService.delete_session()

  编排层：SessionManager 注入 task_store、tool_call_store、blackboard_store，新增 delete_session() 按顺序清理所有关联数据，最后删 session  
  文件

  API：DELETE /api/v1/sessions/{id} → 204

  前端：
  - sessionsApi.delete(id)
  - SessionCard hover 时右上角出现垃圾桶图标，点击弹 window.confirm，删除后刷新列表
  - 若删的是当前选中的 session，自动清空右侧面板

# hitl机制只由actor触发

影响范围

  去掉 type 和 prompt 后，所有依赖它们的代码都要清理：

  ┌───────────────────────┬────────────────────────────────────────────────────────────┐  
  │         文件          │                            改动                            │  
  ├───────────────────────┼────────────────────────────────────────────────────────────┤  
  │ runtime/types.py      │ PlannedTask 删除 type、prompt 字段；Literal import         │  
  │                       │ 可同步移除                                                 │  
  ├───────────────────────┼────────────────────────────────────────────────────────────┤  
  │ runtime/planner.py    │ submit_plan 注解描述去掉 type/prompt；_parse_task_plan     │  
  │                       │ 去掉这两个字段的解析和 type 校验                           │  
  ├───────────────────────┼────────────────────────────────────────────────────────────┤  
  │ runtime/agent_loop.py │ 删除 _pause_for_user_input 方法（无人调用）；_act_all      │  
  │                       │ 里已无 user_input 分支，无需改动                           │  
  └───────────────────────┴────────────────────────────────────────────────────────────┘  

  PlannedTask 改后结构

  @dataclass
  class PlannedTask:
      title: str
      description: str
      skill_name: str | None = None

  逻辑上的变化

  - Plan 阶段：只决定做什么、用哪个 skill，不决定是否 HITL
  - Act 阶段：LLM 在执行任务时自行判断是否需要调用 request_human_input，HITL 完全由 Actor 
  内联处理

  _pause_for_user_input 是旧架构遗留方法（Planner 直接输出 user_input
  类型任务时调用），现在 _act_all 里已不再有相关分支，可以直接删掉。

# 新增 生命周期管理 
共修改/创建 12 个文件：

```
  ┌─────────────────────────┬─────────────────────────────────────────────────────────────────────┐
  │          文件           │                                变更                                 │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ domain/models/task.py   │ 新增 dag_deps、parent_task_id、retry_count 字段；注释加 SUSPENDED   │
  │                         │ 状态                                                                │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │                         │ 新增                                                                │
  │ domain/models/agent.py  │ has_spawn_permission、spawn_depth、parent_task_id、spawned_task_ids │
  │                         │  字段；注释加 WAITING 状态                                          │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ domain/state_machine.py │ Task 加入 ACTIVE↔SUSPENDED 转换；Agent 加入 RUNNING↔WAITING 转换    │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ domain/events/event_typ │ 新增 7                                                              │
  │ es.py                   │ 个事件常量（AGENT_WAITING、AGENT_RESUME、SPAWN_*、LIFECYCLE_*）     │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ config/settings.py      │ 新增 5 个 LM 配置项                                                 │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ orchestrator/lifecycle_ │ 全新：LMState、AgentMeta、SpawnPlanItem、SpawnResult、LifecycleMana │
  │ manager.py              │ ger                                                                 │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ runtime/sub_agent_runne │ 全新：SubAgentRunner，封装 root loop 和 sub-agent 单任务执行        │
  │ r.py                    │                                                                     │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ tools/builtins.py       │ 新增 spawn_agents 工具 schema（不注册到 provider，Actor 特殊处理）  │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ runtime/actor.py        │ 注入 lifecycle_manager；添加 spawn_agents 分支和                    │
  │                         │ _handle_spawn_agents()                                              │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ orchestrator/session_ma │ 注入 lifecycle_manager；create_session 调用                         │
  │ nager.py                │ lm.init_session()；schedule_loop 优先走 LM                          │
  ├─────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ api/v1/deps.py          │ 新增 get_reasoner、get_actor、get_sub_agent_runner、get_lifecycle_m │
  │                         │ anager；循环依赖通过延迟注入打破                                    │
  └─────────────────────────┴─────────────────────────────────────────────────────────────────────┘
```
```
  关键执行流（spawn_agents 路径）：
  root AgentLoop → Actor → LLM 调用 spawn_agents
    → Actor._handle_spawn_agents()
      → LM.handle_spawn_requested()          ← 持锁：检查权限、创建子任务、建 DAG
        → 挂起 requesting_task (SUSPENDED)
        → agent → WAITING
        → _drain_queue() → SubAgentRunner.run_sub_async() × N  ← 启动 daemon 线程
      → resume_event.wait(timeout=3600)      ← 释放锁，阻塞

  [sub-agent 线程]
    → Reasoner.reason() → Actor.act()
      → task_svc.finish() → 发布 TASK_FINISHED
        → LM._on_task_finished()             ← 持锁：更新 DAG
          → _try_resume_waiting_agent_locked()
            → meta.waiting_for 清空 → resume_event.set()  ← 唤醒 root 线程

  [root 线程恢复]
    ← handle_spawn_requested 返回 SpawnResult（含子任务结果）
    ← Actor 注入 tool result，LLM 继续执行
    → mark_task_complete → 任务完成
```

# plan 分支

app/runtime/types.py                                                                                                                            
  - ReasoningContext 新增：mode（必填首位）、mode_system_prompt（默认 ""）、plan_task（默认 None）、atomic_tasks（默认 []）                     
  - 用 TYPE_CHECKING 导入 Task，避免循环依赖

  app/runtime/reasoner.py
  - reason(session, agent) → reason(session, agent, pending_tasks: list[Task])
  - 内部按 pending_tasks[0].type 分发到 _reason_for_plan / _reason_for_act
  - 公共数据获取（memory/blackboard/token）提取为 _fetch_base()
  - _build_plan_prompt() 构建规划模式 prompt（含 skills 列表）
  - _build_act_prompt() 构建执行模式 prompt（含 tool awareness），取代了原先 Actor 中的相关代码

  app/runtime/agent_loop.py
  - 主循环：reasoner.reason() 调用前移到分支之前，统一入口
  - 分支条件从 next_task.type == "plan" 改为 ctx.mode == "plan"
  - _do_plan(plan_task, session, agent) → _do_plan(ctx, agent)，从 ctx.plan_task 取任务
  - _do_act(pending, session, agent) → _do_act(ctx, session, agent)，从 ctx.atomic_tasks 取任务，移除内部 reasoner.reason() 调用

  app/runtime/planner.py
  - _build_system_prompt 简化为两行：agent.system_prompt + ctx.mode_system_prompt（fallback 到原 _FALLBACK_SYSTEM_PROMPT）
  - 移除了原先 skills 列表拼接和 "Your job..." 指令（均已移到 Reasoner）

  app/runtime/actor.py
  - _build_system_prompt 改用 ctx.mode_system_prompt 作为 Layer 2
  - 移除了 tool awareness section（已由 Reasoner 注入 ctx）
  - 保留 skill instructions（Layer 3，per-task）和 Required Tool Protocol（Layer 4）

  app/runtime/sub_agent_runner.py
  - _run_sub_safe 中 reason(session, agent) → reason(session, agent, [task])

  tests/test_phase_implementations.py
  - 两处 _make_ctx() 补充 mode="plan" / mode="act" 参数