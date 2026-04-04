"""Unit tests for DefaultSlackProvider — Slack SDK wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_provider():
    """Create a DefaultSlackProvider by bypassing __init__ (no real slack_sdk needed)."""
    from agent.slack.provider import DefaultSlackProvider

    provider = object.__new__(DefaultSlackProvider)
    mock_client = MagicMock()
    provider._client = mock_client
    return provider, mock_client


# ---------------------------------------------------------------------------
# Initialisation / singleton (no real slack_sdk needed)
# ---------------------------------------------------------------------------


def test_get_slack_provider_returns_none_without_token(monkeypatch) -> None:
    from agent.slack.provider import get_slack_provider, set_slack_provider

    set_slack_provider(None)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert get_slack_provider() is None


def test_get_slack_provider_returns_set_provider() -> None:
    from agent.slack.provider import get_slack_provider, set_slack_provider

    mock_provider = MagicMock()
    set_slack_provider(mock_provider)
    assert get_slack_provider() is mock_provider
    set_slack_provider(None)


def test_set_slack_provider_clears_singleton() -> None:
    from agent.slack.provider import get_slack_provider, set_slack_provider

    set_slack_provider(MagicMock())
    set_slack_provider(None)
    # Without token, get returns None
    assert get_slack_provider() is None


# ---------------------------------------------------------------------------
# post_message
# ---------------------------------------------------------------------------


def test_post_message_minimal() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_postMessage.return_value = MagicMock(data={"ok": True, "ts": "1.0"})

    provider.post_message(channel="C123", text="hello")

    mock_client.chat_postMessage.assert_called_once_with(channel="C123", text="hello")


def test_post_message_with_blocks() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_postMessage.return_value = MagicMock(data={"ok": True, "ts": "1.0"})
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]

    provider.post_message(channel="C123", text="hi", blocks=blocks)

    call_kwargs = mock_client.chat_postMessage.call_args[1]
    assert call_kwargs["blocks"] == blocks


def test_post_message_without_blocks_omits_key() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_postMessage.return_value = MagicMock(data={"ok": True, "ts": "1.0"})

    provider.post_message(channel="C123", text="hi")

    call_kwargs = mock_client.chat_postMessage.call_args[1]
    assert "blocks" not in call_kwargs


def test_post_message_with_thread_ts() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_postMessage.return_value = MagicMock(data={"ok": True, "ts": "1.0"})

    provider.post_message(channel="C123", text="hi", thread_ts="1234.5678")

    call_kwargs = mock_client.chat_postMessage.call_args[1]
    assert call_kwargs["thread_ts"] == "1234.5678"


def test_post_message_without_thread_ts_omits_key() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_postMessage.return_value = MagicMock(data={"ok": True, "ts": "1.0"})

    provider.post_message(channel="C123", text="hi")

    call_kwargs = mock_client.chat_postMessage.call_args[1]
    assert "thread_ts" not in call_kwargs


# ---------------------------------------------------------------------------
# update_message
# ---------------------------------------------------------------------------


def test_update_message_minimal() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_update.return_value = MagicMock(data={"ok": True})

    provider.update_message(channel="C123", ts="1.0", text="updated")

    mock_client.chat_update.assert_called_once_with(channel="C123", ts="1.0", text="updated")


def test_update_message_with_blocks() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_update.return_value = MagicMock(data={"ok": True})
    blocks = [{"type": "divider"}]

    provider.update_message(channel="C123", ts="1.0", text="updated", blocks=blocks)

    call_kwargs = mock_client.chat_update.call_args[1]
    assert call_kwargs["blocks"] == blocks


def test_update_message_without_blocks_omits_key() -> None:
    provider, mock_client = _make_provider()
    mock_client.chat_update.return_value = MagicMock(data={"ok": True})

    provider.update_message(channel="C123", ts="1.0", text="updated")

    call_kwargs = mock_client.chat_update.call_args[1]
    assert "blocks" not in call_kwargs


# ---------------------------------------------------------------------------
# set_status / clear_status
# ---------------------------------------------------------------------------


def test_set_status_calls_api() -> None:
    provider, mock_client = _make_provider()

    provider.set_status(channel="C123", thread_ts="1.0", status="Gathering information...")

    mock_client.assistant_threads_setStatus.assert_called_once_with(
        channel_id="C123", thread_ts="1.0", status="Gathering information..."
    )


def test_set_status_does_not_raise_on_exception() -> None:
    provider, mock_client = _make_provider()
    mock_client.assistant_threads_setStatus.side_effect = RuntimeError("API error")

    # Must not raise
    provider.set_status(channel="C123", thread_ts="1.0", status="Working...")


def test_clear_status_sends_empty_string() -> None:
    provider, mock_client = _make_provider()

    provider.clear_status(channel="C123", thread_ts="1.0")

    mock_client.assistant_threads_setStatus.assert_called_once_with(channel_id="C123", thread_ts="1.0", status="")


def test_clear_status_does_not_raise_on_exception() -> None:
    provider, mock_client = _make_provider()
    mock_client.assistant_threads_setStatus.side_effect = RuntimeError("API error")

    # Must not raise
    provider.clear_status(channel="C123", thread_ts="1.0")


# ---------------------------------------------------------------------------
# add_reaction / remove_reaction
# ---------------------------------------------------------------------------


def test_add_reaction_calls_api() -> None:
    provider, mock_client = _make_provider()

    provider.add_reaction(channel="C123", timestamp="1.0", name="hourglass_flowing_sand")

    mock_client.reactions_add.assert_called_once_with(channel="C123", timestamp="1.0", name="hourglass_flowing_sand")


def test_add_reaction_does_not_raise_on_exception() -> None:
    provider, mock_client = _make_provider()
    mock_client.reactions_add.side_effect = RuntimeError("already reacted")

    # Must not raise
    provider.add_reaction(channel="C123", timestamp="1.0", name="fire")


def test_remove_reaction_calls_api() -> None:
    provider, mock_client = _make_provider()

    provider.remove_reaction(channel="C123", timestamp="1.0", name="hourglass_flowing_sand")

    mock_client.reactions_remove.assert_called_once_with(channel="C123", timestamp="1.0", name="hourglass_flowing_sand")


def test_remove_reaction_does_not_raise_on_exception() -> None:
    provider, mock_client = _make_provider()
    mock_client.reactions_remove.side_effect = RuntimeError("not reacted")

    # Must not raise
    provider.remove_reaction(channel="C123", timestamp="1.0", name="fire")
