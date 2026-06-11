from app.domain.models.task import Task
from app.runtime.prompt_builder import PromptBuilderFactory


def _task():
    return Task(id="t1", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="PENDING", user_prompt="do X", title="Do X", description="d",
                created_at="", updated_at="")


def test_tracking_updates_section_rendered():
    builder = PromptBuilderFactory.for_actor()
    updates = ["Tracked task「Sib」completed.\n# Output\n\nsib out"]
    content = builder.build_initial_user_content(_task(), tracking_updates=updates)
    text = content if isinstance(content, str) else str(content)
    assert "## Tracking task updates" in text
    assert "sib out" in text


def test_no_tracking_updates_omits_section():
    builder = PromptBuilderFactory.for_actor()
    content = builder.build_initial_user_content(_task())
    assert "## Tracking task updates" not in str(content)
