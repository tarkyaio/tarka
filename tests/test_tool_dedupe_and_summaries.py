from __future__ import annotations

from datetime import datetime, timezone


def test_tool_call_key_is_stable_for_arg_order() -> None:
    from agent.chat.tool_summaries import tool_call_key

    a1 = {"namespace": "ns", "pod": "p", "limit": 50}
    a2 = {"limit": 50, "pod": "p", "namespace": "ns"}
    assert tool_call_key("logs.tail", a1) == tool_call_key("logs.tail", a2)


def test_summarize_logs_tail_empty() -> None:
    from agent.chat.tool_summaries import summarize_tool_result

    outcome, summary = summarize_tool_result(
        tool="logs.tail",
        ok=True,
        error=None,
        result={
            "entries": [],
            "status": "empty",
            "reason": "empty",
            "backend": "victorialogs",
            "query_used": 'namespace:"ns" AND pod:"p"',
        },
    )
    assert outcome == "empty"
    assert "empty" in summary
    assert "0 entries" in summary


def test_case_chat_executor_skips_duplicate_tool_call(monkeypatch) -> None:
    """
    Regression: if the model repeats the same tool call, we should skip execution and
    emit a `skipped_duplicate` tool event (instead of re-calling the tool).
    """
    import agent.chat.runtime as runtime
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import ToolResult

    tool_args = {
        "namespace": "ns",
        "pod": "p",
        "start_time": datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat(),
        "end_time": datetime(2025, 1, 1, 0, 15, 0, tzinfo=timezone.utc).isoformat(),
        "limit": 50,
    }

    # Simulate three LLM steps: call logs once, repeat it, then stop.
    llm_outputs = [
        {
            "schema_version": "tarka.tool_plan.v1",
            "reply": "checking logs",
            "tool_calls": [{"tool": "logs.tail", "args": dict(tool_args)}],
        },
        {
            "schema_version": "tarka.tool_plan.v1",
            "reply": "try again",
            "tool_calls": [{"tool": "logs.tail", "args": dict(tool_args)}],
        },
        {"schema_version": "tarka.tool_plan.v1", "reply": "done", "tool_calls": []},
    ]

    def _fake_generate_json(_prompt: str, *, schema=None):
        return (llm_outputs.pop(0), None)

    calls = {"n": 0}

    def _fake_run_tool(**_kwargs):
        calls["n"] += 1
        return ToolResult(
            ok=True,
            result={"entries": [], "status": "empty", "reason": "empty", "backend": "victorialogs", "query_used": "q"},
            error=None,
            updated_analysis=None,
        )

    monkeypatch.setattr(runtime, "generate_json", _fake_generate_json)
    monkeypatch.setattr(runtime, "run_tool", _fake_run_tool)

    policy = ChatPolicy(enabled=True, allow_logs_query=True, max_steps=4, max_tool_calls=6, redact_secrets=False)
    out = runtime._run_chat_langgraph(
        policy=policy,
        action_policy=None,
        analysis_json={"target": {"namespace": "ns", "pod": "p"}},
        user_message="Why is my pod unhealthy?",
        history=[],
        case_id="case1",
        run_id="run1",
    )

    # Only one actual execution should occur; the second identical call should be skipped.
    assert calls["n"] == 1
    assert any(getattr(ev, "outcome", None) == "skipped_duplicate" for ev in out.tool_events)

    executed = [
        ev for ev in out.tool_events if ev.tool == "logs.tail" and getattr(ev, "outcome", None) != "skipped_duplicate"
    ]
    assert len(executed) == 1
