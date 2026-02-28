from __future__ import annotations

import pytest


def test_load_action_policy_defaults_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.authz.policy import load_action_policy

    monkeypatch.delenv("ACTIONS_ENABLED", raising=False)
    p = load_action_policy()
    assert p.enabled is False


def test_load_action_policy_parses_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.authz.policy import load_action_policy

    monkeypatch.setenv("ACTIONS_ENABLED", "1")
    monkeypatch.setenv("ACTIONS_TYPE_ALLOWLIST", "restart_pod, rollout_restart ")
    monkeypatch.setenv("ACTIONS_REQUIRE_APPROVAL", "1")
    monkeypatch.setenv("ACTIONS_ALLOW_EXECUTE", "0")
    p = load_action_policy()
    assert p.enabled is True
    assert p.require_approval is True
    assert p.allow_execute is False
    assert p.action_type_allowlist is not None
    assert "restart_pod" in p.action_type_allowlist
    assert "rollout_restart" in p.action_type_allowlist
