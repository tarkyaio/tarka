"""
Test that streaming chat runtimes do NOT block the asyncio event loop.

Before the fix, generate_json() and trace_tool_call() were called synchronously
inside async generators, blocking the uvicorn event loop.  K8s liveness probes
(/healthz every 20s) would time out and restart the pod.

These tests patch generate_json with a slow function (time.sleep) and verify
that a concurrent monitoring coroutine can still run — proving the blocking
calls are properly offloaded via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict
from unittest.mock import patch

import pytest


def _mock_analysis_json() -> Dict[str, Any]:
    return {
        "target": {"kind": "pod", "namespace": "default", "pod_name": "test-pod"},
        "analysis": {
            "verdict": {"label": "CPU throttling detected"},
            "hypotheses": [{"hypothesis_id": "cpu_throttle", "title": "CPU throttling", "confidence_0_100": 85}],
        },
    }


def _mock_policy():
    from agent.authz.policy import ChatPolicy

    return ChatPolicy(
        enabled=True,
        allow_promql=True,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=True,
        allow_report_rerun=False,
        allow_argocd_read=False,
        redact_secrets=False,
        max_tool_calls=5,
        max_steps=3,
    )


def _slow_generate_json(prompt, schema=None):
    """Simulate a slow LLM call that would block the event loop if not offloaded."""
    time.sleep(0.5)
    return (
        {
            "schema_version": "tarka.tool_plan.v1",
            "reply": "Looks like CPU throttling here.",
            "tool_calls": [],
            "meta": None,
        },
        None,
    )


def _noop_intent(*args, **kwargs):
    """Return an unhandled intent result to force the LLM path."""
    from agent.chat.intents import IntentResult

    return IntentResult(handled=False, reply="", tool_events=[])


@pytest.mark.asyncio
async def test_case_chat_stream_does_not_block_event_loop() -> None:
    """Verify that run_chat_stream offloads generate_json to a thread."""
    from agent.chat.runtime_streaming import run_chat_stream

    timestamps: list[float] = []
    stop = asyncio.Event()

    async def monitor():
        """Record timestamps; if the loop is blocked this won't run."""
        while not stop.is_set():
            timestamps.append(time.monotonic())
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass

    with (
        patch("agent.chat.runtime_streaming.generate_json", side_effect=_slow_generate_json),
        patch("agent.chat.runtime_streaming.try_handle_case_intents", side_effect=_noop_intent),
    ):
        monitor_task = asyncio.create_task(monitor())

        events = []
        async for event in run_chat_stream(
            policy=_mock_policy(),
            analysis_json=_mock_analysis_json(),
            user_message="Check the current CPU metrics for this pod right now",
            history=[],
        ):
            events.append(event)

        stop.set()
        await monitor_task

    # The monitor should have recorded multiple timestamps during the 0.5s sleep.
    # If the event loop was blocked, there would be at most 1-2 timestamps.
    assert len(timestamps) >= 3, (
        f"Event loop was blocked: monitor only ran {len(timestamps)} times "
        f"during a 0.5s blocking call (expected >= 3)"
    )

    # Also verify we still got a valid stream
    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_global_chat_stream_does_not_block_event_loop() -> None:
    """Verify that run_global_chat_stream offloads generate_json to a thread."""
    from agent.chat.global_runtime_streaming import run_global_chat_stream

    timestamps: list[float] = []
    stop = asyncio.Event()

    async def monitor():
        while not stop.is_set():
            timestamps.append(time.monotonic())
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass

    with (
        patch("agent.chat.global_runtime_streaming.generate_json", side_effect=_slow_generate_json),
        patch(
            "agent.chat.global_runtime_streaming.try_handle_global_intents",
            side_effect=_noop_intent,
        ),
    ):
        monitor_task = asyncio.create_task(monitor())

        events = []
        async for event in run_global_chat_stream(
            policy=_mock_policy(),
            user_message="How many open cases are there right now?",
            history=[],
        ):
            events.append(event)

        stop.set()
        await monitor_task

    assert len(timestamps) >= 3, (
        f"Event loop was blocked: monitor only ran {len(timestamps)} times "
        f"during a 0.5s blocking call (expected >= 3)"
    )

    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_no_tool_shortcut_skips_second_llm_call() -> None:
    """When no tools are requested, only one LLM call should be made."""
    from agent.chat.runtime_streaming import run_chat_stream

    call_count = 0

    def _counting_generate_json(prompt, schema=None):
        nonlocal call_count
        call_count += 1
        return (
            {
                "schema_version": "tarka.tool_plan.v1",
                "reply": "Everything looks fine from the diagnostics.",
                "tool_calls": [],
                "meta": None,
            },
            None,
        )

    with (
        patch("agent.chat.runtime_streaming.generate_json", side_effect=_counting_generate_json),
        patch("agent.chat.runtime_streaming.stream_text_response") as mock_stream,
        patch("agent.chat.runtime_streaming.try_handle_case_intents", side_effect=_noop_intent),
    ):
        events = []
        async for event in run_chat_stream(
            policy=_mock_policy(),
            analysis_json=_mock_analysis_json(),
            user_message="Explain the root cause analysis in detail",
            history=[],
        ):
            events.append(event)

        # Only 1 generate_json call (no second streaming call)
        assert call_count == 1
        # stream_text_response should NOT have been called
        mock_stream.assert_not_called()

    # Verify reply was streamed from the first call
    token_events = [e for e in events if e.event_type == "token"]
    assert len(token_events) >= 1
    full_text = "".join(e.content for e in token_events)
    assert "Everything looks fine" in full_text
