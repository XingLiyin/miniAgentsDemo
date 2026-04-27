# task suspend 机制修复
  Task Suspend 机制                                                                                                                  
                                                                                                                                     
  整个流程分三个阶段：                                                                                                               
                                                                                                                                     
  1. 触发挂起（control_tools.py:428）                       

  当 agent 调用 submit_task 工具创建子任务时：
  - 先创建子任务（PENDING 状态），parent_task_id 指向当前任务
  - 立刻将当前任务转为 SUSPENDED
  - 设置 task.actor_done = True，让 agent loop 正常退出当前迭代

  2. Agent Loop 识别挂起（agent_loop.py:95）

  每次 loop 迭代开始前检查任务状态：
  if task.status == "SUSPENDED":
      return  # 直接退出，不继续执行
  这样父任务的 actor 就停在那里，不再消耗资源。

  3. 自动恢复（lifecycle_manager.py:336）

  当某个子任务完成（FINISHED/FAILED/CANCELED）时，LifecycleManager 监听到事件：
  1. 找到该子任务的 parent_task_id
  2. 检查父任务是否为 SUSPENDED
  3. 关键条件：列出所有同一父任务下的子任务，全部进入终态后才 resume
  4. resume 将父任务打回 PENDING，重新入队调度

  ---
  状态流转简图：

  父任务: ACTIVE → (spawn_agents) → SUSPENDED → (所有子任务终态) → PENDING → ACTIVE
  子任务:                            PENDING → ACTIVE → FINISHED

  关键设计点： 挂起不是阻塞等待，而是"状态标记 + 事件驱动恢复"，父任务的 actor 不持有任何锁或线程，完全靠 LifecycleManager
  在子任务完成事件里触发恢复。

✻ Brewed for 38s                   
                                                       
❯ 父任务suspend的时机，在task里，导致原本的user                                                                                      
  prompt没有记录到memory中。重启后，组装message时直接把原本的prompt放在最后一条，时序有问题。你能理解我在说什么吗？先给一版修复方案  
                                                                                                                                     
● 理解了。让我看一下消息组装和 memory 存储的相关代码。                                                                               
                                                                                                                                     
  Read 2 files (ctrl+o to expand)                
                                                                                                                                     
● 现在看 Reasoner 和 Actor 如何组装消息。        
                                                                                                                                     
  Searched for 1 pattern, read 2 files (ctrl+o to expand)                   
                                                                                                                                     
● 现在看 prompt_builder.py 中 build_messages 的具体实现。                                                                            
                                                                                                                                     
  Searched for 1 pattern, read 1 file (ctrl+o to expand)                                                                             
                                                                                                                                     
● 问题我完全理解。让我梳理一下根因和修复方案：                                                                                       
                                                                                                                                     
  ---                                                                                                                                
  根因分析                                                                                                                           
                                                                                                                                     
  问题一：内存写入被跳过                                                                                                             
                                                                                                                                     
  agent_loop.py:95-96：                                     
  if task.status == "SUSPENDED":
      return   # ← 直接返回，跳过了后面 116-141 行的 memory write
  task.user_prompt 和 actor 做过的事（调用了 submit_task）都没有写入 memory。

  问题二：resume 时 build_messages 把 user_prompt 追加到最后

  prompt_builder.py:101-136：build_messages 每次都无条件把 task.user_prompt 追加为最后一条 user message。
  Resume 时，memory 里没有这个 task 的任何历史（因为问题一），于是拼出的 messages 变成：
  [旧历史（其他 task 的记录）]
  user: "Task Background: [子任务结果]\nCurrent message: 原始prompt"  ← 贴在最后
  模型看到"原始 prompt"出现在子任务结果之后，时序颠倒，且完全不知道自己已经 spawn 过子任务。

  ---
  修复方案（两处改动）

  Fix 1：agent_loop.py —— SUSPENDED 时同步写入 memory

  在 return 之前写入两条记录，把本次执行的上下文保留下来：

  # agent_loop.py  ~line 93
  task = self._task_svc.get(task_id)

  if task.status == "SUSPENDED":
      # ↓ 新增：把 user_prompt + 本次 actor 行为写入 memory
      if task.user_prompt:
          self._memory_svc.append_message(
              agent_id=agent_id, role="user",
              content=task.user_prompt,
              session_id=session_id, task_id=task_id,
          )
      spawn_note = _summarize_spawn(result)   # 从 result.tool_calls_made 里提取 submit_task 的 title
      self._memory_svc.append_message(
          agent_id=agent_id, role="assistant",
          content=spawn_note,
          session_id=session_id, task_id=task_id,
      )
      return

  _summarize_spawn 只是把 result 里 submit_task 调用的 title 拼成一句话，比如：

  ▎ "Delegated to sub-task: '分析竞品数据'（awaiting completion）"

  ---
  Fix 2：prompt_builder.py#build_messages —— 检测 resume 场景，改变最后一条消息

  Resume 判断依据：recent_messages 里存在 task_id == task.id 的记录（Fix 1 写入时已携带 task_id）。

  def build_messages(self, task, ctx):
      messages = []

      # 检测 resume（memory 里已有本 task 的历史）
      is_resume = any(m.get("task_id") == task.id for m in ctx.recent_messages)

      if is_resume:
          # 所有 memory 记录原样还原（包括 user_prompt + spawn note）
          for m in ctx.recent_messages:        # 不再 [:-1] 截断
              messages.append(LLMMessage(...))
          # 追加"子任务已完成，请继续"作为当前 user 轮
          resume_parts = []
          if ctx.blackboard_snippets:
              resume_parts.append("Sub-task results:\n" + "\n".join(f"- {s}" for s in ctx.blackboard_snippets))
          resume_parts.append("Sub-tasks completed. Please review the results and continue.")
          messages.append(LLMMessage(role="user", content="\n".join(resume_parts)))
      else:
          # 原有逻辑：历史 + 当前 user_prompt
          ...（现有代码不变）

      return messages

  Resume 后 messages 变成：
  [旧历史]
  user:      "原始 prompt"              ← 来自 memory（Fix 1 写入）
  assistant: "Delegated to sub-task X"  ← 来自 memory（Fix 1 写入）
  user:      "Sub-task results: [...]. Please continue."  ← Fix 2 新增
  时序正确，模型知道自己已 spawn 过子任务，也能读到子任务结果。

  ---
  改动范围总结

  ┌───────────────────────────────┬────────────────────────────────────────────────────────────┐
  │             文件              │                            改动                            │
  ├───────────────────────────────┼────────────────────────────────────────────────────────────┤
  │ app/runtime/agent_loop.py     │ SUSPENDED 提前 return 前写入 user + assistant 两条 memory  │
  ├───────────────────────────────┼────────────────────────────────────────────────────────────┤
  │ app/runtime/prompt_builder.py │ build_messages 检测 resume 场景，替换最后一条 user message │
  └───────────────────────────────┴────────────────────────────────────────────────────────────┘

  # Update task meta data and session goal
  1. app/orchestrator/lifecycle_manager.py                                                                                                                                                                                                                                     
                                                                                                                                                                                                                                                                             
  spawn_daemon_task — daemon task 从固定 inherit_memory=False 改为读取 task settings：
  # 改前
  inherit_memory=False

  # 改后
  inherit_memory = task.settings.get("inherit_memory", False)

  ---
  2. app/orchestrator/session_manager.py

  _create_initial_task — meta_task 的创建逻辑重写：

  - 新增 "inherit_memory": True 到 inputs，让 daemon agent 继承 root agent 的记忆
  - 根据 session goal 是否已提炼，动态构建 description：
    - 首次（session.goal == session.user_prompt）：description 不体现 goal，要求 LLM 判断并设定
    - 续话（session.goal != session.user_prompt）：description 带上当前 goal，要求 LLM 保持稳定

  ---
  3. app/tools/control_tools.py

  update_task_metadata 工具签名 — 新增 session_goal 可选参数：
  session_goal: Annotated[str, "对整个 session 整体目的的理解（≤60字）；首次设定或确认用户方向发生根本性转变时填写，否则留空"] = ""

  _handle_update_task_metadata — 新增写入 session goal 的逻辑：
  - 提取 session_goal 参数
  - 若非空，调用 session_svc.get → 更新 session.goal → session_svc.save
  - 推送 session_goal_updated SSE 事件

  ---
  4. resources/agents/metadata_filler/SOUL.md

  提示词从"只看当前 user_prompt 生成 title/description"升级为：

  - 工作流：阅读对话历史 → 生成 title + description → 判断 session_goal → 调用工具
  - session_goal 规则：
    - 首次（description 未提 goal）：从当前指令和历史中提炼整体目的，填入 session_goal
    - 续话（description 已给出 goal）：仅在用户方向根本性转变时才填新值，否则留空（不触发更新）
  - 稳定性由 prompt 语义保证，无代码层守卫

  ---
  数据流

  用户输入
    └─ _create_initial_task
         ├─ 检查 session.goal vs session.user_prompt
         │    ├─ 首次 → description 不含 goal
         │    └─ 续话 → description 含当前 goal
         └─ spawn_daemon_task（inherit_memory=True）
              └─ _instantiate_sub_agent → 复制 root agent 记忆
                   └─ metadata_filler agent
                        └─ update_task_metadata(title, description, session_goal)
                             ├─ task.title / task.description 更新
                             └─ session.goal 更新（session_goal 非空时）