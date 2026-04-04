"""Unit tests for Slack thread bridge — thread mapping logic.

These tests mock Postgres to avoid external dependencies.

Note: register/update/lookup all use an in-memory fallback when Postgres is
not configured, so they succeed (return True / return data) even without a DB.
"""

from __future__ import annotations


def test_register_succeeds_without_postgres(monkeypatch) -> None:
    from agent.slack.thread_bridge import register_notification_thread

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    result = register_notification_thread(
        slack_channel="#test-register",
        slack_thread_ts="1111111111.000001",
        case_id="abc-123",
    )
    # In-memory fallback always returns True
    assert result is True


def test_lookup_returns_none_for_unknown_thread(monkeypatch) -> None:
    from agent.slack.thread_bridge import lookup_thread

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    # Use a key that was never registered
    result = lookup_thread(slack_channel="#test-lookup-unknown", slack_thread_ts="9999999999.999999")
    assert result is None


def test_lookup_returns_mapping_after_register(monkeypatch) -> None:
    from agent.slack.thread_bridge import lookup_thread, register_notification_thread

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    register_notification_thread(
        slack_channel="#test-lookup-known",
        slack_thread_ts="2222222222.000002",
        case_id="case-xyz",
        tarka_thread_id="thread-abc",
    )
    result = lookup_thread(slack_channel="#test-lookup-known", slack_thread_ts="2222222222.000002")
    assert result is not None
    assert result.case_id == "case-xyz"
    assert result.tarka_thread_id == "thread-abc"


def test_update_tarka_thread_id_succeeds_without_postgres(monkeypatch) -> None:
    from agent.slack.thread_bridge import register_notification_thread, update_tarka_thread_id

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    register_notification_thread(
        slack_channel="#test-update",
        slack_thread_ts="3333333333.000003",
        case_id="case-update",
    )
    result = update_tarka_thread_id(
        slack_channel="#test-update",
        slack_thread_ts="3333333333.000003",
        tarka_thread_id="new-thread-id",
    )
    # In-memory fallback always returns True
    assert result is True


def test_update_tarka_thread_id_reflects_in_lookup(monkeypatch) -> None:
    from agent.slack.thread_bridge import lookup_thread, register_notification_thread, update_tarka_thread_id

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    register_notification_thread(
        slack_channel="#test-update-reflect",
        slack_thread_ts="4444444444.000004",
        case_id="case-reflect",
    )
    update_tarka_thread_id(
        slack_channel="#test-update-reflect",
        slack_thread_ts="4444444444.000004",
        tarka_thread_id="thread-reflect-updated",
    )
    result = lookup_thread(slack_channel="#test-update-reflect", slack_thread_ts="4444444444.000004")
    assert result is not None
    assert result.tarka_thread_id == "thread-reflect-updated"
    assert result.case_id == "case-reflect"  # case_id preserved


def test_register_with_tarka_thread_id(monkeypatch) -> None:
    from agent.slack.thread_bridge import lookup_thread, register_notification_thread

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    register_notification_thread(
        slack_channel="#test-full",
        slack_thread_ts="5555555555.000005",
        case_id="case-full",
        tarka_thread_id="tarka-full",
    )
    result = lookup_thread(slack_channel="#test-full", slack_thread_ts="5555555555.000005")
    assert result is not None
    assert result.case_id == "case-full"
    assert result.tarka_thread_id == "tarka-full"


def test_lookup_unknown_key_with_known_keys_registered(monkeypatch) -> None:
    """Looking up an unknown key returns None even when other keys exist."""
    from agent.slack.thread_bridge import lookup_thread, register_notification_thread

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    register_notification_thread(
        slack_channel="#test-isolation",
        slack_thread_ts="6666666666.000006",
        case_id="case-isolation",
    )
    result = lookup_thread(slack_channel="#test-isolation", slack_thread_ts="0000000000.000000")
    assert result is None
