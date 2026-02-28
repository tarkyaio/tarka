from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, TypedDict

from agent.authz.policy import ChatPolicy, redact_text
from agent.chat.global_tools import run_global_tool
from agent.chat.intents import try_handle_global_intents
from agent.chat.tool_summaries import compact_args_for_prompt, summarize_tool_result, tool_call_key
from agent.chat.types import ChatMessage, ChatToolEvent
from agent.graphs.tracing import build_invoke_config, trace_tool_call
from agent.llm.client import generate_json
from agent.llm.schemas import ToolPlanResponse


@dataclass(frozen=True)
class GlobalChatRunResult:
    reply: str
    tool_events: List[ChatToolEvent]


def _allowed_tools() -> List[str]:
    return ["cases.count", "cases.top", "cases.lookup", "cases.summary"]


def _build_prompt(
    *,
    policy: ChatPolicy,
    user_message: str,
    history: List[ChatMessage],
    tool_events: List[ChatToolEvent],
) -> str:
    # Tool history (compact) to reduce repetition.
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

    # Redact history content defensively.
    hist_compact = []
    for m in history[-14:]:
        txt = redact_text(m.content) if policy.redact_secrets else (m.content or "")
        hist_compact.append({"role": m.role, "content": txt[:600]})

    tools = _allowed_tools()

    # NOTE: this chat does not have a single-case SSOT; tools query Postgres.
    return (
        "You are a senior SRE helping a colleague explore the incident database.\n\n"
        "Personality (for the reply field):\n"
        "- Friendly and conversational - use contractions (I've, here's, let's)\n"
        '- Direct and practical - "I found 5 cases" not "The query returned 5 results"\n'
        '- Quick insights - "Interesting pattern..." not "Analysis shows:"\n\n'
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
        '  "reply": string,  // Natural, conversational answer (<= 600 chars)\n'
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


def _run_global_chat_langgraph(
    *, policy: ChatPolicy, user_message: str, history: List[ChatMessage]
) -> GlobalChatRunResult:
    """
    LangGraph-based GLOBAL chat loop (same semantics as the legacy loop).
    """
    from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]

    class _State(TypedDict, total=False):
        user_message: str
        history: List[ChatMessage]
        tool_events: List[ChatToolEvent]
        remaining_calls: int
        reply: str
        tool_calls: List[Dict[str, Any]]
        stop: bool

    # NOTE: Avoid annotating with the local `_State` type.
    # LangGraph may evaluate type hints via `typing.get_type_hints`, and local forward refs
    # can raise `NameError: name '_State' is not defined` in production.
    def llm_step(state):
        prompt = _build_prompt(
            policy=policy,
            user_message=state.get("user_message") or "",
            history=state.get("history") or [],
            tool_events=state.get("tool_events") or [],
        )
        obj, err = generate_json(prompt, schema=ToolPlanResponse)
        if err or not isinstance(obj, dict):
            return {
                **state,
                "reply": f"LLM chat is unavailable ({err or 'unknown'}). Configure LLM_PROVIDER (vertexai/anthropic) with credentials.",
                "tool_calls": [],
                "stop": True,
            }
        reply = str(obj.get("reply") or "").strip()
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []
        return {**state, "reply": reply or "OK.", "tool_calls": tool_calls, "stop": False}

    def tool_step(state):
        tool_events = list(state.get("tool_events") or [])
        remaining_calls = int(state.get("remaining_calls") or 0)
        tool_calls = state.get("tool_calls") or []
        ran_any = False

        # Executor-side dedupe: never re-run identical tool+args within this invocation.
        seen_keys = set()
        for ev in tool_events:
            try:
                k0 = getattr(ev, "key", None) or tool_call_key(ev.tool, getattr(ev, "args", {}) or {})
                if k0:
                    seen_keys.add(str(k0))
            except Exception:
                continue

        for tc in tool_calls:
            if remaining_calls <= 0:
                break
            if not isinstance(tc, dict):
                continue
            tool = str(tc.get("tool") or "").strip()
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            k_req = tool_call_key(tool, args)
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
                        summary=f"{tool}: skipped duplicate tool call",
                    )
                )
                remaining_calls -= 1
                # Don't set ran_any = True for skipped duplicates
                # This prevents infinite loops when LLM keeps requesting the same tool
                continue
            seen_keys.add(k_req)
            try:
                res = trace_tool_call(
                    tool=tool, args=args, fn=lambda: run_global_tool(policy=policy, tool=tool, args=args)
                )
            except Exception as e:
                # Catch any unhandled exceptions from tool execution
                import logging

                logger = logging.getLogger(__name__)
                logger.exception(f"Tool {tool} raised unhandled exception")
                from agent.chat.tools import ToolResult

                res = ToolResult(ok=False, error=f"tool_exception:{type(e).__name__}:{str(e)[:200]}")
            k = k_req
            outcome, summary = summarize_tool_result(tool=tool, ok=bool(res.ok), error=res.error, result=res.result)
            tool_events.append(
                ChatToolEvent(
                    tool=tool,
                    args=args,
                    ok=bool(res.ok),
                    result=res.result,
                    error=res.error,
                    key=k,
                    outcome=outcome,
                    summary=summary,
                )
            )
            remaining_calls -= 1
            ran_any = True

        if not ran_any:
            return {
                **state,
                "reply": (state.get("reply") or "I couldn't run the requested tools. Please rephrase."),
                "stop": True,
                "tool_calls": [],
                "tool_events": tool_events,
                "remaining_calls": remaining_calls,
            }

        if remaining_calls <= 0:
            return {
                **state,
                "reply": "I reached the tool-call limit for this chat turn. Please narrow the question (e.g., specify team/family).",
                "stop": True,
                "tool_calls": [],
                "tool_events": tool_events,
                "remaining_calls": remaining_calls,
            }

        # Continue the loop (don't stop yet)
        return {**state, "tool_events": tool_events, "remaining_calls": remaining_calls, "stop": False}

    def route_after_llm(state) -> str:
        if state.get("stop"):
            return "end"
        if not (state.get("tool_calls") or []):
            return "end"
        if int(state.get("remaining_calls") or 0) <= 0:
            return "end"
        return "tools"

    def route_after_tools(state) -> str:
        if state.get("stop"):
            return "end"
        if int(state.get("remaining_calls") or 0) <= 0:
            return "end"
        return "llm"

    g = StateGraph(_State)
    g.add_node("llm", llm_step)
    g.add_node("tools", tool_step)
    g.set_entry_point("llm")
    g.add_conditional_edges("llm", route_after_llm, {"tools": "tools", "end": END})
    g.add_conditional_edges("tools", route_after_tools, {"llm": "llm", "end": END})

    app = g.compile()
    init = {
        "user_message": user_message,
        "history": history,
        "tool_events": [],
        "remaining_calls": int(policy.max_tool_calls),
        "reply": "",
        "tool_calls": [],
        "stop": False,
    }
    meta = {"max_steps": int(policy.max_steps), "max_tool_calls": int(policy.max_tool_calls)}
    cfg = build_invoke_config(kind="global_chat", run_name="global_chat", metadata=meta)
    cfg["recursion_limit"] = int(policy.max_steps) + 5
    out = app.invoke(init, config=cfg)
    return GlobalChatRunResult(
        reply=str(out.get("reply") or "").strip() or "OK.", tool_events=list(out.get("tool_events") or [])
    )


def run_global_chat(*, policy: ChatPolicy, user_message: str, history: List[ChatMessage]) -> GlobalChatRunResult:
    """
    Tool-using chat loop for GLOBAL (inbox/fleet) questions.
    """
    if not policy.enabled:
        return GlobalChatRunResult(reply="Chat is disabled by policy.", tool_events=[])

    # Fast-path deterministic intents (avoid LLM cost/truncation for count/top/lookup style questions).
    try:
        ir = try_handle_global_intents(policy=policy, user_message=user_message)
        if ir.handled:
            return GlobalChatRunResult(reply=ir.reply, tool_events=ir.tool_events)
    except Exception:
        pass
    # LangGraph-based loop (primary).
    return _run_global_chat_langgraph(policy=policy, user_message=user_message, history=history)

    tool_events: List[ChatToolEvent] = []
    remaining_calls = int(policy.max_tool_calls)

    for _step in range(int(policy.max_steps)):
        prompt = _build_prompt(policy=policy, user_message=user_message, history=history, tool_events=tool_events)
        obj, err = generate_json(prompt, schema=ToolPlanResponse)
        if err or not isinstance(obj, dict):
            return GlobalChatRunResult(
                reply="LLM chat is unavailable. Ask about a specific case in Case Detail view, or configure Gemini.",
                tool_events=tool_events,
            )

        reply = str(obj.get("reply") or "").strip()
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []

        if not tool_calls:
            return GlobalChatRunResult(reply=reply or "OK.", tool_events=tool_events)

        ran_any = False
        for tc in tool_calls:
            if remaining_calls <= 0:
                break
            if not isinstance(tc, dict):
                continue
            tool = str(tc.get("tool") or "").strip()
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            res = run_global_tool(policy=policy, tool=tool, args=args)
            ev = ChatToolEvent(tool=tool, args=args, ok=bool(res.ok), result=res.result, error=res.error)
            tool_events.append(ev)
            remaining_calls -= 1
            ran_any = True

        if not ran_any:
            return GlobalChatRunResult(
                reply=(reply or "I couldn't run the requested tools. Please rephrase."), tool_events=tool_events
            )

    return GlobalChatRunResult(
        reply="I reached the tool-call limit for this chat turn. Please narrow the question (e.g., specify team/family).",
        tool_events=tool_events,
    )
