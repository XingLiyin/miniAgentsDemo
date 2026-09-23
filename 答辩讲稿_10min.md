# 代码英雄答辩讲稿（10 分钟 · 对着代码讲）

**准备**：IDE 里预先打开这 8 个文件，按顺序排好标签页；字号调到 16pt 以上。

```
app/runtime/agent_loop.py
app/runtime/types.py
app/tools/control_tools.py
app/domain/state_machine.py
app/orchestrator/task_queue.py
app/runtime/tool_gateway.py
app/runtime/policy_rule.py
app/llm/base.py
```

全程节奏：**每段先说这段解决什么，再翻代码，最后一句收**。不要逐行念代码。

---

## 0. 开场（0:00 – 0:40）

> 我讲的是 IPMaster-Cowork 的后端，`app/` 目录，150 个模块、约 1.7 万行 Python。
>
> 这套服务要做的事是：一个请求进来，它自己拆任务、自己派 agent、调工具、必要时停下来问人，最后把结果收回来。
>
> 这类系统现在主流是两种写法。一种是 workflow，流程图在开发期画死，新增一条分支就得改图改代码重新发布。另一种是 ReAct，一个循环从头跑到尾，灵活，但任务结构只存在于消息链里——没有 task 对象，谈不上依赖、进度，失败了只能整段重来，而且"这个任务做完没有"是模型自己在正文里说的。
>
> 我们的取法是：**执行沿用 ReAct 的循环，保留运行期决定下一步的灵活性；但每一步都落成 task 对象，由 Observer 逐轮复核，把 workflow 那种过程约束拿回来。**
>
> 下面我直接翻代码，讲六处。

---

## 1. AgentLoop.run()：执行骨架（0:40 – 3:00）

**打开** `app/runtime/agent_loop.py`，**跳到第 59 行**。

> 这是整个系统最核心的一个方法，一次调用执行一个 task。我先说它的边界：这个方法里没有业务逻辑。

**滚到 107–119 行**。

> 主干就这三步。107 行 `reason` 装配上下文，109 行 `_run_actor` 调模型和执行工具，119 行 `_run_observer` 判定任务结果。
>
> 注意中间 112 行这个分支——`task.status == "SUSPENDED"`，就 return 了。这是等人回答的出口。**人工介入在这里不是一条特殊逻辑，是这条链路上的一个正常返回点**：写完这一轮的执行记录就退出，用户回答之后 task 重新入队，下一轮从头再跑一遍 run()。

**往上滚到 75 行附近**（token_budget 校验）**，再到 93 行附近**（user_prompt 落 memory）。

> 前面这几步是准备：75 行校验 session 的 token 预算，超了直接抛 `TOKEN_BUDGET_EXCEEDED`；93 行把 user_prompt 按包装格式写进 memory——**这一步放在最前面是故意的**，保证这个任务不管以什么方式结束，提问本身都在记录里。

**滚到 144 行 `finally`**。

> 收尾在 finally 段：重新读一次 task，如果是 SUSPENDED，agent 标成 WAITING，否则 FINISHED。不管中间走哪条路径，agent 状态都不会留在 RUNNING。
>
> 整个 run()，连异常处理不到 100 行。**它只决定这几步的先后和三种退出方式：正常结束、挂起等回答、失败上抛给调度层。**prompt 怎么拼、工具怎么调、任务算不算完成，都不在这个文件里。

⏱ *这段讲完约 2 分 20 秒，是整场的重点，别赶。*

---

## 2. types.py：阶段之间的契约（3:00 – 4:00）

**打开** `app/runtime/types.py`。

> 刚才那三步之间传的是什么？就是这个文件里的三个 dataclass。
>
> `ReasoningContext` 是 Reasoner 的输出：goal、recent_messages、agent 身份、检索到的工具和技能。`ActorResult` 是 Actor 的输出：output、tool_calls_made、conversation_turns、还有 `exit_reason` —— 正常结束、上下文超限、还是轮次用尽。`ObserverVerdict` 最简单，只有 summary 和 token 统计。

> 三个阶段不共享可变状态，只通过这三个结构传递。**带来的直接好处是：我可以手工构造一个 ActorResult，单独测 Observer，不需要起模型、不需要跑前面两步。**仓库里 27 组测试能脱网跑，一半原因在这儿。

> 这里还有个细节：`TaskOutcome` 用的是 `Literal["success", "failed", "ask_human"]`，不是 str。状态值写错在类型检查阶段就能发现，不用等运行时。

---

## 3. 完成判定：submit_task_assessment（4:00 – 6:00）

> 这是我认为最值得讲的一处，回到开头说的"严谨性"。

**打开** `app/tools/control_tools.py`，**跳到 325 行**。

> ReAct 型 agent 判断任务完成，通常是模型在回复里说一句"已完成"。我们不这么做——**任务状态只能通过调用这个控制工具来改**，它是注册在 Observer 阶段的工具，模型必须显式调用，参数里要带 `task_status` 和 `task_process_report`。

**滚到 361 行**。

> 361 行先做取值校验，不在四个合法值里的一律按 failed 处理。

**滚到 366–374 行**（护栏）。

> 这段是踩过坑加的：模型判 success、但 `task.outputs` 是空的，我们不认，强制把状态改回 active，并且把提示写进 process_report，让它再跑一轮补一份纯文本终稿。**自称做完但没有产出的，不算做完。**

**滚到 377–400 行**。

> 下面四个分支就是四种结果。success 走 `task_svc.finish`；failed 走 `fail`；active 回到 PENDING 重新排队；`ask_human` 这条——先向用户提问，然后同样把 task 放回 PENDING，把用户的回答暂存到 `pending_user_answer`，下一轮 actor 以 user 消息的形式接着往下走。

**切到** `app/domain/state_machine.py`，**18 行**。

> 所有这些状态变更都要过这张表。这里是 task 的合法转换，Session 和 Agent 各有一张。
>
> 特别看 23 行：`"FINISHED": {"PENDING"}`，注释写着"Observer 复核不通过时 reopen"。**一个已经标记完成的任务，是可以被打回重做的**——Observer 每轮不只看当前任务，还会复核任务列表里已完成的条目。这就是 workflow 那种过程严谨性，但它是在运行期动态发生的，不需要预先画图。
>
> 转换非法的话，48 行这个 `validate_task` 直接抛 `INVALID_STATE_TRANSITION`，不会让系统悄悄跑到一个没人预期的状态。

---

## 4. TaskQueue：任务结构是对象（6:00 – 7:00）

**打开** `app/orchestrator/task_queue.py`，**从文件头的注释开始**。

> 每个 session 一个队列，两个容器：`_ready` 是 LIFO 栈，依赖已满足、可以立刻执行的；`_blocked` 是集合，`dag_deps` 没满足的。

**45 行 push / 57 行 pop / 73 行 notify_completed**。

> push 的时候检查依赖，满足进栈，不满足进 blocked，而且是幂等的。pop 是弹栈顶——LIFO，所以是深度优先：刚拆出来的子任务会先被执行完，而不是横着铺开。
>
> 73 行 `notify_completed`：某个任务终结之后，把 blocked 里依赖已经满足的挑出来放进 ready。

**滚到 114 行 `to_dict` / `from_dict`**。

> 队列可以序列化，随 session 持久化。进程重启之后调度状态还在。
>
> 这一页对应的是开头说的第二点：**任务不是上下文里的一段文字，是带状态、带依赖、能被持久化和重试的对象。**

---

## 5. ToolGateway：工具调用的唯一入口（7:00 – 8:30）

**打开** `app/runtime/tool_gateway.py`，**文件头注释 + 43 行 call()**。

> 全仓库所有工具调用都走这一个方法，没有第二条路。六步写在注释里，代码里也用同样的序号标着。

**依次点 58 / 62 / 76 / 101 / 117 / 142 行**。

> 58 行授权；62 行先写一条 RUNNING 审计；76 行才真正执行 handler；101 行写完成审计，成功失败都写；117 行推 SSE 事件给前端；142 行按配置截断超长输出，防止一个工具返回几兆文本把上下文撑爆。
>
> 异常在这里是收敛的：AppError 和未预期异常都被转成 `ToolResult(is_error=True)` 返回给模型，让它看到错误继续决策，而不是把整个循环炸掉。

**滚到 158 行 `_redact`**。

> 审计之前过一遍脱敏，`authorization`、`x-api-key`、`cookie` 这些头替换成星号——留痕不能把密钥一起留下来。

**切到** `app/runtime/policy_rule.py`，**42 行 WhitelistRule**。

> 授权规则是一组可插拔的对象。白名单规则做两件事：工具是否注册、这个 agent 有没有权限调。**权限是按 agent 的 capability 走的，而且 Actor 阶段和 Observer 阶段各有一套工具清单**——Observer 只拿得到提交评估用的控制工具，碰不到 bash。
>
> 62 行还有一条 `BashExecGuardRule`，针对特定工具的规则。

**切到** `app/runtime/policy_engine.py`，**16–28 行**。

> 这里有个小设计：规则在构造 PolicyEngine 的时候就按 global / tool 分好类建了索引，运行期只查这个工具相关的规则，不会每次调用都遍历全部规则。

---

## 6. LLM 适配层：变化点（8:30 – 9:30）

**打开** `app/llm/base.py`，**17 行**。

> 传输层用的是 `Protocol` 加 `runtime_checkable`，不是抽象基类。**任何有 post 方法、签名对得上的对象都能当 transport 用，不需要继承我的类**——测试里塞一个假的 transport 进去就行。

**滚到 43 行 BaseAdapter，再到 56 行 stream()**。

> 适配器是 ABC，`complete` 和 `parse_response` 必须实现。但 56 行的 `stream` 给了默认实现：调一次 complete 把结果包成一个块吐出来。**子类想做真正的 token 级流式就覆写，不想做也能用**，接入成本低一档。

**切到** `app/llm/provider_registry.py`，**21 / 34 行**。

> 注册只有两个方法，openai 风格和 anthropic 风格各一个。**加一家供应商 = 写一个 adapter 文件 + 注册一行**，runtime 目录一行都不用改——runtime 下没有任何一个 provider 的 import。

**切到** `app/llm/mock_adapter.py`。

> 还有一个 MockAdapter，跟真实 adapter 同层同接口。27 组测试就是靠它跑的，不连网络。

---

## 7. 收尾（9:30 – 10:00）

> 回到开头那句：执行用 ReAct 的循环，保留灵活性；约束靠 task 对象和 Observer 复核，拿回严谨性。
>
> 代码上对应的就是刚才这六处：run() 只编排顺序，三个 dataclass 做阶段契约，状态只能由控制工具改并且过状态机，队列带依赖且能持久化，工具调用收口到一个网关，模型和工具都从注册表接入。
>
> 这套拆法不限于 Agent。换成异步任务系统就是 Job / Worker / Scheduler，换成工作流引擎就是 Workflow / Node / Executor，位置是一样的。讲完了。

---

## 附：可能被问到的问题

**Q：Observer 也是一次模型调用，它自己判错了怎么办？**
`observer.py` 第 70–80 行有降级路径：agent 没配 role_md、或者 LLM 调用抛异常，直接走 `_rule_observe` 规则判定。另外 spawn_depth=0 且正常退出的情况会跳过观察，省一次调用。

**Q：任务失败会无限重试吗？**
不会。`task_manager.py` 295 行判 `retry_count < max_task_retries`（默认 3）；另外 session 级还有 `failure_counter`，342 行累加，到 `failure_threshold` 就暂停整个 session。双层刹车。

**Q：子 agent 会无限往下派生吗？**
`MAX_SPAWN_DEPTH` 默认 1，`MAX_CONCURRENT_AGENTS` 默认 5，都在 `config/settings.py` 里，LifecycleManager 创建前检查。

**Q：上下文会不会撑爆？**
`runtime/memory_compaction.py` 做滚动摘要压缩，Reasoner 每轮估算 token，超阈值先压缩再装配；Actor 的 `exit_reason` 里单独有 `context_limit` 这个值，能区分是正常结束还是被上下文顶掉的。

**Q：为什么用文件存储不用数据库？**
Phase 1 先跑通链路，存储层是按仓储接口写的（`storage/file/` 和 `storage/db/` 并列），换 PostgreSQL 只替换实现，调用方不动。

**Q：这些设计有没有测试覆盖？**
`tests/` 下 27 个文件，队列批量入队、生命周期、HITL 跳过、级联失败关闭工具结果、流式工具调用重组这些边界路径都有单独用例。
