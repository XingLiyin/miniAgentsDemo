"""逐轮执行记录（Task.execution_rounds）的序列化与还原纯函数。

- make_round_record: 把一次 act() 调用的 ConversationTurn 列表 + process_report + output
  序列化成可持久化的 round dict。委派工具（submit_task/submit_plan）的调用被剔除——它们
  提升到 agent memory 作为 tool_call/tool_result pair；含委派调用的那一回合的 llm_text 也被
  清空，因为该文本已由 submit_task 的 assistant tool_call（result.output）承载，避免重复。
  mem_index 锚定该 round 在「当前 task 自身 memory 消息」序列中的插入位置（见 prompt_builder）。
- round_to_actor_messages: 把一条 round dict 还原成 actor 视图的 LLMMessage 列表
  （assistant 回复+tool_calls、tool 结果、末尾 process_report 的 user review note）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.llm.types import LLMMessage

if TYPE_CHECKING:
    from app.runtime.types import ConversationTurn

DELEGATION_TOOLS = {"submit_task", "submit_plan"}


def make_round_record(
    turns: "list[ConversationTurn]",
    process_report: str,
    output: "str | list",
    ts: str,
    mem_index: int = 0,
    user_answer: str = "",
) -> dict[str, Any]:
    """序列化一次 act() 调用为 round 记录；委派工具调用被剔除，委派回合的 llm_text 被清空。

    user_answer: 本回合结束后人类（HITL）给出的回复，紧接本轮 agent 提问之后。供 observer 的
    prior progress 还原「提问 → 人类回答」的完整时序（actor 视图已从 memory 拿到该回答）。
    """
    serialized_turns: list[dict] = []
    for t in turns:
        had_delegation = any(tc.tool_name in DELEGATION_TOOLS for tc in t.tool_calls)
        tcs = [
            {
                "tool_name": tc.tool_name,
                "arguments": tc.arguments,
                "result": tc.result,
                "is_error": tc.is_error,
                "tool_call_id": tc.tool_call_id,
            }
            for tc in t.tool_calls
            if tc.tool_name not in DELEGATION_TOOLS
        ]
        # 委派回合的文本已由 submit_task 的 assistant tool_call 承载，这里清空避免重复。
        llm_text = "" if had_delegation else t.llm_text
        serialized_turns.append({"llm_text": llm_text, "tool_calls": tcs})
    return {
        "turns": serialized_turns,
        "process_report": process_report or "",
        "output": output if output is not None else "",
        "ts": ts or "",
        "mem_index": mem_index,
        "user_answer": user_answer or "",
    }


def round_to_actor_messages(record: dict[str, Any]) -> list[LLMMessage]:
    """把 round 记录还原成 actor 视图的消息序列。

    continuing round（active / ask_human）的产出由 execution_rounds 唯一承载（不再双写进
    agent memory），因此每条 round 自带一条 user 侧「续作信号」收尾，保证 prompt 不以 assistant
    结尾、也不重复回放：
      - HITL（有 user_answer）：人类回答作为 user 消息收尾；
      - active（无 user_answer，有 process_report）：process_report 作为「## Last round review」收尾。
    （记录本身保持完整，observer 的 prior_progress 仍从原始字段还原完整时序。）
    """
    messages: list[LLMMessage] = []
    for t in record.get("turns", []):
        tool_calls = t.get("tool_calls", [])
        messages.append(LLMMessage(
            role="assistant",
            content=t.get("llm_text", "") or "",
            tool_calls=[
                {"id": tc.get("tool_call_id", ""), "name": tc["tool_name"], "input": tc.get("arguments", {})}
                for tc in tool_calls
            ] or None,
        ))
        for tc in tool_calls:
            result = tc.get("result", "") or ""
            if tc.get("is_error"):
                result = f"[ERROR] {result}"
            messages.append(LLMMessage(
                role="tool", content=result, tool_call_id=tc.get("tool_call_id", ""),
            ))
    user_answer = (record.get("user_answer") or "").strip()
    report = (record.get("process_report") or "").strip()
    if user_answer:                       # HITL：人类回答即续作信号
        messages.append(LLMMessage(role="user", content=user_answer))
    elif report:                          # active：observer 的 process report 即续作信号
        messages.append(LLMMessage(role="user", content=f"## Last round review\n{report}"))
    return messages
