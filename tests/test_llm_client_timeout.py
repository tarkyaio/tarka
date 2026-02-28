"""
Tests for LLM client timeout configuration.

Verifies that:
- LLM_TIMEOUT_SECONDS is read from environment
- Timeout is applied to both Anthropic and Vertex AI clients
- Timeout bounds are enforced (5-300 seconds)
- Default timeout is 180 seconds
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def test_llm_timeout_default():
    """Default timeout should be 180 seconds (for multi-step RCA with tool calls)."""
    from agent.llm.client import _load_config

    with patch.dict(os.environ, {}, clear=True):
        config = _load_config()
        assert config.timeout == 180


def test_llm_timeout_from_env():
    """Timeout should be read from LLM_TIMEOUT_SECONDS."""
    from agent.llm.client import _load_config

    with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "30"}):
        config = _load_config()
        assert config.timeout == 30


def test_llm_timeout_bounds():
    """Timeout should be clamped to 5-300 seconds."""
    from agent.llm.client import _load_config

    # Too low
    with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "1"}):
        config = _load_config()
        assert config.timeout == 5

    # Too high
    with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "400"}):
        config = _load_config()
        assert config.timeout == 300

    # Valid range
    with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "60"}):
        config = _load_config()
        assert config.timeout == 60


def test_llm_timeout_invalid_value():
    """Invalid timeout should fallback to default (180 seconds)."""
    from agent.llm.client import _load_config

    with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "invalid"}):
        config = _load_config()
        assert config.timeout == 180


def test_llm_timeout_applied_to_anthropic():
    """Timeout should be passed to Anthropic client constructor."""
    from agent.llm.client import _get_llm_instance, _load_config

    with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "25", "ANTHROPIC_API_KEY": "sk-test-key"}):
        # Mock the import
        with patch.dict("sys.modules", {"langchain_anthropic": MagicMock()}):
            import sys

            mock_chat_anthropic = MagicMock()
            sys.modules["langchain_anthropic"].ChatAnthropic = mock_chat_anthropic
            mock_chat_anthropic.return_value = MagicMock()

            cfg_test = _load_config()
            llm, err = _get_llm_instance("anthropic", cfg_test, enable_thinking=True)

            assert err is None
            mock_chat_anthropic.assert_called_once()
            call_kwargs = mock_chat_anthropic.call_args[1]
            assert call_kwargs["timeout"] == 25
            assert call_kwargs["model"] == cfg_test.model


def test_llm_timeout_applied_to_vertexai():
    """Timeout should be passed to Vertex AI client constructor."""
    from agent.llm.client import _get_llm_instance, _load_config

    with patch.dict(
        os.environ,
        {
            "LLM_TIMEOUT_SECONDS": "25",
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        },
    ):
        with patch("google.auth.default") as mock_auth:
            mock_auth.return_value = (MagicMock(), "test-project")

            # Mock the import
            with patch.dict("sys.modules", {"langchain_google_vertexai": MagicMock()}):
                import sys

                mock_chat_vertex = MagicMock()
                sys.modules["langchain_google_vertexai"].ChatVertexAI = mock_chat_vertex
                mock_chat_vertex.return_value = MagicMock()

                cfg_test = _load_config()
                llm, err = _get_llm_instance("vertexai", cfg_test, enable_thinking=True)

                assert err is None
                mock_chat_vertex.assert_called_once()
                call_kwargs = mock_chat_vertex.call_args[1]
                assert call_kwargs["timeout"] == 25


def test_generate_json_with_mock_mode():
    """generate_json should work in mock mode without external dependencies."""
    from agent.llm.client import generate_json

    with patch.dict(os.environ, {"LLM_MOCK": "1"}):
        result, err = generate_json("test prompt")

        assert err is None
        assert isinstance(result, dict)
        assert "summary" in result or "likely_root_cause" in result


def test_timeout_config_persists_across_calls():
    """Timeout configuration should persist across multiple calls."""
    from agent.llm.client import _load_config

    with patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": "35"}):
        cfg1 = _load_config()
        cfg2 = _load_config()

        assert cfg1.timeout == 35
        assert cfg2.timeout == 35
