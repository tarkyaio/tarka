from __future__ import annotations

import pytest


def _analysis_json_with_target() -> dict:
    return {
        "target": {"target_type": "pod", "namespace": "ns1", "cluster": "c1", "pod": "p1"},
        "analysis": {"features": {"family": "crashloop"}, "hypotheses": []},
        "alert": {"fingerprint": "fp", "labels": {"alertname": "X"}, "annotations": {}},
    }


def test_actions_propose_tool_requires_case_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.authz.policy import ActionPolicy, ChatPolicy
    from agent.chat.tools import run_tool

    cp = ChatPolicy(enabled=True)
    ap = ActionPolicy(enabled=True)
    res = run_tool(
        policy=cp,
        action_policy=ap,
        tool="actions.propose",
        args={"action_type": "restart_pod", "title": "t"},
        analysis_json=_analysis_json_with_target(),
        case_id=None,
    )
    assert res.ok is False
    assert res.error == "case_id_required"


def test_actions_propose_tool_honors_allowlist_and_calls_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.authz.policy import ActionPolicy, ChatPolicy
    from agent.chat.tools import run_tool

    calls = {"create": 0}

    def fake_list_case_actions(*, case_id: str, limit: int = 50):  # type: ignore[no-untyped-def]
        return True, "ok", []

    def fake_create_case_action(**kwargs):  # type: ignore[no-untyped-def]
        calls["create"] += 1
        assert kwargs["case_id"] == "case-1"
        assert kwargs["action_type"] == "restart_pod"
        return True, "ok", "action-1"

    monkeypatch.setattr("agent.memory.actions.list_case_actions", fake_list_case_actions)
    monkeypatch.setattr("agent.memory.actions.create_case_action", fake_create_case_action)

    cp = ChatPolicy(enabled=True)
    ap = ActionPolicy(enabled=True, action_type_allowlist={"restart_pod"})
    res = run_tool(
        policy=cp,
        action_policy=ap,
        tool="actions.propose",
        args={"action_type": "restart_pod", "title": "Restart pod"},
        analysis_json=_analysis_json_with_target(),
        case_id="case-1",
        run_id="run-1",
    )
    assert res.ok is True
    assert (res.result or {}).get("action_id") == "action-1"
    assert calls["create"] == 1
