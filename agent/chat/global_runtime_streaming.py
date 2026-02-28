"""
Streaming global chat runtime for inbox/console queries.

Similar to case chat streaming, but operates on case database queries rather than
single investigation SSOT.

Flow:
1. Thinking indicator ("Querying case database...")
2. Tool planning (BLOCKING with structured output)
3. Tool execution (cases.count, cases.top, etc.)
4. Final response (STREAMING text)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from agent.authz.policy import ChatPolicy, redact_text
from agent.chat.global_tools import run_global_tool
from agent.chat.intents import try_handle_global_intents
from agent.chat.tool_summaries import compact_args_for_prompt, summarize_tool_result, tool_call_key
from agent.chat.types import ChatMessage, ChatToolEvent
from agent.graphs.tracing import trace_tool_call
from agent.llm.client import generate_json
from agent.llm.client_streaming import stream_text_response
from agent.llm.schemas import ToolPlanResponse


@dataclass
class GlobalChatStreamEvent:
    """Single event in the global chat stream."""

    event_type: Literal["thinking", "planning", "tool_start", "tool_end", "token", "done", "error"]
    content: str = ""
    tool: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _allowed_tools() -> List[str]:
    """Get list of available global tools."""
    return ["cases.count", "cases.top", "cases.lookup", "cases.summary"]


def _get_global_tool_start_message(tool: str) -> str:
    """Get contextual message for global tool execution start."""
    messages = {
        "cases.count": "Counting cases...",
        "cases.top": "Looking at the distribution...",
        "cases.lookup": "Looking up that case...",
        "cases.summary": "Grabbing case summary...",
    }
    return messages.get(tool, f"Executing {tool}...")


def _build_tool_plan_prompt(
    *,
    policy: ChatPolicy,
    user_message: str,
    history: List[ChatMessage],
    tool_events: List[ChatToolEvent],
) -> str:
    """
    Build prompt for tool planning (structured output).
    """
    # Tool history (compact)
    tool_hist = []
    for ev in tool_events[-6:]:
        tool_hist.append(
            {
                "tool": ev.tool,
                "key": getattr(ev, "key", None),
                "outcome": getattr(ev, "outcome", None),
                "summary": getattr(ev, "summary", None),
                "args": compact_args_for_prompt(getattr(ev, "args", {}) or {}),
                "ok": ev.ok,
                "error": ev.error,
            }
        )

    # Redact history content
    hist_compact = []
    for m in history[-14:]:
        txt = redact_text(m.content) if policy.redact_secrets else (m.content or "")
        hist_compact.append({"role": m.role, "content": txt[:600]})

    tools = _allowed_tools()

    return (
        "You are an on-call SRE assistant embedded in an incident Console.\n"
        "You are in GLOBAL (inbox) mode.\n\n"
        "Tool Usage:\n"
        "- Use tools when the user asks about data you need to query (counts, trends, specific cases).\n"
        "- DO NOT use tools for greetings, thanks, or general conversation—just reply warmly.\n"
        "- Only ask the user for clarification when their question is genuinely ambiguous (e.g., time window not specified).\n"
        "- Investigation flow: Use tools → Share findings (warmly!) → Suggest next steps\n"
        "- Keep your conversational, empathetic personality.\n\n"
        "Hard constraints (must follow):\n"
        "- Use ONLY TOOL RESULTS to make claims about counts/trends.\n"
        "- Do NOT invent numbers, cases, or incidents.\n"
        "- If a question is ambiguous (e.g., time window), ask a clarifying question.\n"
        "- Return ONLY valid JSON. No markdown. No code fences.\n\n"
        "Available tools (call only these):\n"
        f"{json.dumps(tools)}\n\n"
        "Tool semantics:\n"
        "- cases.count: returns case counts filtered by status/team/family/classification (optionally since_hours).\n"
        "- cases.top: returns top keys by count (by=team|family|classification).\n"
        "- cases.lookup: resolves a case_id or prefix.\n"
        "- cases.summary: returns a minimal summary for a case.\n\n"
        "Examples (GOOD vs BAD):\n"
        "❌ BAD: 'You can query cases.count with status=\"open\"'\n"
        "✅ GOOD: 'Let me check open cases' → calls cases.count\n\n"
        "❌ BAD: 'Try cases.top to see top families'\n"
        "✅ GOOD: 'Let me see which families are most common' → calls cases.top\n\n"
        "Output JSON schema (exact keys):\n"
        "{\n"
        '  "schema_version": "tarka.tool_plan.v1",\n'
        '  "reply": string,\n'
        '  "tool_calls": [ { "tool": string, "args": object } ],\n'
        '  "meta": { "warnings": [string] } | null\n'
        "}\n"
        "Output constraints:\n"
        "- Keep `reply` short (<= 600 chars).\n"
        "- `tool_calls` must be 0-3 items.\n"
        "- Do NOT include any extra top-level keys.\n"
        "Rules:\n"
        "- Default to tool_calls: [] for greetings/pleasantries. Use tools for data questions.\n"
        "- If you're ready to answer (no more evidence needed), set tool_calls to [].\n"
        "- Otherwise, request 1-3 tool calls that are most likely to answer.\n"
        "- Don't repeat a tool call whose `key` already appears in TOOL_HISTORY.\n"
        "- If the last outcome was `empty` or `unavailable`, don't retry with identical args.\n"
        "- Use your database tools instead of suggesting queries to the user!\n"
        "- Keep your warm, conversational tone.\n\n"
        f"TOOL_HISTORY:\n{json.dumps(tool_hist, ensure_ascii=False)}\n\n"
        f"CHAT_HISTORY:\n{json.dumps(hist_compact, ensure_ascii=False)}\n\n"
        f"USER:\n{redact_text(user_message) if policy.redact_secrets else (user_message or '')}\n"
    )


def _build_final_response_prompt(
    *,
    policy: ChatPolicy,
    user_message: str,
    history: List[ChatMessage],
    tool_events: List[ChatToolEvent],
) -> str:
    """
    Build prompt for final natural language response (streaming).
    """
    # Include full tool results
    tool_results = []
    for ev in tool_events:
        tool_results.append(
            {
                "tool": ev.tool,
                "outcome": getattr(ev, "outcome", "unknown"),
                "summary": getattr(ev, "summary", None),
                "ok": ev.ok,
                "error": ev.error,
                "result": ev.result if ev.ok else None,
            }
        )

    # Redact history content
    hist_compact = []
    for m in history[-14:]:
        txt = redact_text(m.content) if policy.redact_secrets else (m.content or "")
        hist_compact.append({"role": m.role, "content": txt[:600]})

    return (
        "You are a senior SRE helping a colleague explore the incident database.\n\n"
        "Your personality:\n"
        "- Friendly and conversational - talk like a human\n"
        "- Practical and focused - surface actionable trends\n"
        "- Use contractions (I've, here's, let's) for natural flow\n"
        "- Autonomous and proactive - use your tools before asking user to query\n"
        '- Quick insights: "Interesting pattern..." not "Analysis shows:"\n\n'
        "Hard constraints (NEVER violate):\n"
        "- Use ONLY the provided TOOL RESULTS\n"
        "- Do NOT invent numbers, cases, or trends\n"
        "- Keep it SHORT (2-3 paragraphs, ~100 words max)\n"
        '- Be direct: "I found 5 cases" not "The query returned 5 results"\n\n'
        "Structure:\n"
        "1. Quick answer to their question\n"
        "2. Notable pattern or trend (if any)\n"
        "3. One follow-up suggestion (optional)\n\n"
        f"TOOL_RESULTS:\n{json.dumps(tool_results, ensure_ascii=False)}\n\n"
        f"CHAT_HISTORY:\n{json.dumps(hist_compact, ensure_ascii=False)}\n\n"
        f"USER:\n{redact_text(user_message) if policy.redact_secrets else (user_message or '')}\n\n"
        "Give them a clear, conversational answer:\n"
    )


async def run_global_chat_stream(
    *,
    policy: ChatPolicy,
    user_message: str,
    history: List[ChatMessage],
) -> AsyncGenerator[GlobalChatStreamEvent, None]:
    """
    Hybrid streaming global chat: structured tool planning + streaming final response.

    Flow:
    1. Emit thinking indicator ("Querying case database...")
    2. Tool planning (BLOCKING with structured output)
    3. Tool execution (emit start/end events)
    4. Final response (STREAMING text)
    5. Emit done event

    Args:
        policy: Chat policy
        user_message: User's message
        history: Chat history

    Yields:
        GlobalChatStreamEvent: Stream events
    """
    if not policy.enabled:
        yield GlobalChatStreamEvent(event_type="error", content="Chat is disabled by policy.")
        return

    # Fast-path: Try deterministic intent handling first
    try:
        ir = try_handle_global_intents(policy=policy, user_message=user_message)
        if ir.handled:
            # Stream the reply
            reply = ir.reply
            chunk_size = 50
            for i in range(0, len(reply), chunk_size):
                yield GlobalChatStreamEvent(
                    event_type="token",
                    content=reply[i : i + chunk_size],
                )
            yield GlobalChatStreamEvent(
                event_type="done",
                content=reply,
                metadata={"tool_events": [e.__dict__ for e in ir.tool_events]},
            )
            return
    except Exception:
        pass

    # Step 1: Emit initial thinking indicator
    yield GlobalChatStreamEvent(
        event_type="thinking",
        content="Querying case database to understand trends...",
    )

    # Track state
    tool_events: List[ChatToolEvent] = []
    remaining_calls = int(policy.max_tool_calls)

    # Multi-turn loop
    for step in range(int(policy.max_steps)):
        # Step 2: Tool Planning (BLOCKING with structured output)
        yield GlobalChatStreamEvent(
            event_type="planning",
            content="Planning database queries..." if step == 0 else "Determining next queries...",
        )

        prompt = _build_tool_plan_prompt(
            policy=policy,
            user_message=user_message,
            history=history,
            tool_events=tool_events,
        )

        # BLOCKING call with structured output
        obj, err = generate_json(prompt, schema=ToolPlanResponse)

        if err or not isinstance(obj, dict):
            yield GlobalChatStreamEvent(
                event_type="error",
                content=f"LLM unavailable ({err or 'unknown'}). Unable to query case database.",
            )
            return

        # Extract tool calls
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []

        # If no tool calls, proceed to final response
        if not tool_calls:
            break

        # Check budget
        if remaining_calls <= 0:
            yield GlobalChatStreamEvent(
                event_type="error",
                content="Reached tool-call limit. Please narrow your question.",
            )
            return

        # Step 3: Tool Execution
        # Dedupe
        seen_keys = set()
        for ev in tool_events:
            try:
                k0 = getattr(ev, "key", None) or tool_call_key(ev.tool, getattr(ev, "args", {}) or {})
                if k0:
                    seen_keys.add(str(k0))
            except Exception:
                continue

        ran_any = False
        for tc in tool_calls:
            if remaining_calls <= 0:
                break
            if not isinstance(tc, dict):
                continue

            tool = str(tc.get("tool") or "").strip()
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            k_req = tool_call_key(tool, args)

            # Skip duplicates
            if k_req in seen_keys:
                tool_events.append(
                    ChatToolEvent(
                        tool=tool,
                        args=args,
                        ok=False,
                        result={"skipped": True},
                        error="skipped_duplicate",
                        key=k_req,
                        outcome="skipped_duplicate",
                        summary=f"{tool}: skipped duplicate",
                    )
                )
                remaining_calls -= 1
                continue

            seen_keys.add(k_req)

            # Emit tool start
            yield GlobalChatStreamEvent(
                event_type="tool_start",
                tool=tool,
                content=_get_global_tool_start_message(tool),
            )

            # Execute tool
            try:
                res = trace_tool_call(
                    tool=tool,
                    args=args,
                    fn=lambda: run_global_tool(policy=policy, tool=tool, args=args),
                )
            except Exception as e:
                # Catch any unhandled exceptions from tool execution
                import logging

                logger = logging.getLogger(__name__)
                logger.exception(f"Tool {tool} raised unhandled exception")
                from agent.chat.tools import ToolResult

                res = ToolResult(ok=False, error=f"tool_exception:{type(e).__name__}:{str(e)[:200]}")

            # Summarize result
            outcome, summary = summarize_tool_result(tool=tool, ok=bool(res.ok), error=res.error, result=res.result)

            # Emit tool end
            yield GlobalChatStreamEvent(
                event_type="tool_end",
                tool=tool,
                content=summary,
                metadata={"outcome": outcome},
            )

            # Track event
            tool_events.append(
                ChatToolEvent(
                    tool=tool,
                    args=args,
                    ok=bool(res.ok),
                    result=res.result,
                    error=res.error,
                    key=k_req,
                    outcome=outcome,
                    summary=summary,
                )
            )

            remaining_calls -= 1
            ran_any = True

        if not ran_any:
            break

    # Step 4: Final Response (STREAMING text)
    final_prompt = _build_final_response_prompt(
        policy=policy,
        user_message=user_message,
        history=history,
        tool_events=tool_events,
    )

    full_reply_parts = []
    async for chunk in stream_text_response(final_prompt, enable_thinking=True):
        if chunk.thinking:
            yield GlobalChatStreamEvent(
                event_type="thinking",
                content=chunk.content,
            )
        else:
            full_reply_parts.append(chunk.content)
            yield GlobalChatStreamEvent(
                event_type="token",
                content=chunk.content,
            )

    # Step 5: Emit done
    full_reply = "".join(full_reply_parts)
    yield GlobalChatStreamEvent(
        event_type="done",
        content=full_reply,
        metadata={
            "tool_events": [
                {
                    "tool": e.tool,
                    "args": e.args,
                    "ok": e.ok,
                    "error": e.error,
                    "outcome": getattr(e, "outcome", None),
                    "summary": getattr(e, "summary", None),
                }
                for e in tool_events
            ],
        },
    )
