"""Unit tests for Slack Bolt app — message parsing and handler logic."""

from __future__ import annotations

import pytest


def test_strip_mention_removes_user_id() -> None:
    from agent.slack.bolt_app import _strip_mention

    assert _strip_mention("<@U1234ABCD> what happened?") == "what happened?"
    assert _strip_mention("<@U1234ABCD>  check pod") == "check pod"
    assert _strip_mention("no mention here") == "no mention here"


def test_strip_mention_handles_empty() -> None:
    from agent.slack.bolt_app import _strip_mention

    assert _strip_mention("") == ""
    assert _strip_mention("<@U1234ABCD>") == ""


def test_extract_case_id_finds_uuid() -> None:
    from agent.slack.bolt_app import _extract_case_id

    assert _extract_case_id("check case 550e8400-e29b-41d4-a716-446655440000") == "550e8400-e29b-41d4-a716-446655440000"


def test_extract_case_id_returns_none_for_no_uuid() -> None:
    from agent.slack.bolt_app import _extract_case_id

    assert _extract_case_id("what happened to payment service?") is None


def test_extract_case_id_handles_mixed_case() -> None:
    from agent.slack.bolt_app import _extract_case_id

    assert (
        _extract_case_id("case 550E8400-E29B-41D4-A716-446655440000 details") == "550E8400-E29B-41D4-A716-446655440000"
    )


def test_is_slack_configured_checks_both_tokens(monkeypatch) -> None:
    from agent.slack.bolt_app import _is_slack_configured

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    assert _is_slack_configured() is False

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    assert _is_slack_configured() is False

    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    assert _is_slack_configured() is True


def test_create_bolt_app_returns_none_without_tokens(monkeypatch) -> None:
    from agent.slack.bolt_app import create_bolt_app

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)

    app = create_bolt_app()
    assert app is None


def test_stream_to_slack_collects_tokens() -> None:
    """_stream_to_slack returns concatenated token content."""
    import asyncio

    from agent.slack.bolt_app import _stream_to_slack

    class _Ev:
        def __init__(self, t, c=""):
            self.event_type = t
            self.content = c

    async def _stream():
        for ev in [_Ev("token", "hello "), _Ev("token", "world")]:
            yield ev

    result = asyncio.run(_stream_to_slack(_stream()))
    assert result == "hello world"


def test_stream_to_slack_error_event() -> None:
    """_stream_to_slack surfaces error content when no tokens arrived."""
    import asyncio

    from agent.slack.bolt_app import _stream_to_slack

    class _Ev:
        def __init__(self, t, c=""):
            self.event_type = t
            self.content = c

    async def _stream():
        yield _Ev("error", "timeout")

    result = asyncio.run(_stream_to_slack(_stream()))
    assert "timeout" in result


def test_stream_to_slack_truncates_long_reply() -> None:
    """_stream_to_slack truncates replies exceeding 3000 chars."""
    import asyncio

    from agent.slack.bolt_app import _stream_to_slack

    class _Ev:
        def __init__(self, t, c=""):
            self.event_type = t
            self.content = c

    async def _stream():
        yield _Ev("token", "x" * 4000)

    result = asyncio.run(_stream_to_slack(_stream()))
    assert len(result) < 4000  # shorter than input
    assert "truncated" in result


@pytest.mark.asyncio
async def test_handle_message_sets_and_clears_status() -> None:
    """set_status fires immediately on invocation; clear_status always runs in finally."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.slack.bolt_app import _handle_message

    mock_provider = MagicMock()
    mock_say = AsyncMock()
    event = {"channel": "C123", "ts": "1234.5678", "text": "hello"}

    async def _fake_stream():
        yield MagicMock(event_type="token", content="hi", tool=None, metadata=None)

    with (
        patch("agent.slack.provider.get_slack_provider", return_value=mock_provider),
        patch("agent.slack.bolt_app._load_analysis_json", return_value=None),
        patch("agent.authz.policy.load_chat_policy", return_value=MagicMock(enabled=True)),
        patch("agent.authz.policy.load_action_policy", return_value=MagicMock()),
        patch(
            "agent.chat.global_runtime_streaming.run_global_chat_stream",
            return_value=_fake_stream(),
        ),
    ):
        await _handle_message(event, mock_say, MagicMock())

    mock_provider.set_status.assert_called_once_with(
        channel="C123", thread_ts="1234.5678", status="Gathering information..."
    )
    mock_provider.clear_status.assert_called_once_with(channel="C123", thread_ts="1234.5678")
    mock_say.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_clear_status_on_stream_error() -> None:
    """clear_status runs in finally even when the stream raises."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.slack.bolt_app import _handle_message

    mock_provider = MagicMock()
    mock_say = AsyncMock()
    event = {"channel": "C123", "ts": "1234.5678", "text": "hello"}

    async def _erroring_stream():
        raise RuntimeError("stream failed")
        yield  # make it an async generator

    with (
        patch("agent.slack.provider.get_slack_provider", return_value=mock_provider),
        patch("agent.slack.bolt_app._load_analysis_json", return_value=None),
        patch("agent.authz.policy.load_chat_policy", return_value=MagicMock(enabled=True)),
        patch("agent.authz.policy.load_action_policy", return_value=MagicMock()),
        patch(
            "agent.chat.global_runtime_streaming.run_global_chat_stream",
            return_value=_erroring_stream(),
        ),
    ):
        await _handle_message(event, mock_say, MagicMock())

    # Status must be cleared even on error
    mock_provider.clear_status.assert_called_once_with(channel="C123", thread_ts="1234.5678")


@pytest.mark.asyncio
async def test_handle_message_uses_thread_ts_when_in_thread() -> None:
    """When replying inside an existing thread, status anchors to thread root ts."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.slack.bolt_app import _handle_message

    mock_provider = MagicMock()
    mock_say = AsyncMock()
    # Event has both ts (message) and thread_ts (thread root)
    event = {"channel": "C123", "ts": "9999.0001", "thread_ts": "1234.5678", "text": "follow up"}

    async def _fake_stream():
        yield MagicMock(event_type="token", content="reply", tool=None, metadata=None)

    with (
        patch("agent.slack.provider.get_slack_provider", return_value=mock_provider),
        patch("agent.slack.bolt_app._load_analysis_json", return_value=None),
        patch("agent.authz.policy.load_chat_policy", return_value=MagicMock(enabled=True)),
        patch("agent.authz.policy.load_action_policy", return_value=MagicMock()),
        patch(
            "agent.chat.global_runtime_streaming.run_global_chat_stream",
            return_value=_fake_stream(),
        ),
    ):
        await _handle_message(event, mock_say, MagicMock())

    # Status and say must use thread_ts (not message ts)
    mock_provider.set_status.assert_called_once_with(
        channel="C123", thread_ts="1234.5678", status="Gathering information..."
    )
    mock_say.assert_called_once_with(text="reply", thread_ts="1234.5678")


@pytest.mark.asyncio
async def test_handle_message_policy_disabled() -> None:
    """When chat policy is disabled, status is still cleared."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.slack.bolt_app import _handle_message

    mock_provider = MagicMock()
    mock_say = AsyncMock()
    event = {"channel": "C123", "ts": "1234.5678", "text": "hello"}

    with (
        patch("agent.slack.provider.get_slack_provider", return_value=mock_provider),
        patch("agent.authz.policy.load_chat_policy", return_value=MagicMock(enabled=False)),
    ):
        await _handle_message(event, mock_say, MagicMock())

    mock_provider.set_status.assert_called_once()
    mock_provider.clear_status.assert_called_once_with(channel="C123", thread_ts="1234.5678")
    mock_say.assert_called_once()  # "Chat is currently disabled."


def test_stream_to_slack_empty_stream_returns_fallback() -> None:
    """Empty stream returns the fallback message."""
    import asyncio

    from agent.slack.bolt_app import _stream_to_slack

    async def _empty():
        return
        yield  # noqa: unreachable

    result = asyncio.run(_stream_to_slack(_empty()))
    assert "didn't generate a response" in result
