"""
Tests for chat thread initialization via streaming endpoints.

These tests verify that:
1. Streaming endpoints accept empty messages for initialization
2. They return thread info via SSE "init" event
3. Both global and case threads can be initialized properly
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_global_thread_init_with_empty_message():
    """Test that global thread endpoint returns init event for empty message."""
    from agent.api.webhook import _thread_send_stream
    from agent.memory.chat import ChatThread

    # Mock dependencies
    mock_request = MagicMock()
    mock_request.state = MagicMock()
    mock_request.state.user_key = "test-user"

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mock_thread = ChatThread(
        thread_id="test-thread-global",
        kind="global",
        user_key="test-user",
        case_id=None,
        title=None,
        created_at=now,
        updated_at=now,
        last_message_at=None,
    )

    with patch("agent.api.webhook._require_user_key", return_value="test-user"), patch(
        "agent.memory.chat.get_thread", return_value=(True, "", mock_thread)
    ), patch("agent.memory.chat.list_messages", return_value=(True, "", [])):

        # Call with empty message
        events = []
        async for event in _thread_send_stream(
            request=mock_request, thread_id="test-thread-global", raw={"message": None, "limit": 50}
        ):
            events.append(event)

        # Should have exactly one event
        assert len(events) == 1

        # Parse the SSE event
        event_str = events[0]
        assert "event: init" in event_str
        assert "data:" in event_str

        # Extract and parse the data
        data_line = [line for line in event_str.split("\n") if line.startswith("data:")][0]
        data_json = data_line.replace("data: ", "")
        data = json.loads(data_json)

        # Verify structure
        assert "thread" in data
        assert data["thread"]["thread_id"] == "test-thread-global"
        assert data["thread"]["kind"] == "global"
        assert "messages" in data
        assert isinstance(data["messages"], list)


@pytest.mark.asyncio
async def test_case_thread_init_with_empty_message():
    """Test that case thread endpoint returns init event for empty message."""
    from agent.api.webhook import _thread_send_stream
    from agent.memory.chat import ChatThread

    # Mock dependencies
    mock_request = MagicMock()
    mock_request.state = MagicMock()
    mock_request.state.user_key = "test-user"

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mock_thread = ChatThread(
        thread_id="test-thread-case-123",
        kind="case",
        user_key="test-user",
        case_id="case-123",
        title="Test Case",
        created_at=now,
        updated_at=now,
        last_message_at=None,
    )

    with patch("agent.api.webhook._require_user_key", return_value="test-user"), patch(
        "agent.memory.chat.get_thread", return_value=(True, "", mock_thread)
    ), patch("agent.memory.chat.list_messages", return_value=(True, "", [])):

        # Call with empty message
        events = []
        async for event in _thread_send_stream(
            request=mock_request,
            thread_id="test-thread-case-123",
            raw={"message": None, "run_id": "run-456", "limit": 50},
        ):
            events.append(event)

        # Should have exactly one event
        assert len(events) == 1

        # Parse the SSE event
        event_str = events[0]
        assert "event: init" in event_str

        # Extract and parse the data
        data_line = [line for line in event_str.split("\n") if line.startswith("data:")][0]
        data_json = data_line.replace("data: ", "")
        data = json.loads(data_json)

        # Verify case-specific fields
        assert data["thread"]["thread_id"] == "test-thread-case-123"
        assert data["thread"]["kind"] == "case"
        assert data["thread"]["case_id"] == "case-123"


@pytest.mark.asyncio
async def test_empty_message_returns_existing_messages():
    """Test that init event includes existing message history."""
    from agent.api.webhook import _thread_send_stream
    from agent.memory.chat import ChatMessage, ChatThread

    mock_request = MagicMock()
    mock_request.state = MagicMock()
    mock_request.state.user_key = "test-user"

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    mock_thread = ChatThread(
        thread_id="test-thread",
        kind="global",
        user_key="test-user",
        case_id=None,
        title=None,
        created_at=now,
        updated_at=now,
        last_message_at=None,
    )

    # Mock existing messages
    mock_messages = [
        ChatMessage(
            message_id="msg-1",
            seq=1,
            role="user",
            content="Hello",
            created_at="2024-01-01T00:00:00Z",
        ),
        ChatMessage(
            message_id="msg-2",
            seq=2,
            role="assistant",
            content="Hi there!",
            created_at="2024-01-01T00:00:01Z",
        ),
    ]

    with patch("agent.api.webhook._require_user_key", return_value="test-user"), patch(
        "agent.memory.chat.get_thread", return_value=(True, "", mock_thread)
    ), patch("agent.memory.chat.list_messages", return_value=(True, "", mock_messages)):

        events = []
        async for event in _thread_send_stream(
            request=mock_request, thread_id="test-thread", raw={"message": None, "limit": 50}
        ):
            events.append(event)

        # Parse the data
        data_line = [line for line in events[0].split("\n") if line.startswith("data:")][0]
        data = json.loads(data_line.replace("data: ", ""))

        # Should include messages
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello"
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][1]["content"] == "Hi there!"
