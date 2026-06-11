from app.llm.types import LLMMessage
from app.runtime.prompt_builder import PromptBuilderFactory


def _sanitize(msgs):
    return PromptBuilderFactory.for_actor().sanitize_messages(msgs)


def test_dangling_tool_call_is_stripped_keeping_text():
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="delegating",
                   tool_calls=[{"id": "tc1", "name": "submit_task", "input": {}}]),
        LLMMessage(role="user", content="continue"),
    ]
    out = _sanitize(msgs)
    # the assistant message survives as plain text, but its dangling tool_call is gone
    asst = [m for m in out if m.role == "assistant"]
    assert len(asst) == 1
    assert "delegating" in str(asst[0].content)
    assert not asst[0].tool_calls


def test_dangling_tool_call_with_no_text_is_dropped():
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="assistant", content="",
                   tool_calls=[{"id": "tc1", "name": "submit_task", "input": {}}]),
    ]
    out = _sanitize(msgs)
    assert all(m.role != "assistant" for m in out)


def test_complete_pair_is_preserved():
    msgs = [
        LLMMessage(role="assistant", content="calling",
                   tool_calls=[{"id": "tc1", "name": "read", "input": {"p": "f"}}]),
        LLMMessage(role="tool", content="body", tool_call_id="tc1"),
    ]
    out = _sanitize(msgs)
    asst = [m for m in out if m.role == "assistant"]
    tool = [m for m in out if m.role == "tool"]
    assert len(asst) == 1 and asst[0].tool_calls and asst[0].tool_calls[0]["id"] == "tc1"
    assert len(tool) == 1 and tool[0].tool_call_id == "tc1"


def test_partial_tool_calls_keeps_matched_drops_unmatched():
    msgs = [
        LLMMessage(role="assistant", content="two calls",
                   tool_calls=[{"id": "tc1", "name": "read", "input": {}},
                               {"id": "tc2", "name": "submit_task", "input": {}}]),
        LLMMessage(role="tool", content="body", tool_call_id="tc1"),
    ]
    out = _sanitize(msgs)
    asst = [m for m in out if m.role == "assistant"][0]
    ids = [tc["id"] for tc in asst.tool_calls]
    assert ids == ["tc1"]  # tc2 dropped (no result)


def test_orphan_tool_result_is_dropped():
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(role="tool", content="stray", tool_call_id="ghost"),
    ]
    out = _sanitize(msgs)
    assert all(m.role != "tool" for m in out)
