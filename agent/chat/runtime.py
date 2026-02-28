from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.authz.policy import ActionPolicy, ChatPolicy, redact_text
from agent.chat.intents import try_handle_case_intents
from agent.chat.tool_summaries import compact_args_for_prompt, summarize_tool_result, tool_call_key
from agent.chat.tools import ToolResult, run_tool
from agent.chat.types import ChatMessage, ChatToolEvent
from agent.graphs.tracing import build_invoke_config, trace_tool_call
from agent.llm.client import generate_json
from agent.llm.schemas import ToolPlanResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatRunResult:
    reply: str
    tool_events: List[ChatToolEvent]
    updated_analysis: Optional[Dict[str, Any]] = None


TOOL_DESCRIPTIONS = {
    "promql.instant": "Query Prometheus metrics (instant query)",
    "k8s.pod_context": "Get K8s pod info (status, conditions, events)",
    "k8s.rollout_status": "Check K8s workload rollout status",
    "k8s.events": "Query Kubernetes cluster events for pods/workloads/namespaces",
    "logs.tail": "Fetch recent logs from a pod/container",
    "memory.similar_cases": "Search past incidents for similar cases",
    "memory.skills": "Retrieve learned skills/patterns from past incidents",
    "rerun.investigation": "Re-run investigation with different time window (args: time_window required e.g. '30m', '1h', '2h'; reference_time optional: 'original' uses alert time (default), 'now' uses current time)",
    "argocd.app_status": "Get ArgoCD application deployment status",
    "aws.ec2_status": "Check AWS EC2 instance health",
    "aws.ebs_health": "Check AWS EBS volume health",
    "aws.elb_health": "Check AWS ELB/ALB target health",
    "aws.rds_status": "Check AWS RDS database status",
    "aws.ecr_image": "Get AWS ECR image details",
    "aws.security_group": "Get AWS security group rules",
    "aws.nat_gateway": "Check AWS NAT gateway status",
    "aws.vpc_endpoint": "Check AWS VPC endpoint status",
    "aws.cloudtrail_events": "Query AWS CloudTrail for infrastructure changes (all args optional: start_time, end_time, resource_ids, region auto-discovered from investigation)",
    "aws.s3_bucket_location": "Get S3 bucket region/location (args: bucket - auto-extracts from logs if not provided)",
    "aws.iam_role_permissions": "Get IAM role permissions grouped by service (args: role_name OR service_account+namespace - auto-extracts role from K8s service account if service_account provided)",
    "github.recent_commits": "Get recent commits (repo auto-discovered; args: limit (max 30, default 20), branch, since ISO timestamp. Auto-widens from 2h to 24h if no commits found. Do NOT retry with a larger limit—results are capped at 30)",
    "github.workflow_runs": "Get GitHub Actions workflow runs (repo auto-discovered; you can pass a bare workload name or 'org/repo' if known)",
    "github.workflow_logs": "Get GitHub Actions workflow logs (repo auto-discovered; you can pass a bare workload name or 'org/repo' if known)",
    "github.read_file": "Read a file from GitHub repo (repo auto-discovered; you can pass a bare workload name or 'org/repo' if known)",
    "github.commit_diff": "Get changed files and patch for a specific commit (args: sha required, repo auto-discovered)",
    "actions.list": "List available remediation actions",
    "actions.propose": "Propose a remediation action",
}


def _allowed_tools(policy: ChatPolicy, action_policy: Optional[ActionPolicy]) -> List[str]:
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
                "github.commit_diff",
            ]
        )
    if action_policy is not None and action_policy.enabled:
        tools.extend(["actions.list", "actions.propose"])
    return tools


def _build_prompt(
    *,
    policy: ChatPolicy,
    action_policy: Optional[ActionPolicy],
    analysis_json: Dict[str, Any],
    user_message: str,
    history: List[ChatMessage],
    tool_events: List[ChatToolEvent],
) -> str:
    """
    Build a strict JSON-only prompt for a tool-using SRE chat.
    """
    # Compact case context; do not include raw logs (tools fetch them on-demand).
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
        # Include lightweight evidence metadata so the LLM can pass repo/bucket in tool args.
        ev = analysis_json.get("evidence") if isinstance(analysis_json.get("evidence"), dict) else {}
        if ev:
            ctx["evidence"] = ev
    except Exception:
        ctx = {}

    # Add a compact tool history so the model doesn't repeat itself.
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

    # Redact history content defensively.
    hist_compact = []
    for m in history[-12:]:
        txt = redact_text(m.content) if policy.redact_secrets else (m.content or "")
        hist_compact.append({"role": m.role, "content": txt[:600]})

    tools = _allowed_tools(policy, action_policy)
    tool_list = "\n".join([f"- {t}: {TOOL_DESCRIPTIONS.get(t, 'No description')}" for t in tools])

    return (
        "You are a senior SRE with 10+ years of on-call experience helping a colleague debug an incident.\n\n"
        "Personality (for the reply field):\n"
        "- Friendly and conversational - use contractions (I've, let's, here's)\n"
        '- Practical and direct - "Looks like CPU throttling" not "Analysis indicates"\n'
        '- Honest about uncertainty - "I\'m not seeing logs yet" not "unavailable data suggests"\n\n'
        "Tool Usage (precise, not exhaustive):\n"
        "- IMPORTANT: Only use tools when the user's question REQUIRES live data you don't already have in CASE JSON.\n"
        "- DO NOT use tools for: greetings, thanks, pleasantries, or questions answerable from CASE JSON alone.\n"
        "  If the user says 'hello', 'thanks', or 'what happened?', answer from CASE JSON with tool_calls: [].\n"
        "- When tools ARE needed, match scope precisely:\n"
        "  • Specific question (e.g. 'show me the commit') → 1 targeted tool call, then answer.\n"
        "  • Open investigation (e.g. 'dig deeper into the root cause') → 2-3 tool calls across sources.\n"
        "- Never suggest kubectl/aws/gh commands when you have equivalent tools.\n"
        "- CRITICAL: If a tool call fails or errors, report the failure to the user immediately.\n"
        "  Do NOT silently pivot to unrelated tools when the relevant tool fails.\n"
        "  Example: github.recent_commits fails → reply explaining the error, don't call logs.tail instead.\n"
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
        "❌ BAD: github.recent_commits errors → silently calls logs.tail, k8s.events, k8s.pod_context\n"
        "✅ GOOD: github.recent_commits errors → reply 'I couldn't fetch commits: <reason>. You may need to check GitHub config.'\n\n"
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
        "- Default to tool_calls: [] unless the user's question specifically needs live/fresh data.\n"
        "- Questions about 'what happened', 'summarize', 'status' → answer from CASE JSON, tool_calls: [].\n"
        "- Questions needing live data ('check current pod status', 'query CPU metrics now') → 1-2 tool calls.\n"
        "- If a tool errored in TOOL_HISTORY, report the error — don't compensate with unrelated tools.\n"
        "- Don't repeat a tool call whose `key` already appears in TOOL_HISTORY.\n"
        "- If the last outcome was `empty` or `unavailable`, don't retry with identical args.\n"
        "- Never output kubectl/aws/gh commands in your reply—use your tools instead!\n"
        "- Keep your warm, conversational tone.\n\n"
        f"CASE:\n{json.dumps(ctx, sort_keys=True, ensure_ascii=False)}\n\n"
        f"TOOL_HISTORY:\n{json.dumps(tool_hist, ensure_ascii=False)}\n\n"
        f"CHAT_HISTORY:\n{json.dumps(hist_compact, ensure_ascii=False)}\n\n"
        f"USER:\n{redact_text(user_message) if policy.redact_secrets else (user_message or '')}\n"
    )


def _run_chat_langgraph(
    *,
    policy: ChatPolicy,
    action_policy: Optional[ActionPolicy],
    analysis_json: Dict[str, Any],
    user_message: str,
    history: List[ChatMessage],
    case_id: Optional[str],
    run_id: Optional[str],
) -> ChatRunResult:
    """
    LangGraph-based chat loop (same semantics as the legacy loop).

    State is intentionally mapped 1:1 to existing shapes:
    - tool_events: List[ChatToolEvent]
    - updated_analysis: Optional[Dict[str, Any]]
    - analysis_json: Dict[str, Any] (SSOT slice)
    """
    from typing import TypedDict

    from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]

    class _State(TypedDict, total=False):
        analysis_json: dict[str, object]
        user_message: str
        history: list[ChatMessage]
        tool_events: list[ChatToolEvent]
        updated_analysis: dict[str, object] | None
        remaining_calls: int
        reply: str
        tool_calls: list[dict[str, object]]
        stop: bool
        all_tools_errored: bool  # set when all tool calls in a step failed

    def _fallback_reply(_analysis_json: Dict[str, Any], *, err: Optional[str]) -> str:
        a = _analysis_json.get("analysis") if isinstance(_analysis_json.get("analysis"), dict) else {}
        hyps = a.get("hypotheses") if isinstance(a.get("hypotheses"), list) else []
        lines = [
            "LLM chat is unavailable (provider not configured or provider error).",
            f"Reason: {str(err or '').strip() or 'unknown'}",
            "Here are the top likely causes from deterministic diagnostics:",
        ]
        for h in hyps[:3]:
            if isinstance(h, dict):
                lines.append(f"- {h.get('title') or h.get('hypothesis_id')}: {h.get('confidence_0_100')} / 100")
        return "\n".join([x for x in lines if x]).strip()

    # NOTE: Avoid annotating with the local `_State` type.
    # LangGraph may evaluate type hints via `typing.get_type_hints`, and local forward refs
    # can raise `NameError: name '_State' is not defined` in production.
    def llm_step(state):
        aj = state.get("analysis_json") or {}
        tool_events = state.get("tool_events") or []
        prompt = _build_prompt(
            policy=policy,
            action_policy=action_policy,
            analysis_json=aj,
            user_message=state.get("user_message") or "",
            history=state.get("history") or [],
            tool_events=tool_events,
        )
        obj, err = generate_json(prompt, schema=ToolPlanResponse)
        if err or not isinstance(obj, dict):
            return {
                **state,
                "reply": _fallback_reply(aj, err=err),
                "tool_calls": [],
                "stop": True,
            }
        reply = str(obj.get("reply") or "").strip()
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []
        return {**state, "reply": reply or "OK.", "tool_calls": tool_calls, "stop": False}

    def tool_step(state):
        aj = state.get("analysis_json") or {}
        tool_events = list(state.get("tool_events") or [])
        updated_analysis = state.get("updated_analysis")
        remaining_calls = int(state.get("remaining_calls") or 0)
        tool_calls = state.get("tool_calls") or []

        # Executor-side dedupe: never re-run identical tool+args within this invocation.
        seen_keys = set()
        for ev in tool_events:
            try:
                k0 = getattr(ev, "key", None) or tool_call_key(ev.tool, getattr(ev, "args", {}) or {})
                if k0:
                    seen_keys.add(str(k0))
            except Exception:
                continue

        ran_any = False
        events_before = len(tool_events)  # track where this step's events start
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
                    tool=tool,
                    args=args,
                    fn=lambda: run_tool(
                        policy=policy,
                        action_policy=action_policy,
                        tool=tool,
                        args=args,
                        analysis_json=aj,
                        case_id=case_id,
                        run_id=run_id,
                    ),
                )
            except Exception as e:
                # Catch any unhandled exceptions from tool execution
                logger.exception(f"Tool {tool} raised unhandled exception")
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
            if res.updated_analysis is not None:
                updated_analysis = res.updated_analysis
                try:
                    aj = dict(aj)
                    aj["analysis"] = res.updated_analysis
                except Exception:
                    pass

            # Fail-fast: if a tool errors, skip remaining tools in this step
            # and go back to the LLM so it can see the error before deciding
            # what to do next. This prevents the LLM from bundling unrelated
            # tools (e.g. github + logs) and silently continuing when one fails.
            if not res.ok:
                skipped = len(tool_calls) - (tool_calls.index(tc) + 1)
                if skipped > 0:
                    logger.info(f"Tool {tool} errored; skipping {skipped} remaining tool(s) in this step")
                break

        if not ran_any:
            return {
                **state,
                "reply": (state.get("reply") or "I couldn't run the requested tools. Please rephrase your request."),
                "tool_calls": [],
                "stop": True,
                "analysis_json": aj,
                "tool_events": tool_events,
                "updated_analysis": updated_analysis,
                "remaining_calls": remaining_calls,
            }

        # All-errors stop: if every tool call in this step errored, go back to
        # the LLM once to compose an error reply, but prevent further tool calls.
        # This stops the LLM from pivoting to unrelated tools when the relevant
        # tool fails (e.g. github fails → don't call logs, k8s, etc.).
        step_events = tool_events[events_before:]  # only events from this step
        all_errored = step_events and all(not ev.ok for ev in step_events)
        if all_errored:
            logger.info("All tool calls in this step errored; allowing one final LLM reply")
            return {
                **state,
                "tool_calls": [],
                "stop": False,  # go back to LLM for a final reply with error context
                "analysis_json": aj,
                "tool_events": tool_events,
                "updated_analysis": updated_analysis,
                "remaining_calls": remaining_calls,
                "all_tools_errored": True,
            }

        # Budget stop: mirror legacy behavior.
        if remaining_calls <= 0:
            return {
                **state,
                "reply": "I reached the tool-call limit for this chat turn. Please narrow the question or open the full report for details.",
                "tool_calls": [],
                "stop": True,
                "analysis_json": aj,
                "tool_events": tool_events,
                "updated_analysis": updated_analysis,
                "remaining_calls": remaining_calls,
            }

        # Continue the loop (don't stop yet)
        return {
            **state,
            "analysis_json": aj,
            "tool_events": tool_events,
            "updated_analysis": updated_analysis,
            "remaining_calls": remaining_calls,
            "stop": False,
        }

    def route_after_llm(state) -> str:
        if state.get("stop"):
            return "end"
        # If we're here because all tools errored, this is the final reply pass — stop.
        if state.get("all_tools_errored"):
            return "end"
        tool_calls = state.get("tool_calls") or []
        if not tool_calls:
            return "end"
        if int(state.get("remaining_calls") or 0) <= 0:
            return "end"
        return "tools"

    def route_after_tools(state) -> str:
        if state.get("stop"):
            return "end"
        # If all tools errored, go back to LLM for one final reply about the errors.
        if state.get("all_tools_errored"):
            return "llm"
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
        "analysis_json": analysis_json,
        "user_message": user_message,
        "history": history,
        "tool_events": [],
        "updated_analysis": None,
        "remaining_calls": int(policy.max_tool_calls),
        "reply": "",
        "tool_calls": [],
        "stop": False,
        "all_tools_errored": False,
    }
    meta = {
        "case_id": str(case_id or ""),
        "run_id": str(run_id or ""),
        "max_steps": int(policy.max_steps),
        "max_tool_calls": int(policy.max_tool_calls),
    }
    cfg = build_invoke_config(
        kind="case_chat", run_name=f"case_chat:{case_id or 'unknown'}:{run_id or 'latest'}", metadata=meta
    )
    cfg["recursion_limit"] = int(policy.max_steps) + 5
    out = app.invoke(init, config=cfg)
    return ChatRunResult(
        reply=str(out.get("reply") or "").strip() or "OK.",
        tool_events=list(out.get("tool_events") or []),
        updated_analysis=out.get("updated_analysis"),
    )


def run_chat(
    *,
    policy: ChatPolicy,
    action_policy: Optional[ActionPolicy] = None,
    analysis_json: Dict[str, Any],
    user_message: str,
    history: List[ChatMessage],
    case_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> ChatRunResult:
    """
    Tool-using chat loop (best-effort).

    Notes:
    - If LLM is not configured, return a deterministic fallback reply.
    - Tool execution is policy-gated.
    """
    if not policy.enabled:
        return ChatRunResult(reply="Chat is disabled by policy.", tool_events=[])

    # Fast-path deterministic answers for common intents (avoid LLM truncation / cost).
    try:
        ir = try_handle_case_intents(analysis_json=analysis_json, user_message=user_message)
        if ir.handled:
            return ChatRunResult(reply=ir.reply, tool_events=ir.tool_events, updated_analysis=None)
    except Exception:
        # If fast-path fails, fall back to the normal LLM/tool loop.
        pass

    # LangGraph-based loop (primary).
    return _run_chat_langgraph(
        policy=policy,
        action_policy=action_policy,
        analysis_json=analysis_json,
        user_message=user_message,
        history=history,
        case_id=case_id,
        run_id=run_id,
    )

    # If no model key, return deterministic fallback (still useful for on-call).
    # We use the gemini wrapper error to detect unavailability.
    tool_events: List[ChatToolEvent] = []
    updated_analysis: Optional[Dict[str, Any]] = None

    remaining_calls = int(policy.max_tool_calls)

    for _step in range(int(policy.max_steps)):
        prompt = _build_prompt(
            policy=policy,
            action_policy=action_policy,
            analysis_json=analysis_json,
            user_message=user_message,
            history=history,
            tool_events=tool_events,
        )
        obj, err = generate_json(prompt, schema=ToolPlanResponse)
        if err or not isinstance(obj, dict):
            # Deterministic fallback: show top hypotheses and next tests.
            a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
            hyps = a.get("hypotheses") if isinstance(a.get("hypotheses"), list) else []
            lines = [
                "LLM chat is unavailable (Vertex AI not configured or provider error).",
                "Here are the top likely causes from deterministic diagnostics:",
            ]
            # Make the failure actionable without leaking sensitive details.
            # `generate_json` returns stable, non-sensitive error codes.
            code = str(err or "").strip() or "unknown"
            hint = None
            if code == "missing_api_key":
                hint = "API key not configured. Set ANTHROPIC_API_KEY for Anthropic provider."
            elif code == "missing_gcp_project":
                hint = "Set GOOGLE_CLOUD_PROJECT for Vertex AI."
            elif code == "missing_gcp_location":
                hint = "Set GOOGLE_CLOUD_LOCATION for Vertex AI."
            elif code == "missing_adc_credentials":
                hint = "ADC credentials missing. Configure Workload Identity / ADC."
            elif code == "unauthenticated":
                hint = "LLM credentials rejected. Check API key or ADC setup."
            elif code == "permission_denied":
                hint = "LLM credentials lack permission. Check project access."
            elif code.startswith("sdk_import_failed:"):
                sdk = code.split(":", 1)[-1]
                hint = f"LLM SDK not installed. Install: poetry install -E {sdk.replace('langchain-', '').replace('langchain_', '').replace('google-vertexai', 'vertex')}"
            elif code.startswith("model_not_found:"):
                hint = "Configured model not available. Check LLM_MODEL setting."
            elif code == "provider_not_configured":
                hint = "LLM provider not configured. Set LLM_PROVIDER to 'vertexai' or 'anthropic'."

            lines.insert(1, f"Reason: {code}")
            if hint:
                lines.insert(2, f"Fix: {hint}")
            for h in hyps[:3]:
                if isinstance(h, dict):
                    lines.append(f"- {h.get('title') or h.get('hypothesis_id')}: {h.get('confidence_0_100')} / 100")
            return ChatRunResult(reply="\n".join(lines).strip(), tool_events=tool_events)

        reply = str(obj.get("reply") or "").strip()
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []

        # If no tool calls, we're done.
        if not tool_calls:
            return ChatRunResult(reply=reply or "OK.", tool_events=tool_events, updated_analysis=updated_analysis)

        # Execute tools (bounded).
        ran_any = False
        for tc in tool_calls:
            if remaining_calls <= 0:
                break
            if not isinstance(tc, dict):
                continue
            tool = str(tc.get("tool") or "").strip()
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            res = run_tool(
                policy=policy,
                action_policy=action_policy,
                tool=tool,
                args=args,
                analysis_json=analysis_json,
                case_id=case_id,
                run_id=run_id,
            )
            ev = ChatToolEvent(tool=tool, args=args, ok=bool(res.ok), result=res.result, error=res.error)
            tool_events.append(ev)
            remaining_calls -= 1
            ran_any = True
            if res.updated_analysis is not None:
                updated_analysis = res.updated_analysis
                # If rerun happened, update context for subsequent steps.
                try:
                    analysis_json = dict(analysis_json)
                    analysis_json["analysis"] = res.updated_analysis
                except Exception:
                    pass

        if not ran_any:
            # Model requested invalid tools; stop.
            return ChatRunResult(
                reply=(reply or "I couldn't run the requested tools. Please rephrase your request."),
                tool_events=tool_events,
                updated_analysis=updated_analysis,
            )

        # Continue loop with tool results appended in tool history.
        # (Tool results are already included via tool_events.)
        continue

    # Max steps reached
    return ChatRunResult(
        reply="I reached the tool-call limit for this chat turn. Please narrow the question or open the full report for details.",
        tool_events=tool_events,
        updated_analysis=updated_analysis,
    )
