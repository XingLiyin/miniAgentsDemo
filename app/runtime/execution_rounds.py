"""逐轮执行记录（Task.execution_rounds）的序列化与还原纯函数。

- make_round_record: 把一次 act() 调用的 ConversationTurn 列表 + process_report + output
  序列化成可持久化的 round dict。委派工具（submit_task/submit_plan）的调用不写入——它们
  提升到 agent memory 作为 tool_call/tool_result pair。
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
) -> dict[str, Any]:
    """序列化一次 act() 调用为 round 记录；委派工具调用被剔除。"""
    serialized_turns: list[dict] = []
    for t in turns:
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
        serialized_turns.append({"llm_text": t.llm_text, "tool_calls": tcs})
    return {
        "turns": serialized_turns,
        "process_report": process_report or "",
        "output": output if output is not None else "",
        "ts": ts or "",
    }


def round_to_actor_messages(record: dict[str, Any]) -> list[LLMMessage]:
    """把 round 记录还原成 actor 视图的消息序列。"""
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
    report = (record.get("process_report") or "").strip()
    if report:
        messages.append(LLMMessage(role="user", content=f"## Last round review\n{report}"))
    return messages
