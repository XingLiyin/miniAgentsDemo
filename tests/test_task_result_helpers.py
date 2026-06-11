"""Delivery rendering helpers: full per-round process report + id-bearing label."""
from __future__ import annotations

from app.domain.models.task import Task
from app.runtime.task_result import full_process_report, task_label


def _task(**kw):
    base = dict(id="c9", session_id="s1", creator_agent_id="a1", assigned_agent_id="a1",
                status="FINISHED", user_prompt="", title="My Task", created_at="", updated_at="")
    base.update(kw)
    return Task(**base)


def test_task_label_has_title_and_id():
    assert task_label(_task(), "completed") == "Task「My Task」(id=c9) completed."


def test_full_process_report_joins_all_rounds():
    t = _task()
    t.execution_rounds = [
        {"process_report": "did A", "turns": [], "output": "", "ts": "", "mem_index": 0},
        {"process_report": "", "turns": [], "output": "", "ts": "", "mem_index": 1},      # empty skipped
        {"process_report": "did B", "turns": [], "output": "", "ts": "", "mem_index": 2},
    ]
    out = full_process_report(t)
    assert "### Round 1\ndid A" in out
    assert "### Round 3\ndid B" in out
    assert "Round 2" not in out  # empty round omitted


def test_full_process_report_falls_back_to_snapshot():
    t = _task()
    t.execution_rounds = []
    t.process_report = "snapshot only"
    assert full_process_report(t) == "snapshot only"
