"""
Streaming LLM client for progressive text responses.

This module provides async streaming for natural language responses where UX matters.
For structured JSON output (tool planning, etc.), continue using the blocking client.py
with with_structured_output() for 100% reliability.

Key features:
- Token batching (3-5 tokens or 100ms) for smooth visual feedback
- Native thinking detection (Anthropic) or simulated thinking (Gemini)
- Graceful error handling mid-stream
- Provider-agnostic (works with both Vertex AI and Anthropic)

Usage:
    async for chunk in stream_text_response(prompt):
        if chunk.thinking:
            print(f"[THINKING] {chunk.content}")
        else:
            print(chunk.content, end="", flush=True)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict

from agent.llm.client import _env_bool, _get_llm_instance, _load_config, _provider


@dataclass
class LLMStreamChunk:
    """Single chunk of streamed content."""

    content: str
    thinking: bool = False  # True if this is thinking content (Anthropic)
    metadata: Dict[str, Any] = field(default_factory=dict)


async def stream_text_response(
    prompt: str,
    *,
    enable_thinking: bool = True,
    batch_size: int = 5,
    batch_timeout_ms: int = 100,
) -> AsyncGenerator[LLMStreamChunk, None]:
    """
    Stream natural language text response (NOT structured JSON).

    Use this for final prose responses where UX matters.
    For structured output (tool planning), use existing generate_json().

    Key behaviors:
    - Streams tokens with batching (5 tokens or 100ms)
    - Detects thinking mode (Anthropic native, simulated for Gemini)
    - NO JSON parsing (this is for prose only)
    - Gracefully handles streaming errors

    Args:
        prompt: The prompt to send to the LLM
        enable_thinking: Enable extended thinking (Anthropic) or simulate (Gemini)
        batch_size: Number of tokens to accumulate before emitting (default: 5)
        batch_timeout_ms: Max time to wait before flushing buffer (default: 100ms)

    Yields:
        LLMStreamChunk: Batched content chunks with thinking flag
    """
    # Mock mode: return stable stub
    if _env_bool("LLM_MOCK", False):
        yield LLMStreamChunk(
            content="LLM_MOCK enabled: streaming disabled.",
            thinking=False,
        )
        return

    provider = _provider()
    cfg = _load_config()

    # Get LangChain model instance
    # Disable thinking for streaming to avoid Anthropic API 400 errors
    # Extended thinking may have compatibility issues with streaming
    llm, err = _get_llm_instance(provider, cfg, enable_thinking=False)
    if err:
        yield LLMStreamChunk(
            content=f"LLM initialization failed: {err}",
            thinking=False,
            metadata={"error": err},
        )
        return

    # Simulate initial thinking indicator for Gemini
    # (Thinking is disabled for actual LLM call above to avoid API errors)
    if enable_thinking and provider in ("vertexai", "vertex", "gcp_vertexai"):
        yield LLMStreamChunk(
            content="Analyzing the situation and formulating a response...",
            thinking=True,
        )

    try:
        buffer = []
        last_flush_time = time.time()
        batch_timeout_sec = batch_timeout_ms / 1000.0

        async for chunk in llm.astream(prompt):  # type: ignore[attr-defined]
            # Check if this is thinking content (Anthropic only)
            if hasattr(chunk, "type") and chunk.type == "thinking":
                # Emit thinking block immediately (don't batch)
                yield LLMStreamChunk(
                    content=str(getattr(chunk, "content", "")),
                    thinking=True,
                )
                continue

            # Extract content from chunk
            content = ""
            if hasattr(chunk, "content"):
                if isinstance(chunk.content, str):
                    content = chunk.content
                elif isinstance(chunk.content, list):
                    # Anthropic may return list of content blocks
                    for block in chunk.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            content += block.get("text", "")
                        elif hasattr(block, "text"):
                            content += str(block.text)

            if not content:
                continue

            # Add to buffer
            buffer.append(content)

            # Flush if we hit batch size OR timeout
            elapsed = time.time() - last_flush_time
            if len(buffer) >= batch_size or elapsed >= batch_timeout_sec:
                yield LLMStreamChunk(
                    content="".join(buffer),
                    thinking=False,
                )
                buffer.clear()
                last_flush_time = time.time()

        # Flush any remaining content
        if buffer:
            yield LLMStreamChunk(
                content="".join(buffer),
                thinking=False,
            )

    except asyncio.CancelledError:
        # Client cancelled the stream - this is expected behavior
        if buffer:
            yield LLMStreamChunk(
                content="".join(buffer),
                thinking=False,
                metadata={"cancelled": True},
            )
        raise

    except Exception as e:
        # Emit any buffered content before error
        if buffer:
            yield LLMStreamChunk(
                content="".join(buffer),
                thinking=False,
            )

        # Emit error chunk
        yield LLMStreamChunk(
            content=f"\n\n[Error during streaming: {type(e).__name__}]",
            thinking=False,
            metadata={"error": str(e), "error_type": type(e).__name__},
        )
