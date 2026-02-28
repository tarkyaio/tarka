"""
Unit tests for global chat streaming runtime.

Tests global chat event emission and database query scenarios.
"""

from __future__ import annotations

import pytest


def _mock_policy():
    """Return mock chat policy."""
    from agent.authz.policy import ChatPolicy

    return ChatPolicy(
        enabled=True,
        allow_promql=False,  # Global chat doesn't use PromQL
        allow_k8s_read=False,
        allow_logs_query=False,
        allow_memory_read=False,
        redact_secrets=False,
        max_tool_calls=5,
        max_steps=3,
    )


@pytest.mark.asyncio
async def test_global_stream_emits_thinking_event(monkeypatch) -> None:
    """Test that global streaming emits initial thinking event."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="Show me the correlation between CPU throttling and OOM incidents",
        history=[],
    ):
        events.append(event)

    # Should have at least thinking and done events
    thinking_events = [e for e in events if e.event_type == "thinking"]
    assert len(thinking_events) >= 1
    # Global chat should mention "case database" or "querying"
    assert any(
        "case" in e.content.lower() or "query" in e.content.lower() or "database" in e.content.lower()
        for e in thinking_events
    )


@pytest.mark.asyncio
async def test_global_stream_emits_planning_event(monkeypatch) -> None:
    """Test that global streaming emits planning event."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="Show me team distribution",
        history=[],
    ):
        events.append(event)

    planning_events = [e for e in events if e.event_type == "planning"]
    assert len(planning_events) >= 1
    # Should mention "database" or "queries"
    assert any("query" in e.content.lower() or "database" in e.content.lower() for e in planning_events)


@pytest.mark.asyncio
async def test_global_stream_emits_done_event(monkeypatch) -> None:
    """Test that global streaming completes with done event."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="test",
        history=[],
    ):
        events.append(event)

    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 1
    assert done_events[0].content  # Has reply content
    assert "tool_events" in done_events[0].metadata


@pytest.mark.asyncio
async def test_global_stream_handles_fast_path_intents(monkeypatch) -> None:
    """Test that deterministic global intents bypass LLM."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    # "help" should trigger fast-path intent
    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="help",
        history=[],
    ):
        events.append(event)

    # Should get done event
    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_global_stream_emits_token_events(monkeypatch) -> None:
    """Test that global streaming emits progressive token events."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="Show me recent trends",
        history=[],
    ):
        events.append(event)

    token_events = [e for e in events if e.event_type == "token"]
    # Should have some token events
    assert len(token_events) >= 1

    # Tokens should accumulate to match done content
    done_events = [e for e in events if e.event_type == "done"]
    if done_events and token_events:
        accumulated = "".join(e.content for e in token_events)
        # Accumulated tokens should be part of final reply
        assert accumulated in done_events[0].content or done_events[0].content in accumulated


@pytest.mark.asyncio
async def test_global_stream_respects_disabled_policy(monkeypatch) -> None:
    """Test that disabled policy returns error event."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.authz.policy import ChatPolicy
    from agent.chat.global_runtime_streaming import run_global_chat_stream

    disabled_policy = ChatPolicy(enabled=False)

    events = []
    async for event in run_global_chat_stream(
        policy=disabled_policy,
        user_message="test",
        history=[],
    ):
        events.append(event)

    error_events = [e for e in events if e.event_type == "error"]
    assert len(error_events) == 1
    assert "disabled" in error_events[0].content.lower()


@pytest.mark.asyncio
async def test_global_stream_event_sequence_order(monkeypatch) -> None:
    """Test that events are emitted in correct order."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="test",
        history=[],
    ):
        events.append(event)

    event_types = [e.event_type for e in events]

    # Expected sequence: thinking → planning → [tool_start/tool_end]* → token* → done
    assert event_types[0] == "thinking"
    assert "done" in event_types  # Should end with done

    # Planning should come before tokens
    if "planning" in event_types and "token" in event_types:
        planning_idx = event_types.index("planning")
        first_token_idx = event_types.index("token")
        assert planning_idx < first_token_idx


@pytest.mark.asyncio
async def test_global_stream_includes_tool_events_in_metadata(monkeypatch) -> None:
    """Test that done event includes tool execution metadata."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="Count cases",
        history=[],
    ):
        events.append(event)

    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 1

    metadata = done_events[0].metadata
    assert "tool_events" in metadata
    assert isinstance(metadata["tool_events"], list)


@pytest.mark.asyncio
async def test_global_stream_handles_llm_error(monkeypatch) -> None:
    """Test that LLM errors are handled gracefully."""
    # Don't set LLM_MOCK, but also don't configure provider
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="test",
        history=[],
    ):
        events.append(event)

    # Should get error event due to missing configuration
    error_events = [e for e in events if e.event_type == "error"]
    assert len(error_events) >= 1
    assert any("missing_gcp_project" in e.content or "unavailable" in e.content for e in error_events)


@pytest.mark.asyncio
async def test_global_stream_contextual_thinking_messages(monkeypatch) -> None:
    """Test that thinking messages are contextual for global chat."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="test",
        history=[],
    ):
        events.append(event)

    thinking_events = [e for e in events if e.event_type == "thinking"]
    assert len(thinking_events) >= 1
    # Should mention "case", "database", or "query" for global chat
    first_thinking = thinking_events[0].content.lower()
    assert "case" in first_thinking or "database" in first_thinking or "query" in first_thinking


@pytest.mark.asyncio
async def test_global_stream_respects_max_steps(monkeypatch) -> None:
    """Test that streaming respects max_steps limit."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.authz.policy import ChatPolicy
    from agent.chat.global_runtime_streaming import run_global_chat_stream

    limited_policy = ChatPolicy(
        enabled=True,
        max_tool_calls=10,
        max_steps=1,  # Only 1 step
    )

    events = []
    async for event in run_global_chat_stream(
        policy=limited_policy,
        user_message="test",
        history=[],
    ):
        events.append(event)

    # Should complete without infinite loop
    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_global_stream_different_thinking_than_case(monkeypatch) -> None:
    """Test that global chat has different thinking messages than case chat."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.chat.global_runtime_streaming import run_global_chat_stream

    events = []
    async for event in run_global_chat_stream(
        policy=_mock_policy(),
        user_message="test",
        history=[],
    ):
        events.append(event)

    thinking_events = [e for e in events if e.event_type == "thinking"]
    assert len(thinking_events) >= 1

    # Global chat should NOT mention "evidence" (that's case-specific)
    # Should mention "database" or "cases" instead
    first_thinking = thinking_events[0].content.lower()
    assert "evidence" not in first_thinking  # Case-specific term
    assert "database" in first_thinking or "case" in first_thinking or "query" in first_thinking
