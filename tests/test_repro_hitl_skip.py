from app.domain.models.task import Task
from app.runtime.agent_loop import AgentLoop
from app.runtime.types import ActorResult, ConversationTurn, ObserverVerdict, ReasoningContext
from app.runtime.prompt_builder import PromptBuilderFactory


class _Mem:
    def __init__(self):
        self.msgs = []
    def append_message(self, agent_id, role, content, session_id="", task_id=None,
                       tool_call_id=None, tool_calls=None):
        self.msgs.append({"role": role, "content": content, "tool_call_id": tool_call_id,
                          "tool_calls": tool_calls, "task_id": task_id})
    def get_all_messages(self, agent_id):
        return list(self.msgs)


class _TaskSvc:
    def __init__(self):
        self.saved = []
    def save(self, task):
        self.saved.append(task)


def _loop(mem, ts):
    return AgentLoop(None, ts, mem, None, None, None, None, None)


def test_hitl_answer_present_in_next_round_context():
    mem, ts = _Mem(), _TaskSvc()

    # ── Cycle N: the ask_human cycle ────────────────────────────────────────
    # Before the actor runs, run() persisted the wrapped user_prompt to memory.
    mem.append_message("a1", "user", "## Current Goal\nT\n## Current Message\nstart", task_id="t1")

    task = Task(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="PENDING", user_prompt="start", title="T", description="d",
                user_prompt_in_memory=True, created_at="", updated_at="")
    task.outputs = "Question to user?"          # actor's plain-text question
    task.pending_user_answer = "MY HUMAN ANSWER"  # stashed by submit_task_assessment ask_human

    result = ActorResult(
        task_id="t1", success=True, output="Question to user?",
        conversation_turns=[ConversationTurn(round=0, messages_sent=[],
                                             llm_text="Question to user?", tool_calls=[])],
    )

    loop = _loop(mem, ts)
    # Real run() order for the ask_human/PENDING branch:
    loop._append_execution_round("a1", task, result, "asked the user", "s1", "t1")
    loop._write_execution_memory("a1", task, ObserverVerdict(summary="asked the user"), "s1", "t1")

    # ── Cycle N+1: build the actor context for the next round ───────────────
    recent = mem.get_all_messages("a1")
    ctx = ReasoningContext(goal="g", recent_messages=recent, blackboard_snippets=[],
                           current_task=task)
    msgs = PromptBuilderFactory.for_actor().build_messages(task, ctx)

    joined = "\n".join(str(m.content) for m in msgs)
    print("\n=== BUILT MESSAGES (next round) ===")
    for m in msgs:
        print(f"[{m.role}] {str(m.content)[:80]!r}")

    # The human answer must reach the next round.
    assert "MY HUMAN ANSWER" in joined, "human answer missing from next-round context!"

    # The asked question must appear exactly ONCE — not replayed from
    # execution_rounds *and* agent memory.
    assert joined.count("Question to user?") == 1, "assistant question duplicated!"

    # The observer's process report must NOT be injected into the actor view for a
    # HITL round — the human's reply is the real continuation signal, and the report
    # otherwise displaces it.
    assert "## Last round review" not in joined, "process report displaced the human reply!"

    # Clean, non-duplicated conversation: prompt → question → human answer.
    assert [(m.role, str(m.content)) for m in msgs] == [
        ("user", "## Current Goal\nT\n## Current Message\nstart"),
        ("assistant", "Question to user?"),
        ("user", "MY HUMAN ANSWER"),
    ]
