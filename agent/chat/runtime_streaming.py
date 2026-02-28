"""
Streaming chat runtime for progressive UX.

This module provides async streaming for chat responses with contextual feedback.
Uses a hybrid approach:
1. Tool planning: BLOCKING with structured output (reliable, fast <1s)
2. Tool execution: TRACKED with events (transparent progress)
3. Final response: STREAMING text (responsive UX)

Key benefits:
- 100% reliability for tool planning (structured output)
- Real-time feedback during tool execution
- Progressive token streaming for final response
- Contextual thinking indicators
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from agent.authz.policy import ActionPolicy, ChatPolicy, redact_text
from agent.chat.intents import try_handle_case_intents
from agent.chat.runtime import TOOL_DESCRIPTIONS
from agent.chat.tool_summaries import compact_args_for_prompt, summarize_tool_result, tool_call_key
from agent.chat.tools import run_tool
from agent.chat.types import ChatMessage, ChatToolEvent
from agent.graphs.tracing import trace_tool_call
from agent.llm.client import generate_json
from agent.llm.client_streaming import stream_text_response
from agent.llm.schemas import ToolPlanResponse


@dataclass
class ChatStreamEvent:
    """Single event in the chat stream."""

    event_type: Literal["thinking", "planning", "tool_start", "tool_end", "token", "done", "error"]
    content: str = ""
    tool: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def _allowed_tools(policy: ChatPolicy, action_policy: Optional[ActionPolicy]) -> List[str]:
    """Get list of allowed tools based on policy."""
    tools: List[str] = []
    if policy.allow_promql:
        tools.extend(["promql.instant"])
    if policy.allow_k8s_read:
        tools.extend(["k8s.pod_context", "k8s.rollout_status"])
    if policy.allow_k8s_events:
        tools.extend(["k8s.events"])
    if policy.allow_logs_query:
        tools.extend(["logs.tail"])
    if policy.allow_memory_read:
        tools.extend(["memory.similar_cases", "memory.skills"])
    if policy.allow_report_rerun:
        tools.extend(["rerun.investigation"])
    if policy.allow_argocd_read:
        tools.extend(["argocd.app_status"])
    if policy.allow_aws_read:
        tools.extend(
            [
                "aws.ec2_status",
                "aws.ebs_health",
                "aws.elb_health",
                "aws.rds_status",
                "aws.ecr_image",
                "aws.security_group",
                "aws.nat_gateway",
                "aws.vpc_endpoint",
                "aws.cloudtrail_events",
            ]
        )
    if policy.allow_github_read:
        tools.extend(
            [
                "github.recent_commits",
                "github.workflow_runs",
                "github.workflow_logs",
                "github.read_file",
            ]
        )
    if action_policy is not None and action_policy.enabled:
        tools.extend(["actions.list", "actions.propose"])
    return tools


def _get_tool_start_message(tool: str) -> str:
    """Get contextual message for tool execution start."""
    messages = {
        "promql.instant": "Checking the metrics...",
        "logs.tail": "Pulling recent logs...",
        "k8s.pod_context": "Looking at the pod status...",
        "k8s.rollout_status": "Checking rollout health...",
        "k8s.events": "Checking K8s events...",
        "memory.similar_cases": "Hmm, searching for similar incidents...",
        "memory.skills": "Let me check what's worked before...",
        "rerun.investigation": "Re-running investigation...",
        "actions.list": "Listing available actions...",
        "actions.propose": "I've got a suggestion...",
        "argocd.app_status": "Checking ArgoCD status...",
        "aws.ec2_status": "Checking EC2 instance status...",
        "aws.ebs_health": "Checking EBS volume health...",
        "aws.elb_health": "Checking load balancer health...",
        "aws.rds_status": "Checking RDS instance status...",
        "aws.ecr_image": "Checking ECR image details...",
        "aws.security_group": "Checking security group rules...",
        "aws.nat_gateway": "Checking NAT gateway status...",
        "aws.vpc_endpoint": "Checking VPC endpoint status...",
        "aws.cloudtrail_events": "Checking CloudTrail events...",
        "github.recent_commits": "Checking recent commits...",
        "github.workflow_runs": "Checking workflow runs...",
        "github.workflow_logs": "Checking workflow logs...",
        "github.read_file": "Reading file from GitHub...",
    }
    return messages.get(tool, f"Executing {tool}...")


def _build_tool_plan_prompt(
    *,
    policy: ChatPolicy,
    action_policy: Optional[ActionPolicy],
    analysis_json: Dict[str, Any],
    user_message: str,
    history: List[ChatMessage],
    tool_events: List[ChatToolEvent],
) -> str:
    """
    Build prompt for tool planning (structured output).
    This is BLOCKING but fast (<1s) and 100% reliable.
    """
    # Compact case context
    ctx: Dict[str, Any] = {}
    try:
        ctx["target"] = analysis_json.get("target")
        a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
        ctx["verdict"] = a.get("verdict")
        ctx["scores"] = a.get("scores")
        ctx["features"] = a.get("features")
        ctx["hypotheses"] = a.get("hypotheses") or []
        ctx["change"] = a.get("change")
        ctx["noise"] = a.get("noise")
    except Exception:
        ctx = {}

    # Add compact tool history
    tool_hist = []
    for ev in tool_events[-8:]:
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

    # Redact history content defensively
    hist_compact = []
    for m in history[-12:]:
        txt = redact_text(m.content) if policy.redact_secrets else (m.content or "")
        hist_compact.append({"role": m.role, "content": txt[:600]})

    tools = _allowed_tools(policy, action_policy)
    tool_list = "\n".join([f"- {t}: {TOOL_DESCRIPTIONS.get(t, 'No description')}" for t in tools])

    return (
        "You are an on-call SRE assistant.\n\n"
        "Tool Usage (precise, not exhaustive):\n"
        "- IMPORTANT: Only use tools when the user's question REQUIRES live data you don't already have in CASE JSON.\n"
        "- DO NOT use tools for: greetings, thanks, pleasantries, or questions answerable from CASE JSON alone.\n"
        "  If the user says 'hello', 'thanks', or 'what happened?', answer from CASE JSON with tool_calls: [].\n"
        "- When tools ARE needed, match scope precisely:\n"
        "  • Specific question (e.g. 'show me the commit') → 1 targeted tool call, then answer.\n"
        "  • Open investigation (e.g. 'dig deeper into the root cause') → 2-3 tool calls across sources.\n"
        "- Never suggest kubectl/aws/gh commands when you have equivalent tools.\n"
        "- Only ask the user for info outside your tool scope:\n"
        "  ✓ Ask user: Business context, policy decisions, access you don't have\n"
        "  ✗ Don't ask: K8s status, logs, metrics, AWS health, GitHub—you have tools!\n\n"
        "Hard constraints (must follow):\n"
        "- Use ONLY the provided CASE JSON + TOOL RESULTS.\n"
        "- Do NOT invent logs/metrics/events or root causes.\n"
        "- If you need more evidence, request tool calls.\n"
        "- Always cite evidence keys (e.g., analysis.hypotheses[0], features.k8s.waiting_reason) when making claims.\n"
        "- Return ONLY valid JSON. No markdown. No code fences.\n\n"
        "Available tools (call only these):\n"
        f"{tool_list}\n\n"
        "Examples (GOOD vs BAD):\n"
        "❌ BAD: 'Next steps: Run kubectl get statefulset mysql -n prod'\n"
        "✅ GOOD: 'Let me check the StatefulSet' → calls k8s.rollout_status\n\n"
        "❌ BAD: 'Check with: aws ec2 describe-instance-status'\n"
        "✅ GOOD: 'Let me check EC2 health' → calls aws.ec2_status\n\n"
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
        "- Default to tool_calls: [] unless the user's question specifically needs live/fresh data.\n"
        "- Questions about 'what happened', 'summarize', 'status' → answer from CASE JSON, tool_calls: [].\n"
        "- Questions needing live data ('check current pod status', 'query CPU metrics now') → 1-2 tool calls.\n"
        "- Don't repeat a tool call whose `key` already appears in TOOL_HISTORY.\n"
        "- If the last outcome was `empty` or `unavailable`, don't retry with identical args.\n"
        "- Never output kubectl/aws/gh commands in your reply—use your tools instead!\n"
        "- Keep your warm, conversational tone.\n\n"
        f"CASE:\n{json.dumps(ctx, sort_keys=True, ensure_ascii=False)}\n\n"
        f"TOOL_HISTORY:\n{json.dumps(tool_hist, ensure_ascii=False)}\n\n"
        f"CHAT_HISTORY:\n{json.dumps(hist_compact, ensure_ascii=False)}\n\n"
        f"USER:\n{redact_text(user_message) if policy.redact_secrets else (user_message or '')}\n"
    )


def _build_final_response_prompt(
    *,
    policy: ChatPolicy,
    analysis_json: Dict[str, Any],
    user_message: str,
    history: List[ChatMessage],
    tool_events: List[ChatToolEvent],
) -> str:
    """
    Build prompt for final natural language response (streaming).
    This includes all tool results and asks for a comprehensive answer.
    """
    # Compact case context
    ctx: Dict[str, Any] = {}
    try:
        ctx["target"] = analysis_json.get("target")
        a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
        ctx["verdict"] = a.get("verdict")
        ctx["scores"] = a.get("scores")
        ctx["features"] = a.get("features")
        ctx["hypotheses"] = a.get("hypotheses") or []
        ctx["change"] = a.get("change")
        ctx["noise"] = a.get("noise")
    except Exception:
        ctx = {}

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
    for m in history[-12:]:
        txt = redact_text(m.content) if policy.redact_secrets else (m.content or "")
        hist_compact.append({"role": m.role, "content": txt[:600]})

    return (
        "You are a senior SRE with 10+ years of on-call experience helping a colleague debug an incident.\n\n"
        "Your personality:\n"
        "- Friendly and approachable - you've been in their shoes\n"
        "- Practical and focused - cut through the noise to what matters\n"
        '- Conversational - talk like a human, not a bot ("Let\'s check..." not "I will analyze...")\n'
        "- Autonomous and proactive - use your tools before asking user to investigate\n"
        "- Slightly witty when appropriate - lighten the mood without being unprofessional\n"
        '- Honest about uncertainty - "I\'m not seeing X yet" beats vague speculation\n\n'
        "Communication style:\n"
        "- Use contractions (I've, let's, here's, can't) for natural flow\n"
        "- Vary sentence structure - mix short punchy insights with explanation\n"
        '- Lead with impact: "Here\'s what caught my eye..." not "Analysis results:"\n'
        '- Casual transitions: "Hmm", "Quick heads up", "By the way", "FYI"\n'
        '- Empathetic acknowledgment: "Rough day" when chaos is obvious\n\n'
        "Hard constraints (NEVER violate):\n"
        "- Use ONLY the provided CASE JSON + TOOL RESULTS\n"
        "- Do NOT invent logs/metrics/events or root causes\n"
        '- Always cite evidence (e.g., "the metrics container shows...")\n'
        "- Keep it SHORT (2-4 paragraphs, ~150 words max)\n"
        '- Be direct when data is missing: "I couldn\'t find logs" not "unavailable data suggests"\n\n'
        "Structure your response:\n"
        "1. Quick context (what's happening)\n"
        "2. Key insight (the smoking gun or main pattern)\n"
        "3. Next step (1-2 actionable items)\n\n"
        "Example tone:\n"
        '❌ "The investigation reveals CPU throttling with confidence 0.85."\n'
        "✅ \"Looks like we've got CPU throttling here - the container's hitting limits pretty hard.\"\n\n"
        '❌ "No log entries were retrieved from the backend."\n'
        '✅ "Hmm, I couldn\'t pull any logs from the backend. Worth checking if logging is actually configured for this namespace."\n\n'
        f"CASE:\n{json.dumps(ctx, sort_keys=True, ensure_ascii=False)}\n\n"
        f"TOOL_RESULTS:\n{json.dumps(tool_results, ensure_ascii=False)}\n\n"
        f"CHAT_HISTORY:\n{json.dumps(hist_compact, ensure_ascii=False)}\n\n"
        f"USER:\n{redact_text(user_message) if policy.redact_secrets else (user_message or '')}\n\n"
        "Give them a clear, conversational answer:\n"
    )


async def run_chat_stream(
    *,
    policy: ChatPolicy,
    action_policy: Optional[ActionPolicy] = None,
    analysis_json: Dict[str, Any],
    user_message: str,
    history: List[ChatMessage],
    case_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> AsyncGenerator[ChatStreamEvent, None]:
    """
    Hybrid streaming chat: structured tool planning + streaming final response.

    Flow:
    1. Emit thinking indicator
    2. Tool planning (BLOCKING with structured output - reliable, <1s)
    3. Tool execution (emit start/end events for each tool)
    4. Final response (STREAMING text - progressive UX)
    5. Emit done event

    Args:
        policy: Chat policy (controls tool access)
        action_policy: Action policy (controls remediation actions)
        analysis_json: Investigation SSOT
        user_message: User's message
        history: Chat history
        case_id: Case ID (for tracing)
        run_id: Run ID (for tracing)

    Yields:
        ChatStreamEvent: Stream events (thinking, tool_start, token, done, etc.)
    """
    if not policy.enabled:
        yield ChatStreamEvent(event_type="error", content="Chat is disabled by policy.")
        return

    # Fast-path: Try deterministic intent handling first
    try:
        ir = try_handle_case_intents(analysis_json=analysis_json, user_message=user_message)
        if ir.handled:
            # Stream the reply as tokens for consistent UX
            reply = ir.reply
            chunk_size = 50  # Stream in 50-char chunks for fast deterministic responses
            for i in range(0, len(reply), chunk_size):
                yield ChatStreamEvent(
                    event_type="token",
                    content=reply[i : i + chunk_size],
                )
            yield ChatStreamEvent(
                event_type="done",
                content=reply,
                metadata={"tool_events": [e.__dict__ for e in ir.tool_events]},
            )
            return
    except Exception:
        # If fast-path fails, fall through to LLM
        pass

    # Step 1: Emit initial thinking indicator
    yield ChatStreamEvent(
        event_type="thinking",
        content="Analyzing case evidence and determining next steps...",
    )

    # Track state across loop iterations
    tool_events: List[ChatToolEvent] = []
    updated_analysis: Optional[Dict[str, Any]] = None
    remaining_calls = int(policy.max_tool_calls)
    current_analysis = dict(analysis_json)

    # Multi-turn loop (like original runtime)
    for step in range(int(policy.max_steps)):
        # Step 2: Tool Planning (BLOCKING with structured output)
        yield ChatStreamEvent(
            event_type="planning",
            content="Planning investigation approach..." if step == 0 else "Determining next steps...",
        )

        prompt = _build_tool_plan_prompt(
            policy=policy,
            action_policy=action_policy,
            analysis_json=current_analysis,
            user_message=user_message,
            history=history,
            tool_events=tool_events,
        )

        # BLOCKING call with structured output (reliable, fast <1s)
        obj, err = generate_json(prompt, schema=ToolPlanResponse)

        if err or not isinstance(obj, dict):
            # LLM unavailable - emit error with fallback
            a = current_analysis.get("analysis") if isinstance(current_analysis.get("analysis"), dict) else {}
            hyps = a.get("hypotheses") if isinstance(a.get("hypotheses"), list) else []
            error_msg = f"LLM unavailable ({err or 'unknown'}). "
            if hyps:
                error_msg += "Top hypotheses from diagnostics:\n"
                for h in hyps[:3]:
                    if isinstance(h, dict):
                        error_msg += f"- {h.get('title')}: {h.get('confidence_0_100')}/100\n"
            yield ChatStreamEvent(event_type="error", content=error_msg)
            return

        # Extract tool calls
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []

        # If no tool calls, proceed to final response
        if not tool_calls:
            break

        # Check budget
        if remaining_calls <= 0:
            yield ChatStreamEvent(
                event_type="error",
                content="Reached tool-call limit. Please narrow your question.",
            )
            return

        # Step 3: Tool Execution (emit start/end events)
        # Dedupe: never re-run identical tool+args
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
            yield ChatStreamEvent(
                event_type="tool_start",
                tool=tool,
                content=_get_tool_start_message(tool),
            )

            # Execute tool (blocking)
            try:
                res = trace_tool_call(
                    tool=tool,
                    args=args,
                    fn=lambda: run_tool(
                        policy=policy,
                        action_policy=action_policy,
                        tool=tool,
                        args=args,
                        analysis_json=current_analysis,
                        case_id=case_id,
                        run_id=run_id,
                    ),
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
            yield ChatStreamEvent(
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

            # Update analysis if tool modified it
            if res.updated_analysis is not None:
                updated_analysis = res.updated_analysis
                try:
                    current_analysis = dict(current_analysis)
                    current_analysis["analysis"] = res.updated_analysis
                except Exception:
                    pass

        if not ran_any:
            # No tools executed - proceed to final response
            break

        # Continue loop if we still have budget and didn't hit max steps

    # Step 4: Final Response (STREAMING text)
    final_prompt = _build_final_response_prompt(
        policy=policy,
        analysis_json=current_analysis,
        user_message=user_message,
        history=history,
        tool_events=tool_events,
    )

    full_reply_parts = []
    async for chunk in stream_text_response(final_prompt, enable_thinking=True):
        if chunk.thinking:
            # Emit thinking chunk (Anthropic native thinking)
            yield ChatStreamEvent(
                event_type="thinking",
                content=chunk.content,
            )
        else:
            # Emit token
            full_reply_parts.append(chunk.content)
            yield ChatStreamEvent(
                event_type="token",
                content=chunk.content,
            )

    # Step 5: Emit done
    full_reply = "".join(full_reply_parts)
    yield ChatStreamEvent(
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
            "updated_analysis": updated_analysis,
        },
    )
