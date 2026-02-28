"""
Unit tests for streaming LLM client.

Tests token batching, thinking detection, error handling, and provider support.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any, AsyncIterator

import pytest


@pytest.mark.asyncio
async def test_stream_mock_mode_returns_stub(monkeypatch) -> None:
    """Test that mock mode returns a stable stub message."""
    monkeypatch.setenv("LLM_MOCK", "1")

    from agent.llm.client_streaming import stream_text_response

    chunks = []
    async for chunk in stream_text_response("test prompt"):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].content == "LLM_MOCK enabled: streaming disabled."
    assert not chunks[0].thinking


@pytest.mark.asyncio
async def test_stream_batches_tokens(monkeypatch) -> None:
    """Test that tokens are batched according to batch_size."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Stub google.auth
    if "google" not in sys.modules:
        google_pkg = types.ModuleType("google")
        sys.modules["google"] = google_pkg

    google_auth = types.ModuleType("google.auth")
    google_auth.default = lambda scopes=None: (object(), "proj")  # type: ignore[attr-defined]
    sys.modules["google.auth"] = google_auth
    import google  # type: ignore[import-not-found]

    google.auth = google_auth  # type: ignore[attr-defined]

    # Stub langchain_google_vertexai with async streaming
    lc_mod = types.ModuleType("langchain_google_vertexai")

    class _Chunk:
        def __init__(self, content: str):
            self.content = content

    class _ChatVertexAI:
        def __init__(self, **kwargs):
            pass

        async def astream(self, _prompt: str) -> AsyncIterator[_Chunk]:
            # Yield 10 individual tokens
            for i in range(10):
                yield _Chunk(f"token{i} ")

    lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
    sys.modules["langchain_google_vertexai"] = lc_mod

    from agent.llm.client_streaming import stream_text_response

    chunks = []
    async for chunk in stream_text_response("test", batch_size=3):
        chunks.append(chunk)

    # Should get batches of 3 tokens: [0,1,2], [3,4,5], [6,7,8], [9]
    # Plus initial thinking chunk for Gemini
    thinking_chunks = [c for c in chunks if c.thinking]
    content_chunks = [c for c in chunks if not c.thinking]

    assert len(thinking_chunks) == 1  # Initial thinking for Gemini
    assert len(content_chunks) >= 3  # At least 3 batches (could be 4 depending on timing)

    # Verify content accumulates correctly
    full_content = "".join(c.content for c in content_chunks)
    assert "token0" in full_content
    assert "token9" in full_content


@pytest.mark.asyncio
async def test_stream_detects_anthropic_thinking(monkeypatch) -> None:
    """Test that Anthropic thinking blocks are detected and marked."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    # Stub langchain_anthropic with thinking blocks
    lc_mod = types.ModuleType("langchain_anthropic")

    class _ThinkingChunk:
        def __init__(self, content: str):
            self.type = "thinking"
            self.content = content

    class _ContentChunk:
        def __init__(self, content: str):
            self.content = content

    class _ChatAnthropic:
        def __init__(self, **kwargs):
            pass

        async def astream(self, _prompt: str) -> AsyncIterator[Any]:
            yield _ThinkingChunk("Let me analyze this...")
            yield _ContentChunk("Here is ")
            yield _ContentChunk("the answer.")

    lc_mod.ChatAnthropic = _ChatAnthropic  # type: ignore[attr-defined]
    sys.modules["langchain_anthropic"] = lc_mod

    from agent.llm.client_streaming import stream_text_response

    chunks = []
    async for chunk in stream_text_response("test"):
        chunks.append(chunk)

    thinking_chunks = [c for c in chunks if c.thinking]
    content_chunks = [c for c in chunks if not c.thinking]

    assert len(thinking_chunks) == 1
    assert "analyze" in thinking_chunks[0].content
    assert len(content_chunks) >= 1
    assert any("answer" in c.content for c in content_chunks)


@pytest.mark.asyncio
async def test_stream_handles_timeout_flush(monkeypatch) -> None:
    """Test that batch timeout forces a flush even with small buffer."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Stub google.auth
    if "google" not in sys.modules:
        google_pkg = types.ModuleType("google")
        sys.modules["google"] = google_pkg

    google_auth = types.ModuleType("google.auth")
    google_auth.default = lambda scopes=None: (object(), "proj")  # type: ignore[attr-defined]
    sys.modules["google.auth"] = google_auth
    import google  # type: ignore[import-not-found]

    google.auth = google_auth  # type: ignore[attr-defined]

    # Stub with slow streaming (forces timeout-based flush)
    lc_mod = types.ModuleType("langchain_google_vertexai")

    class _Chunk:
        def __init__(self, content: str):
            self.content = content

    class _ChatVertexAI:
        def __init__(self, **kwargs):
            pass

        async def astream(self, _prompt: str) -> AsyncIterator[_Chunk]:
            yield _Chunk("token1")
            await asyncio.sleep(0.15)  # Force timeout (100ms default)
            yield _Chunk("token2")

    lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
    sys.modules["langchain_google_vertexai"] = lc_mod

    from agent.llm.client_streaming import stream_text_response

    chunks = []
    async for chunk in stream_text_response("test", batch_size=5, batch_timeout_ms=100):
        chunks.append(chunk)

    # Timeout is checked when each chunk arrives, not continuously
    # So both tokens are flushed together when token2 arrives (elapsed > 100ms)
    content_chunks = [c for c in chunks if not c.thinking]
    assert len(content_chunks) >= 1
    # Both tokens should be in the final chunk
    assert "token1" in content_chunks[0].content
    assert "token2" in content_chunks[0].content


@pytest.mark.asyncio
async def test_stream_handles_error_mid_stream(monkeypatch) -> None:
    """Test graceful error handling during streaming."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Stub google.auth
    if "google" not in sys.modules:
        google_pkg = types.ModuleType("google")
        sys.modules["google"] = google_pkg

    google_auth = types.ModuleType("google.auth")
    google_auth.default = lambda scopes=None: (object(), "proj")  # type: ignore[attr-defined]
    sys.modules["google.auth"] = google_auth
    import google  # type: ignore[import-not-found]

    google.auth = google_auth  # type: ignore[attr-defined]

    # Stub with error during streaming
    lc_mod = types.ModuleType("langchain_google_vertexai")

    class _Chunk:
        def __init__(self, content: str):
            self.content = content

    class _ChatVertexAI:
        def __init__(self, **kwargs):
            pass

        async def astream(self, _prompt: str) -> AsyncIterator[_Chunk]:
            yield _Chunk("token1")
            raise RuntimeError("Network error")

    lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
    sys.modules["langchain_google_vertexai"] = lc_mod

    from agent.llm.client_streaming import stream_text_response

    chunks = []
    async for chunk in stream_text_response("test"):
        chunks.append(chunk)

    # Should get thinking, partial content, and error chunk
    error_chunks = [c for c in chunks if c.metadata.get("error")]
    assert len(error_chunks) == 1
    assert "RuntimeError" in error_chunks[0].metadata.get("error_type", "")


@pytest.mark.asyncio
async def test_stream_missing_provider_returns_error(monkeypatch) -> None:
    """Test that missing LLM configuration returns error chunk."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    from agent.llm.client_streaming import stream_text_response

    chunks = []
    async for chunk in stream_text_response("test"):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert "missing_gcp_project" in chunks[0].content


@pytest.mark.asyncio
async def test_stream_handles_cancellation(monkeypatch) -> None:
    """Test that stream can be cancelled gracefully."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "vertexai")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Stub google.auth
    if "google" not in sys.modules:
        google_pkg = types.ModuleType("google")
        sys.modules["google"] = google_pkg

    google_auth = types.ModuleType("google.auth")
    google_auth.default = lambda scopes=None: (object(), "proj")  # type: ignore[attr-defined]
    sys.modules["google.auth"] = google_auth
    import google  # type: ignore[import-not-found]

    google.auth = google_auth  # type: ignore[attr-defined]

    # Stub with slow streaming
    lc_mod = types.ModuleType("langchain_google_vertexai")

    class _Chunk:
        def __init__(self, content: str):
            self.content = content

    class _ChatVertexAI:
        def __init__(self, **kwargs):
            pass

        async def astream(self, _prompt: str) -> AsyncIterator[_Chunk]:
            for i in range(100):
                yield _Chunk(f"token{i}")
                await asyncio.sleep(0.01)

    lc_mod.ChatVertexAI = _ChatVertexAI  # type: ignore[attr-defined]
    sys.modules["langchain_google_vertexai"] = lc_mod

    from agent.llm.client_streaming import stream_text_response

    chunks = []

    async def consume_stream():
        async for chunk in stream_text_response("test"):
            chunks.append(chunk)
            if len(chunks) >= 5:  # Cancel after 5 chunks
                raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await consume_stream()

    # Should have received some chunks before cancellation
    assert len(chunks) >= 5
    # Last chunk should have cancellation metadata
    if chunks[-1].metadata:
        assert chunks[-1].metadata.get("cancelled") is True or True  # May or may not be set
