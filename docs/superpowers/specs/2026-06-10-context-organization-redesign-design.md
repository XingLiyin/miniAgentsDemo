# 上下文组织机制重构 — 设计文档

日期：2026-06-10
状态：已与用户对齐，待实现

## 背景与动机

当前上下文组织把 observer 的 process report 和 actor 的输出混在同一条 assistant memory 里
（`agent_loop._write_execution_memory` 写入 `"{output}\n\n# Process Report\n\n{summary}"`），
导致：

- actor 下一轮把 observer 的 process report 当成自己的"回复"读到，污染对话流。
- 子任务结果以合成的 `assistant` 消息（`task_manager._task_result_content`，内嵌
  `# Output` / `# Process Report`）回传父级，而不是自然的工具调用结果。
- observer 复核前序轮时要靠 `# Process Report` 字符串标记切分 memory（`_split_agent_summary`），脆弱。

目标：把 agent memory 收敛成一条**干净的工具使用对话流**，process report 成为
**observer-only / 交付用**的元数据，子任务结果以 `submit_task` 的 **tool_result** 形式回传。

## 核心模型：两层 memory

| 层 | 持有什么 | 读取方 |
|---|---|---|
| **Task**（任务内细粒度工作记忆） | 各 act() 轮的 conversation turns（含 bash/read 等具体 tool 调用与结果）、该轮 process_report、该轮 output | actor 续跑当前 task；observer 复核 |
| **Agent memory**（跨任务干净对话流） | 每个 task 的结果（**只 output**，assistant 角色）；自己提交的子任务的 `submit_task` tool_call + 回填的 tool_result；tracker 更新（注入起始 user message） | actor 取前序任务上下文；memory compaction |

**核心不变量**：process report **绝不进入 assistant 角色的消息**。它只存在于三处：
1. Task 上（`execution_rounds[].process_report` 与 `process_report` 快照）；
2. observer 渲染的 "Prior progress"；
3. 子任务被提交时，回填给父级的 `tool_result` 里。

子任务自身的顶层结果（option a）：agent memory 只留 **output**，process report 留在 task 上。

## 数据结构改动

### `app/domain/models/task.py`

新增两个字段（含 `to_dict` / `from_dict` 序列化，默认值保证向后兼容）：

```python
execution_rounds: list[dict] = field(default_factory=list)
#   每项: {"turns": [...], "process_report": str, "output": str | list, "ts": str}
#   turns 为该 act() 调用的 conversation_turns 序列化结果（含 tool_calls + results + reply）
parent_tool_call_id: str | None = None
#   子任务专用：父 agent 中 submit_task/submit_plan 调用的 tool_call id。
#   submit_plan 的 N 个子任务共享同一个 id（聚合为一条 tool_result）。
```

保留现有 `process_report` / `outputs`：作为"最新一轮快照 + 终态交付"用途。
现有 `conversation_turns` 字段保持不动（不复用，避免语义混淆）。

## 写入路径

### A. `agent_loop` — 观察后追加逐轮记录（新增）

`run()` 中 observer 返回后、`_write_execution_memory` 前后，新增一步：把
`result.conversation_turns + verdict.process_report + task.outputs` 序列化追加进
`task.execution_rounds`，并 `task_svc.save(task)`。

### B. `agent_loop._write_execution_memory` — 改写

- 对 agent 自己执行的 task：**只写 `output` 作为 assistant 消息**，不再拼接 `# Process Report`。
  `output` 为空则不写 assistant 消息。
- HITL：`pending_user_answer` 仍作为 user 消息追加（顺序逻辑不变）。
- 多模态 output（图片）路径保留，只是去掉 process report 文本段。

### C. `control_tools.submit_task` / `submit_plan` — 挂起时改写

挂起父任务时不再依赖 `agent_loop._summarize_spawn` 的 `"Delegated…"` 文本。职责拆分：

- **control_tools**（submit_task / submit_plan）只负责创建子任务，并在子任务上写
  `parent_tool_call_id`（值取自 `ctx` 中本次工具调用的 id）。submit_plan 的多个子任务共用同一 id。
  control_tools **不**直接写 memory。
- **agent_loop**（见 D）在挂起后，把本次 `submit_task`/`submit_plan` 的
  **assistant tool_call 条目写入父 agent memory**
  （`memory_svc.append_message(role="assistant", tool_calls=[...], content=<llm 文本>)`），
  tool_call id 取自 `ActorResult.tool_calls_made[*].tool_call_id`。

> 这样 control_tools 不反向依赖 memory；写 memory 的唯一入口仍是 agent_loop。
> control_tools 拿当前工具调用 id 的方式（经 `CallContext` 透传 or 由 agent_loop 回填到子任务）
> 在实现计划中细化。

### D. `agent_loop._write_suspension_memory` — 删除/改写

不再写 `_summarize_spawn` 摘要。挂起时改为写 C 中描述的 submit_* tool_call 条目。

### E. `task_manager` — 子任务终结、父恢复时改写

`_try_resume_parent` → `_flush_tracking_tasks_to_memory`（自己提交的子任务）：

- 往父 agent memory 写一条 **`role="tool"` 消息**，`tool_call_id` = 子任务的
  `parent_tool_call_id`，内容 = 子任务 `output + process report`（允许 process report，
  因为这是 tool_result，不是 assistant 回复）。
- 同一 `parent_tool_call_id` 下的多个子任务（submit_plan）聚合为**一条** tool_result。
- 替换掉现有 `_task_result_content` 的 assistant 写法（仅对自己提交的路径）。

`_notify_trackers` / `_sync_sibling_tracking` / `_write_sib_result_to_memory`（tracker/sibling，
**非**自己提交）：见"交付路径"——改为 user message 注入，不写 tool_result。

## 读取 / 渲染路径

### F. `ActorPromptBuilder.build_messages` — 重建当前 task 视图

- 当前 task 的前序轮从 `task.execution_rounds` 重建：每轮还原
  `assistant(reply + tool_calls)` + `tool(results)`。
- agent memory 提供 task 之前的跨任务上下文，以及该 task 的 `submit_task` tool_call/tool_result pair。
- 两个来源按 `ts` / `created_at` **时间序归并**成最终 message 列表。
- 每个前序轮的 `process_report` 作为 **user 角色的简短 review note**
  （`## Last round review\n{process_report}`）注入，把"下一步提示"喂给 actor，
  但不计为 assistant 回复（满足核心不变量）。

### G. `ObserverPromptBuilder._build_prior_progress` — 改数据源

- 改为从 `task.execution_rounds` 读前序轮，直接取每轮 `process_report` 字段。
- 删除 `_split_agent_summary` 的 `# Process Report` 字符串切分逻辑。
- "Current turns" 仍来自本轮 `ActorResult.conversation_turns`，不变。

## 交付路径（汇总）

| 场景 | 交付方式 |
|---|---|
| 自己 `submit_task`/`submit_plan` 的子任务 | 回填 `submit_task` 的 **tool_result**（output + process report），按 `parent_tool_call_id` 聚合 |
| tracker/sibling（**非**自己提交，但在 tracking 列表） | 注入到它**开始自己 task 的 user message** 的 `## Tracking task updates` 段 |
| agent 自己的顶层 task 完成 | agent memory 留 **output（assistant）**；process report 只在 task 上 |

**去重规则**：被跟踪 task 的 `assigned_agent_id == self`（自己执行/提交）→ 走 tool_result；
否则（非自己提交）→ 走 user message 注入。

**tracker 注入时机**：被跟踪 task 在 tracker 正在执行中才完成时，**不打断**，等 tracker
下一轮起始 user message 再注入（已终结的从 tracking 列表里读，渲染到 `## Tracking task updates`）。

## 适用范围与边界

- **inline（use_subagent=False）与 sub-agent（=True）统一**走 tool_result。inline 父子同 agent，
  子任务的 `execution_rounds` 挂在子 task 上，细粒度不铺进父的线性流。
- **submit_task 与 submit_plan 都改**。
- sub-agent 仍独立有意义：它们能见到的 task 集合不同（memory 继承是拷贝）。
- 失败子任务：tool_result / tracking 注入需带状态 + failure reason（error），让父级知道失败原因。
- 向后兼容：旧 task 无 `execution_rounds` 时按空列表处理；observer/actor 前序轮为空即退化为
  "首轮"行为。

## 影响文件清单

- `app/domain/models/task.py` — 新增字段 + 序列化
- `app/runtime/agent_loop.py` — 追加 execution_rounds、改写 `_write_execution_memory`、改写挂起写入
- `app/runtime/prompt_builder.py` — `ActorPromptBuilder.build_messages` 重建、`ObserverPromptBuilder._build_prior_progress` 改数据源、删除 `_split_agent_summary`
- `app/runtime/observer.py` — 适配前序轮数据源（如需）
- `app/tools/control_tools.py` — submit_task/submit_plan 记录 tool_call id
- `app/orchestrator/task_manager.py` — 自交付走 tool_result、tracker/sibling 走 user message 注入

## 测试要点

- agent memory 中 assistant 消息**不含** `# Process Report`（核心不变量回归）。
- observer 的 "Prior progress" 在多轮（PENDING 重跑）任务里正确显示各轮 process report，
  数据来自 `task.execution_rounds` 而非字符串切分。
- submit_task 子任务完成后，父 agent memory 出现成对的
  `assistant(tool_call=submit_task)` + `tool(tool_call_id=…)`，内容含子任务 output + process report。
- submit_plan 的多个子任务聚合为一条 tool_result。
- tracker（非提交者）在起始 user message 看到 `## Tracking task updates`，且**不**出现重复的
  tool_result。
- inline 与 sub-agent 两条路径行为一致。
- 失败子任务的 reason 正确透传给父级。
