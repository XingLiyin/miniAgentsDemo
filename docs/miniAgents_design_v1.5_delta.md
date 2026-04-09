
# 设计方案：Observer 接管 task 完成判定

## 核心目标

| 职责 | 当前 | 变更后 |
|------|------|--------|
| task 完成信号 | LLM 调用 mark_task_complete 工具 | Observer LLM 评估 |
| task_svc.finish/fail 调用方 | Actor | AgentLoop（依据 verdict） |
| 对话记录 | 每轮结束即丢弃 | 保留为 ConversationTurn 传给 Observer |
| Observer 输入 | ActorResult.output（纯文本） | 完整对话记录 |
| 任务完成 HITL | Actor | AgentLoop |
| plan task 的 finish 调用 | Actor | AgentLoop（统一） |

---

## 1. types.py — 新增 / 修改

新增 ConversationTurn，记录每一轮 LLM 交互：

```python
@dataclass
class ConversationTurn:
    round: int
    messages_sent: list[LLMMessage]   # 发给 LLM 的上下文
    llm_text: str                      # LLM 本轮文本回复
    tool_calls: list[ToolCallRecord]   # 本轮调用的工具及结果
````

ActorResult 新增字段：

* `conversation_turns: list[ConversationTurn] = field(default_factory=list)`
* `output` 改为“最后一轮的 LLM 文本”（保留）
* `success` 改为 Actor 执行级成功（未抛异常）

ObserverVerdict 新增字段：

* `task_success: bool`         # 当前 task 是否完成（替代 mark_task_complete）
* `task_result: str`           # 写入 task.result 的内容
* `needs_user_confirm: bool = False`  # Observer 不确定时，要求 AgentLoop 触发 HITL
* `done` 保留原语义：session 级目标是否达成

---

## 2. actor.py — 简化 _act_as_executor

移除：

* `mark_task_complete` 从 tool 列表中移除
* `_finish_task / _fail_task` 调用（Actor 不再判定 task 完成）
* `_ask_user_for_task_confirmation`（移至 AgentLoop）

修改：

* 每轮 loop 开始前记录发出的 messages，结束后构造 ConversationTurn 追加到列表
* 退出条件：max_rounds 耗尽 OR LLM 本轮无 tool_calls
* 返回：

```python
ActorResult(conversation_turns=[...], output=last_text, success=True)
```

`_act_as_planner` 同样移除 `task_svc.finish()` 调用，直接返回：

```python
ActorResult(actor_mode="plan")
```

---

## 3. observer.py — 接管 task 判定

`submit_observation` 工具新增字段：

* `task_complete: bool`   # 当前 task 是否完成
* `task_result: str`      # task 的执行结论
* `done: bool`            # session 目标是否完成
* `summary: str`
* `reasoning: str`
* `needs_user_confirm: bool = False`

`observe()` 签名变化：

```python
def observe(self, session, result: ActorResult, ctx, task: Task) -> ObserverVerdict:
```

`_llm_observe` 的 prompt 变化：

* 当前：只传 `result.output` 一段文本
* 变更后：将 `result.conversation_turns` 展开为完整对话记录：

```
Round 1:
  [tool calls & results]
  LLM: <llm_text>
Round 2: ...
```

* Observer LLM 看到的是 Actor 完整执行过程，而非摘要

`_rule_observe_plan` 变化：

```python
return ObserverVerdict(
    task_success=True,
    task_result=f"Created {result.plan_task_count} tasks",
    done=result.plan_task_count == 0,
    ...
)
```

---

## 4. agent_loop.py — 接管 task 状态写入 + HITL

`run()` 新增逻辑（在 observe() 之后）：

```python
verdict = self._observer.observe(session, result, ctx, task)

# task 完成判定（由 Observer 决定，AgentLoop 执行）
if verdict.needs_user_confirm:
    confirmed, feedback = self._ask_user_for_task_confirmation(task, result.output)
    if confirmed:
        self._task_svc.finish(task.id, result=result.output)
        verdict = ObserverVerdict(task_success=True, task_result=result.output, ...)
    else:
        self._task_svc.fail(task.id, error=feedback)
        raise AppError("TASK_NOT_CONFIRMED", feedback)
elif verdict.task_success:
    self._task_svc.finish(task.id, result=verdict.task_result)
else:
    self._task_svc.fail(task.id, error=verdict.task_result)
    raise AppError("TASK_FAILED_BY_OBSERVER", verdict.task_result)

if verdict.done:
    self._session_svc.transition(session_id, "SUCCEEDED")
```

`_ask_user_for_task_confirmation` 和 `_block_for_user_input` 从 Actor 迁移至 AgentLoop（需要注入 session_svc，AgentLoop 已有）

---

## 5. 依赖变化

| 模块                 | 变化                                |
| ------------------ | --------------------------------- |
| Observer.**init**  | 无新依赖（task_svc 不注入，由 AgentLoop 调用） |
| Observer.observe   | 新增 task: Task 参数                  |
| AgentLoop.**init** | 无新依赖                              |
| AgentLoop.run      | 新增 HITL 逻辑 + task_svc.finish/fail |
| deps.py            | 无变化                               |

---

## 6. 退出条件与错误传播

| 情况                                 | Actor 行为     | Observer 行为                              | AgentLoop 行为      |
| ---------------------------------- | ------------ | ---------------------------------------- | ----------------- |
| LLM 正常完成（无 tool call）              | 返回 last_text | task_complete=True                       | task_svc.finish() |
| max_rounds 耗尽                      | 返回最后文本       | 评估 transcript，可设 needs_user_confirm=True | HITL 或 fail       |
| LLM 调用工具但未结束                       | 返回最后状态       | 评估 transcript                            | 同上                |
| spawn_agents / request_human_input | 正常处理（不变）     | —                                        | —                 |
| Actor 抛异常                          | 异常上浮         | 不调用                                      | LM 处理 FAILED      |

---

## 关键点

1. Actor 职责收窄：只负责“驱动 LLM 执行工具循环，保留记录”，不判定是否完成。
2. Observer 信息更丰富：从“任务输出的一段摘要”升级为“完整多轮对话”，判断质量提升。
3. AgentLoop 成为事务协调者：task 状态写入、HITL 触发都集中在 AgentLoop，职责明确。
4. plan task 统一：`_act_as_planner` 也不再自己调 `task_svc.finish()`，通过 Observer 规则路径完成，避免特殊处理。

