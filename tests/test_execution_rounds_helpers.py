from app.runtime.execution_rounds import (
    DELEGATION_TOOLS, make_round_record, round_to_actor_messages,
)
from app.runtime.types import ConversationTurn, ToolCallRecord


def test_make_round_record_serializes_turns_and_skips_delegation():
    turns = [ConversationTurn(
        round=0, messages_sent=[], llm_text="did stuff",
        tool_calls=[
            ToolCallRecord(tool_name="read", arguments={"p": "f"}, result="body", tool_call_id="tc1"),
            ToolCallRecord(tool_name="submit_task", arguments={"title": "Z"}, result="created", tool_call_id="tc2"),
        ],
    )]
    rec = make_round_record(turns, process_report="rep", output="out", ts="2026-06-10T00:00:00Z")
    assert rec["process_report"] == "rep"
    assert rec["output"] == "out"
    assert rec["ts"] == "2026-06-10T00:00:00Z"
    names = [tc["tool_name"] for t in rec["turns"] for tc in t["tool_calls"]]
    assert "read" in names
    assert "submit_task" not in names  # delegation excluded
    # the delegating turn's text is blanked (it is carried by the submit_task tool_call memory)
    assert rec["turns"][0]["llm_text"] == ""


def test_make_round_record_keeps_text_when_no_delegation():
    turns = [ConversationTurn(
        round=0, messages_sent=[], llm_text="just thinking",
        tool_calls=[ToolCallRecord(tool_name="read", arguments={}, result="b", tool_call_id="tc1")],
    )]
    rec = make_round_record(turns, process_report="", output="", ts="", mem_index=2)
    assert rec["turns"][0]["llm_text"] == "just thinking"
    assert rec["mem_index"] == 2


def test_round_to_actor_messages_shape():
    rec = {
        "turns": [{
            "llm_text": "did stuff",
            "tool_calls": [{"tool_name": "read", "arguments": {"p": "f"}, "result": "body",
                            "is_error": False, "tool_call_id": "tc1"}],
        }],
        "process_report": "next: do Y",
        "output": "out",
        "ts": "2026-06-10T00:00:00Z",
    }
    msgs = round_to_actor_messages(rec)
    roles = [m.role for m in msgs]
    assert roles == ["assistant", "tool", "user"]
    assert msgs[0].tool_calls[0]["name"] == "read"
    assert msgs[0].tool_calls[0]["id"] == "tc1"
    assert msgs[1].tool_call_id == "tc1"
    assert msgs[1].content == "body"
    assert "## Last round review" in msgs[2].content
    assert "next: do Y" in msgs[2].content


def test_round_to_actor_messages_no_report_omits_review_note():
    rec = {"turns": [{"llm_text": "hi", "tool_calls": []}], "process_report": "", "output": "", "ts": ""}
    msgs = round_to_actor_messages(rec)
    assert [m.role for m in msgs] == ["assistant"]


def test_delegation_tools_constant():
    assert DELEGATION_TOOLS == {"submit_task", "submit_plan"}
