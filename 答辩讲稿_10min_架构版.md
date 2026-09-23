# 代码英雄答辩讲稿 · 架构版（10 分钟）

**结构**：前 3 分钟讲架构全貌，中间 5 分半只讲一件事——**一个 task 从创建到完成的完整路径**，最后 1 分钟收尾。

**准备**：IDE 左侧留着 `app/` 目录树；这几个文件预先打开排好顺序。

```
app/api/v1/deps.py
app/orchestrator/task_manager.py
app/orchestrator/task_queue.py
app/runtime/agent_loop.py
app/runtime/actor.py
app/tools/control_tools.py
app/domain/state_machine.py
```

---

## 开场（0:00 – 0:30）

> IPMaster-Cowork 的后端，`app/` 目录，150 个模块、1.7 万行 Python。
>
> 它做的事情是：一个请求进来，系统自己拆任务、自己派 agent、调工具、需要时停下来问人，最后把结果收回来。
>
> 我先用三分钟讲整体怎么分层，然后挑一条路径细讲——**一个 task 从生成到完成，代码里到底走了哪些地方**。

---

## 第一部分：架构（0:30 – 3:30）

### 1) 先看目录（0:30 – 1:30）

**指着 IDE 左边的 `app/` 目录树讲，不用开文件。**

> 目录基本就是架构。

> `api/` —— FastAPI 路由，按资源分：sessions、tasks、tools、skills、llms、memories。只做协议转换和参数校验，另外通过 SSE 把执行过程推给前端。
>
> `orchestrator/` —— 调度层，三个管理者加一个队列。**SessionManager** 管一次会话的生命周期；**TaskManager** 管任务调度，决定下一个跑哪个、要不要重试、什么时候收尾；**LifecycleManager** 管 agent 实例，维护这个 session 下的 agent 树，决定复用已有的还是新建一个。
>
> `runtime/` —— 执行层，**AgentLoop** 一次执行一个 task，内部分成 Reasoner、Actor、Observer 三段。工具调用统一走 **ToolGateway**，权限过 **PolicyEngine**。
>
> 下面三个是能力层：`llm/` 适配 OpenAI 和 Anthropic 两种风格，`tools/` 分内置、控制、MCP 三类，`skills/` 管技能来源。
>
> 最后是 `domain/` 领域模型和状态机、`storage/` 存储、`observability/` 日志与追踪。

> 代码行数上，`runtime` 最多，3087 行；然后是 `tools` 2198、`llm` 1841、`domain` 1818、`orchestrator` 1806。**重量在执行和能力接入这两块，调度层反而不厚**——因为调度只做决策，不做事。

### 2) 分层的依赖方向（1:30 – 2:30）

**打开** `app/api/v1/deps.py`，**跳到 268 行**。

> 这里能一眼看出分层关系。`get_agent_loop()` 把 AgentLoop 需要的东西全部注入进去：四个领域服务、agent 存储，加上 Reasoner、Actor、Observer 三个阶段对象。
>
> AgentLoop 自己不 new 任何东西，也不知道这些依赖是怎么构造的。整个系统的装配集中在这一个文件里，用 `@lru_cache` 保证单例——仓库里 33 处。

**往下滚到 282 行 `get_lifecycle_manager`**。

> 并发上限、spawn 深度这些都从 settings 注入。299 行这句 `lm.set_agent_loop(...)` 是回填，因为 LM 要启动 AgentLoop、AgentLoop 又要用到领域服务，用 setter 打破构造期的循环依赖。

> 三条依赖规则：**路由不碰业务逻辑；调度层不进入执行流程；runtime 里没有任何一个 provider 的 import。**换模型、加 MCP server，执行链路一行不用改。

### 3) 三层的分工（2:30 – 3:30）

> 讲完位置，说一下这三层怎么配合，然后我就按这条线细讲。
>
> 请求进来先到 SessionManager，它建会话、建 root agent、起执行线程。
>
> 任务这边全部由事件驱动：task 一旦创建，TaskService 发 `TASK_CREATED`，TaskManager 订阅到之后决定入队。一个 task 执行完，发 `TASK_EXECUTION_FINISHED`，TaskManager 再决定下一个跑谁、这个 session 是不是可以结束了。
>
> 真正干活的是 AgentLoop，一次调用执行一个 task，跑完把控制权交回给调度层。
>
> **这三层之间是通过 EventBus 和 task 状态通信的，不是互相直接调。**下面我就顺着一个 task 的路径，把这几处代码串起来。

---

## 第二部分：细讲 —— 一个 task 的一生（3:30 – 9:00）

> 假设现在模型刚刚拆出一个子任务，我们从它被创建那一刻开始看。

### ① 入队（3:30 – 4:20）

**打开** `app/orchestrator/task_manager.py`，**跳到 170 行 `on_task_created`**。

> task 创建后，领域服务发事件，走到这个订阅方法。TaskManager 在这里决定它进不进队列。

**切到** `app/orchestrator/task_queue.py`，**45 行 `push`**。

> 每个 session 一个队列，两个容器：`_ready` 是 LIFO 栈，依赖已满足、可以立刻执行；`_blocked` 是集合，`dag_deps` 还没满足的。
>
> push 的时候检查依赖，满足入栈，不满足入 blocked，而且是幂等的——重复 push 同一个 id 不会进两次。
>
> **这是 ReAct 型 agent 里没有的东西：任务在这里是一个对象，有 id、有依赖、有状态，不是消息链里的一段文字。**

### ② 出队和分派（4:20 – 5:10）

**滚到 356 行 `next_task`，再到 369 行 `_dispatch_next`**。

> 上一个 task 结束后，调度层从队列 pop 出栈顶——LIFO，所以是深度优先：刚拆出来的子任务会先被做完，而不是横着铺开一堆半成品。
>
> 381 行把 task 状态转成 ACTIVE。接着 385 行问 LifecycleManager 要一个执行者，`prepare_executor` 根据 task 上的 `use_subagent`、`subagent_template`、`inherit_memory` 这几个属性，决定复用当前 agent 还是按模板新建一个子 agent、继承哪些 memory。
>
> **注意这里的分工：TaskManager 读 task 属性做决策，LifecycleManager 负责 agent 实例怎么来。**谁决策、谁执行，分得很清楚。

### ③ 执行（5:10 – 6:20）

**打开** `app/runtime/agent_loop.py`，**跳到 107 行**。

> agent 拿到了，开始执行。主干就这三行：107 行 reason 装上下文，109 行 act 调模型和工具，119 行 observe 判定结果。
>
> 112 行这个分支要单独说：`task.status == "SUSPENDED"` 直接 return。这是等人回答的出口——**人工介入不是旁路逻辑，是这条链路上的一个正常返回点**，写完本轮执行记录就退出，用户回答之后 task 重新入队，下一轮从头再跑一遍 run()。

**切到** `app/runtime/actor.py`，**82 行**。

> Actor 内部是多轮的：`for _round in range(agent.loop_guard.actor_max_tool_rounds)`，每轮调一次模型，模型返回工具调用就执行，结果拼回消息里继续下一轮。

**滚到 168 行**。

> 退出条件在这里：没有工具调用了、或者拿到完成信号、或者 prompt token 已经到上下文上限的 80%。退出时记一个 `exit_reason` —— normal、context_limit、max_rounds。
>
> **这个字段很有用**：后面 Observer 拿到 ActorResult，能区分这一轮是正常做完的，还是被上下文顶掉的，判定逻辑不一样。

### ④ 判定（6:20 – 7:40）

**打开** `app/tools/control_tools.py`，**跳到 325 行 `submit_task_assessment`**。

> 这是我最想讲的一处。
>
> ReAct 型 agent 判断任务完成，通常是模型在回复正文里说一句"已完成"。我们不认这个——**任务状态只能通过调这个控制工具来改**，它注册在 Observer 阶段的工具清单里，模型必须显式调用，参数带 `task_status` 和 `task_process_report`。

**滚到 361 行**。

> 先做取值校验，不在四个合法值里的一律按 failed。

**滚到 366 行——重点停在这里**。

> 这段是踩过坑之后加的：模型判了 success，但 `task.outputs` 是空的，我们不认，强制把状态改回 active，并把提示写进 process_report，让它再跑一轮补一份纯文本终稿。
>
> **自称做完但拿不出产出的，不算做完。**

**滚到 377 行到 400 行**。

> 下面是四个分支。success 走 `task_svc.finish`；failed 走 `fail`；active 回 PENDING 重新排队；`ask_human` 这条——先向用户提问，同样把 task 放回 PENDING，把回答暂存进 `pending_user_answer`，下一轮 Actor 以 user 消息接着往下走。

### ⑤ 状态机把关（7:40 – 8:20）

**打开** `app/domain/state_machine.py`，**18 行**。

> 所有状态变更都要过这张表，Session、Task、Agent 各有一张。
>
> 看 23 行：`"FINISHED": {"PENDING"}`，注释写的是"Observer 复核不通过时 reopen"。**一个已经标完成的任务可以被打回重做**——Observer 每轮不只看当前任务，还会复核任务列表里的已完成条目。
>
> 这就是 workflow 那种过程约束，但它发生在运行期，不需要预先画图。
>
> 48 行 `validate_task`，非法转换直接抛 `INVALID_STATE_TRANSITION`，不会让系统悄悄跑到一个没人预期的状态上。

### ⑥ 回到队列（8:20 – 9:00）

**切回** `app/orchestrator/task_manager.py`，**182 行 `on_task_finished`**。

> 一轮结束，事件发出来，回到调度层。TaskManager 在这里做三件事：通知队列这个 task 终结了——`notify_completed` 把 blocked 里依赖已满足的挑进 ready；然后取下一个 task；如果队列空了，就判定整个 session 结束。

**顺带提 295 行**。

> 失败这条路有两层刹车：task 级看 `retry_count < max_task_retries`，默认 3 次；session 级还有 `failure_counter`，累加到阈值就暂停整个会话。

> 到这里一个 task 的循环就闭合了：**入队 → 分派 → 执行 → 判定 → 过状态机 → 回队列**。整个过程中，流程没有一处是预先定义好的，但每一步都留下了对象和状态。

---

## 收尾（9:00 – 10:00）

> 这条路径回答的就是开头那个取舍：
>
> **执行沿用 ReAct 的循环** —— 下一步做什么由模型在运行期决定，计划可以中途补拆，不需要提前把流程画完。
>
> **约束靠 task 对象和复核** —— 每一步落成 task，带状态、依赖、归属；完成与否由控制工具显式提交，还要过状态机；判错了能 reopen，失败了按次数重试。
>
> 顺带说一句代码层面的收获：这条链路上每个环节的边界都是写死的——run() 只编排顺序，阶段之间只传 `types.py` 里的三个 dataclass，工具调用只有 ToolGateway 一个入口，模型和工具都从注册表接。所以 27 组测试可以脱网跑，改一处不会牵动全局。
>
> 这套拆法也不限于 Agent，换成异步任务系统就是 Job / Worker / Scheduler，位置是一样的。讲完了。

---

## 附：追问准备

**Q：这和 LangGraph 那类图编排有什么区别？**
图编排的结构在开发期定义，运行期不能改；我们的结构是运行期生成的——Planner 产出计划变成 task 入队，执行中还能继续拆。代价是可预测性差一些，所以才要 Observer 复核和状态机来兜。

**Q：Observer 也是模型调用，它判错了怎么办？**
`observer.py` 70–80 行有降级：agent 没配 role_md、或者 LLM 调用抛异常，直接走 `_rule_observe` 规则判定。另外 spawn_depth=0 且正常退出的情况会跳过观察，省一次调用。

**Q：LIFO 为什么不是 FIFO？**
深度优先：刚拆出来的子任务立刻做完，父任务才能继续。FIFO 会让一堆父任务都停在半路等子任务，中间状态和上下文都要一直挂着。

**Q：子 agent 会无限派生吗？**
`MAX_SPAWN_DEPTH` 默认 1，`MAX_CONCURRENT_AGENTS` 默认 5，在 `config/settings.py`，LifecycleManager 创建前检查。

**Q：上下文会不会撑爆？**
`runtime/memory_compaction.py` 做滚动摘要压缩，Reasoner 每轮估算 token 超阈值先压缩；Actor 的 `exit_reason` 单独有 `context_limit` 值，能区分是做完了还是被顶掉的。

**Q：工具调用怎么保证安全？**
全部经 `runtime/tool_gateway.py` 的 `call()`，六步：授权、写 RUNNING 审计、执行、写完成审计、推 SSE、截断超长输出；审计前过 `_redact()` 把 authorization、x-api-key、cookie 脱敏。权限按 agent capability 走，Actor 和 Observer 各有一套工具清单。

**Q：为什么用文件存储不用数据库？**
Phase 1 先跑通链路。存储层按仓储接口写的，`storage/file/` 和 `storage/db/` 并列，换 PostgreSQL 只替换实现。
