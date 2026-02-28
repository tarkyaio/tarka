from __future__ import annotations


def test_chat_policy_defaults_disabled(monkeypatch) -> None:
    from agent.authz.policy import load_chat_policy

    monkeypatch.delenv("CHAT_ENABLED", raising=False)
    p = load_chat_policy()
    assert p.enabled is False


def test_chat_policy_allows_enabling_and_caps(monkeypatch) -> None:
    from agent.authz.policy import load_chat_policy

    monkeypatch.setenv("CHAT_ENABLED", "1")
    monkeypatch.setenv("CHAT_MAX_TOOL_CALLS", "9999")
    monkeypatch.setenv("CHAT_MAX_LOG_LINES", "5")  # should clamp to minimum
    monkeypatch.setenv("CHAT_MAX_TIME_WINDOW_SECONDS", "1")  # should clamp to minimum
    p = load_chat_policy()
    assert p.enabled is True
    assert p.max_tool_calls <= 20
    assert p.max_log_lines >= 20
    assert p.max_time_window_seconds >= 300
