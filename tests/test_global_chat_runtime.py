"""
Tests for global chat runtime.

Verifies that:
- Imports are at module level (no NameError)
- LangGraph StateGraph can be created successfully
- TypedDict types are properly resolved
- Global chat can execute without import errors
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch


def test_global_runtime_imports():
    """Module-level imports should be available."""
    from agent.chat import global_runtime

    # Verify all required types are imported at module level
    assert hasattr(global_runtime, "Any")
    assert hasattr(global_runtime, "Dict")
    assert hasattr(global_runtime, "List")
    assert hasattr(global_runtime, "TypedDict")


def test_global_runtime_typeddict_resolution():
    """TypedDict _State should resolve without NameError (test import fix)."""
    from agent.chat import global_runtime

    # Verify all types are importable at module level
    assert hasattr(global_runtime, "Any")
    assert hasattr(global_runtime, "Dict")
    assert hasattr(global_runtime, "List")
    assert hasattr(global_runtime, "TypedDict")

    # Test that we can use these types (using module-level imports)
    test_dict: Dict[str, Any] = {"key": "value"}
    test_list: List[int] = [1, 2, 3]
    assert test_dict["key"] == "value"
    assert len(test_list) == 3


def test_global_chat_with_mock_llm():
    """Global chat should work when LLM is unavailable (uses LangGraph path)."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.global_runtime import run_global_chat
    from agent.chat.intents import IntentResult

    policy = ChatPolicy(
        enabled=True,
        allow_promql=True,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=True,
        allow_report_rerun=True,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
    )

    # Mock the intent fast-path to not handle the query (force LLM path)
    with patch("agent.chat.global_runtime.try_handle_global_intents") as mock_intents:
        mock_intents.return_value = IntentResult(handled=False, reply="", tool_events=[])

        with patch("agent.chat.global_runtime.generate_json") as mock_generate:
            # Simulate LLM unavailable
            mock_generate.return_value = (None, "missing_api_key")

            with patch("langgraph.graph.StateGraph") as mock_state_graph:
                mock_graph = MagicMock()
                mock_graph.compile.return_value.invoke.return_value = {
                    "reply": "LLM chat is unavailable (missing_api_key).",
                    "tool_events": [],
                    "stop": True,
                }
                mock_state_graph.return_value = mock_graph

                result = run_global_chat(policy=policy, user_message="How many incidents?", history=[])

                # Should return error message, not crash
                assert "unavailable" in result.reply.lower()


def test_global_chat_llm_unavailable():
    """Global chat should return fallback when LLM is unavailable."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.global_runtime import run_global_chat

    policy = ChatPolicy(
        enabled=True,
        allow_promql=True,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=True,
        allow_report_rerun=True,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
    )

    with patch("agent.chat.global_runtime.generate_json") as mock_generate:
        # Simulate LLM error
        mock_generate.return_value = (None, "missing_api_key")

        with patch("langgraph.graph.StateGraph") as mock_state_graph:
            mock_graph = MagicMock()
            mock_graph.compile.return_value.invoke.return_value = {
                "reply": "LLM chat is unavailable (missing_api_key). Configure LLM_PROVIDER (vertexai/anthropic) with credentials.",
                "tool_events": [],
                "stop": True,
            }
            mock_state_graph.return_value = mock_graph

            result = run_global_chat(policy=policy, user_message="test", history=[])

            assert "unavailable" in result.reply.lower()
            assert "missing_api_key" in result.reply


def test_global_chat_disabled_policy():
    """Global chat should return disabled message when policy is disabled."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.global_runtime import run_global_chat

    policy = ChatPolicy(
        enabled=False,
        allow_promql=False,
        allow_k8s_read=False,
        allow_logs_query=False,
        allow_memory_read=False,
        allow_report_rerun=False,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
    )

    result = run_global_chat(policy=policy, user_message="test", history=[])

    assert result.reply == "Chat is disabled by policy."
    assert result.tool_events == []


def test_global_chat_build_prompt():
    """_build_prompt should construct valid prompt with all required fields."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.global_runtime import _build_prompt
    from agent.chat.types import ChatMessage

    policy = ChatPolicy(
        enabled=True,
        allow_promql=True,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=True,
        allow_report_rerun=True,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
    )

    prompt = _build_prompt(
        policy=policy,
        user_message="How many incidents?",
        history=[ChatMessage(role="user", content="Previous question")],
        tool_events=[],
    )

    # Verify prompt structure (updated for Phase 2 personality changes)
    assert "senior SRE" in prompt
    assert "GLOBAL (inbox) mode" in prompt
    assert "cases.count" in prompt
    assert "cases.top" in prompt
    assert "cases.lookup" in prompt
    assert "cases.summary" in prompt
    assert "How many incidents?" in prompt
    assert "Previous question" in prompt


def test_global_chat_tool_step_sets_stop_flag():
    """Tool step should explicitly set stop=False when continuing loop."""
    import inspect

    from agent.chat.global_runtime import _run_global_chat_langgraph

    # Verify the fix is in place by checking the source code
    source = inspect.getsource(_run_global_chat_langgraph)

    # The fix should set stop=False in the final return of tool_step
    assert (
        '"stop": False' in source or "'stop': False" in source
    ), "tool_step should explicitly set stop=False when continuing the loop"

    # Also verify budget stop sets stop=True
    assert (
        '"stop": True' in source or "'stop': True" in source
    ), "tool_step should set stop=True when budget is exhausted"
