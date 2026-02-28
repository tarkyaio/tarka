"""Tests for Anthropic provider integration via langchain-anthropic."""

from __future__ import annotations

import sys
import types


def test_anthropic_requires_api_key(monkeypatch) -> None:
    """Test that Anthropic provider requires ANTHROPIC_API_KEY."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert obj is None
    assert err == "missing_api_key"


def test_anthropic_success_parses_json(monkeypatch) -> None:
    """Test that Anthropic provider works with mocked ChatAnthropic."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "256")

    # Stub langchain_anthropic.ChatAnthropic
    lc_mod = types.ModuleType("langchain_anthropic")

    class _Msg:
        def __init__(self, content: str):
            self.content = content

    class _ChatAnthropic:
        def __init__(self, **kwargs):
            # Validate we pass the required fields through
            assert kwargs.get("anthropic_api_key") == "sk-ant-test-key"
            assert kwargs.get("model") == "claude-3-5-sonnet-20241022"
            # Verify extended thinking is always enabled
            assert kwargs.get("thinking") == {"type": "enabled", "budget_tokens": 1024}

        def invoke(self, _prompt: str):
            return _Msg('{"ok": true, "answer": 42}')

    lc_mod.ChatAnthropic = _ChatAnthropic  # type: ignore[attr-defined]
    sys.modules["langchain_anthropic"] = lc_mod

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert err is None
    assert obj == {"ok": True, "answer": 42}


def test_anthropic_schema_structured_output(monkeypatch) -> None:
    """Test that Anthropic provider works with schema-based structured output."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "256")

    from agent.llm.schemas import ToolPlanResponse

    calls = {"structured_invoke": 0}

    # Stub langchain_anthropic.ChatAnthropic
    lc_mod = types.ModuleType("langchain_anthropic")

    class _ChatAnthropic:
        def __init__(self, **kwargs):
            assert kwargs.get("anthropic_api_key") == "sk-ant-test-key"
            assert kwargs.get("model") == "claude-3-5-sonnet-20241022"
            # Verify extended thinking is disabled for structured output (incompatible)
            assert kwargs.get("thinking") is None

        def with_structured_output(self, schema):
            # Ensure we request schema-based structured output
            assert schema is ToolPlanResponse

            class _Structured:
                def invoke(self, _prompt: str):
                    calls["structured_invoke"] += 1
                    return ToolPlanResponse(reply="ok", tool_calls=[])

            return _Structured()

        def invoke(self, _prompt: str):
            raise AssertionError("raw invoke should not be used when schema is provided")

    lc_mod.ChatAnthropic = _ChatAnthropic  # type: ignore[attr-defined]
    sys.modules["langchain_anthropic"] = lc_mod

    from agent.llm.client import generate_json

    obj, err = generate_json("hello", schema=ToolPlanResponse)
    assert err is None
    assert obj is not None
    assert obj.get("schema_version") == "tarka.tool_plan.v1"
    assert calls["structured_invoke"] == 1


def test_anthropic_error_classification(monkeypatch) -> None:
    """Test that Anthropic-specific errors are classified correctly."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("LLM_MODEL", "claude-3-5-sonnet-20241022")

    # Stub langchain_anthropic.ChatAnthropic to raise 429 error
    lc_mod = types.ModuleType("langchain_anthropic")

    class _ChatAnthropic:
        def __init__(self, **kwargs):
            pass

        def invoke(self, _prompt: str):
            raise Exception("429 Rate limit exceeded")

    lc_mod.ChatAnthropic = _ChatAnthropic  # type: ignore[attr-defined]
    sys.modules["langchain_anthropic"] = lc_mod

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert obj is None
    assert err == "rate_limited"


def test_anthropic_sdk_import_failure(monkeypatch) -> None:
    """Test that missing langchain-anthropic SDK is detected."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    # Remove langchain_anthropic from sys.modules to simulate missing SDK
    if "langchain_anthropic" in sys.modules:
        del sys.modules["langchain_anthropic"]

    # Mock the import to fail
    import builtins

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "langchain_anthropic":
            raise ImportError("No module named 'langchain_anthropic'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    from agent.llm.client import generate_json

    obj, err = generate_json("hello")
    assert obj is None
    assert err == "sdk_import_failed:langchain_anthropic"
