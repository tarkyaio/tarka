from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from typing import Any, Dict, List, TypedDict

from agent.authz.policy import ChatPolicy, load_chat_policy
from agent.chat.tool_summaries import compact_args_for_prompt, summarize_tool_result, tool_call_key
from agent.chat.tools import run_tool
from agent.chat.types import ChatToolEvent
from agent.core.models import Investigation, RCAInsights
from agent.dump import investigation_to_json_dict
from agent.graphs.tracing import build_invoke_config, trace_tool_call
from agent.llm.client import generate_json
from agent.llm.schemas import RCASynthesisResponse, ToolPlanResponse
from agent.pipeline.pipeline import run_investigation

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _format_tool_list(tools: List[str]) -> str:
    """Format tool list with descriptions for LLM prompt."""
    # Import here to avoid circular dependency
    try:
        from agent.chat.runtime import TOOL_DESCRIPTIONS
    except ImportError:
        # Fallback to simple list if import fails
        return "\n".join([f"- {t}" for t in tools])

    return "\n".join([f"- {t}: {TOOL_DESCRIPTIONS.get(t, 'No description')}" for t in tools])


def _allowed_tools(policy: ChatPolicy) -> List[str]:
    # Mirror `agent.chat.runtime._allowed_tools` (case tools only; no actions in worker RCA).
    tools: List[str] = []
    if policy.allow_promql:
        tools.append("promql.instant")
    if policy.allow_k8s_read:
        tools.extend(["k8s.pod_context", "k8s.rollout_status"])
    if policy.allow_logs_query:
        tools.append("logs.tail")
    if policy.allow_memory_read:
        tools.extend(["memory.similar_cases", "memory.skills"])
    # Allow reruns to expand/adjust time window during RCA.
    if policy.allow_report_rerun:
        tools.append("rerun.investigation")
    if policy.allow_argocd_read:
        tools.append("argocd.app_status")
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
                "aws.s3_bucket_location",
                "aws.iam_role_permissions",
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
    return tools


def _need_more_evidence(
    *, analysis_json: Dict[str, Any], tool_events: List[ChatToolEvent], confidence_threshold: int = 70
) -> bool:
    a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
    feats = a.get("features") if isinstance(a.get("features"), dict) else {}
    quality = feats.get("quality") if isinstance(feats.get("quality"), dict) else {}

    evidence_quality = str(quality.get("evidence_quality") or "").strip().lower()
    missing_inputs = quality.get("missing_inputs") if isinstance(quality.get("missing_inputs"), list) else []
    contradictions = quality.get("contradiction_flags") if isinstance(quality.get("contradiction_flags"), list) else []

    hyps = a.get("hypotheses") if isinstance(a.get("hypotheses"), list) else []
    top_conf = 0
    if hyps and isinstance(hyps[0], dict):
        try:
            top_conf = int(hyps[0].get("confidence_0_100") or 0)
        except Exception:
            top_conf = 0

    if evidence_quality == "low":
        return True
    if missing_inputs:
        return True
    if contradictions:
        return True
    if not hyps:
        return True
    if top_conf < int(confidence_threshold):
        return True

    # CRITICAL: Check if high-confidence hypothesis (>80%) has been verified with tools
    # Pattern matches identify proximate causes; verification tools find root causes
    if top_conf >= 80:
        # Get the top hypothesis to determine what kind of verification is needed
        top_hyp = hyps[0] if hyps and isinstance(hyps[0], dict) else {}
        hyp_label = str(top_hyp.get("label") or "").lower()
        hyp_refs = top_hyp.get("supporting_refs") if isinstance(top_hyp.get("supporting_refs"), list) else []
        hyp_refs_str = " ".join(str(r) for r in hyp_refs).lower()

        # Determine which verification tools are relevant for this hypothesis
        # Some hypotheses require MULTIPLE tools for complete verification
        require_multiple_tools = False

        # S3-related hypotheses require BOTH bucket location AND IAM permissions
        if "s3" in hyp_label or "s3" in hyp_refs_str or "bucket" in hyp_label:
            relevant_verification_tools = {"aws.s3_bucket_location", "aws.iam_role_permissions"}
            require_multiple_tools = (
                True  # Need both to determine if it's region mismatch vs cross-account vs permissions
            )
        # Database-related hypotheses
        elif "rds" in hyp_label or "database" in hyp_label or "db" in hyp_label:
            relevant_verification_tools = {"aws.rds_status", "aws.iam_role_permissions"}
            require_multiple_tools = True  # Need both to determine if it's DB status vs permissions
        # Image-related hypotheses
        elif "image" in hyp_label or "ecr" in hyp_label or "pull" in hyp_label:
            relevant_verification_tools = {"aws.ecr_image", "aws.iam_role_permissions"}
            require_multiple_tools = True  # Need both to determine if it's image issue vs permissions
        # Network-related hypotheses
        elif "network" in hyp_label or "connectivity" in hyp_label or "nat" in hyp_label or "vpc" in hyp_label:
            relevant_verification_tools = {"aws.nat_gateway", "aws.vpc_endpoint", "aws.security_group"}
            require_multiple_tools = False  # Any one network tool is usually sufficient
        # Pod/K8s-related hypotheses
        elif "pod" in hyp_label or "container" in hyp_label or "k8s" in hyp_label:
            relevant_verification_tools = {"k8s.pod_context", "k8s.rollout_status"}
            require_multiple_tools = False  # Either pod context or rollout status is sufficient
        else:
            # Generic: any verification tool counts
            relevant_verification_tools = {
                "aws.s3_bucket_location",
                "aws.iam_role_permissions",
                "aws.ec2_status",
                "aws.ebs_health",
                "aws.rds_status",
                "aws.ecr_image",
                "aws.security_group",
                "aws.nat_gateway",
                "aws.vpc_endpoint",
                "k8s.pod_context",
                "k8s.rollout_status",
            }
            require_multiple_tools = False

        # Check if we've used relevant verification tools successfully
        verified_tools = set()
        for ev in tool_events or []:
            tool = getattr(ev, "tool", None)
            ok = getattr(ev, "ok", False)
            outcome = getattr(ev, "outcome", None)
            # Count as verified if relevant tool succeeded (ok=True and outcome not empty/unavailable/error)
            if tool in relevant_verification_tools and ok and outcome not in ("empty", "unavailable", "error"):
                verified_tools.add(tool)

        # Determine if verification is complete
        if require_multiple_tools:
            # Prefer 2 tools for complete verification, but 1 tool is acceptable if:
            # - We have at least one verification tool, AND
            # - Confidence is very high (>= 95%), suggesting the single tool gave a definitive answer
            if len(verified_tools) >= 2:
                verified = True  # Both tools called - fully verified
            elif len(verified_tools) == 1 and top_conf >= 95:
                verified = True  # One tool but very high confidence - likely definitive (e.g., bucket doesn't exist)
            else:
                verified = False  # Need more evidence
        else:
            # Any one relevant tool is sufficient
            verified = len(verified_tools) >= 1

        # If high confidence but NOT verified with relevant tools, continue investigating
        if not verified:
            return True

    return False


def _get_family_specific_guidance(family: str) -> str:
    """Return family-specific verification examples and guidance for RCA prompts."""

    if family == "job_failed":
        return """
Examples of verification for Job failures:
  * IAM errors: Check role permissions with aws.iam_role_permissions (use service_account+namespace args, NOT role_name)
  * S3 errors: Check bucket location/region with aws.s3_bucket_location
  * ECR errors: Verify image existence and repository permissions
  * DB errors: Verify endpoint reachability with aws.rds_status

CRITICAL - IRSA Configuration:
- For AWS access errors (S3, ECR, RDS), ALWAYS check if IRSA is configured first
- Use aws.iam_role_permissions with service_account+namespace (NOT role_name)
- If tool returns 'no_iam_role_annotation', the root cause is: "Service account lacks IAM role annotation (IRSA not configured)"
- Do NOT infer role names (like appending "-role" to service account name)
- Only after confirming IRSA is configured should you check specific permissions

Interpreting Permission Boundaries (for AWS-related failures):
- When verification tools return 'agent_lacks_permission' or 'AccessDenied':
  * This is VALUABLE diagnostic information, not a verification failure
  * Common scenarios:
    - S3 bucket in different AWS account (cross-account access not configured)
    - S3 bucket has restrictive bucket policy denying access
    - RDS instance in different AWS account or VPC peering not configured
    - ECR repository in different account or resource policy restrictions
  * How to use this evidence:
    - If job's IAM role HAS the required permissions (e.g., s3:GetBucketLocation)
      but agent gets AccessDenied when checking the bucket itself,
      this strongly suggests cross-account bucket or restrictive bucket policy
    - Include this in root cause: 'Bucket likely in different AWS account or has
      bucket policy restricting access - job's IAM role has s3:* permissions but
      cannot access this specific bucket'
    - Recommend checking bucket ownership, bucket policy, and cross-account setup
  * Do NOT treat permission boundaries as verification failures - they provide clues!
"""

    elif family == "cpu_throttling":
        return """
Examples of verification for CPU throttling:
  * Check CPU limits vs requests in container spec using K8s tools
  * Query throttling metrics over time to see pattern (spikes vs sustained)
  * Check if other containers on same node are also throttled (node-level issue)
  * Verify if throttling correlates with traffic spikes or batch jobs
"""

    elif family == "oom_killed":
        return """
Examples of verification for OOM kills:
  * Check memory limits vs actual usage patterns over time
  * Look for memory leak indicators (growing usage trend in metrics)
  * Check if memory spike correlates with specific operations (from logs)
  * Verify heap dumps or memory profiles if available
  * Check if issue is pod-level or node-level (eviction vs OOMKilled)
"""

    elif family == "http_5xx":
        return """
Examples of verification for HTTP 5xx errors:
  * Check upstream dependency health and response times using metrics
  * Verify rate limiting or connection pool exhaustion in logs
  * Check for database connection errors or slow queries
  * Look for correlation with deployments or traffic spikes in change history
  * Verify if error rate matches traffic pattern or is flat
"""

    elif family == "pod_not_healthy":
        return """
Examples of verification for pod health issues:
  * Check readiness/liveness probe configurations using K8s tools
  * Verify pod conditions (Ready, ContainersReady, PodScheduled)
  * Look for recent pod events (FailedScheduling, Unhealthy, BackOff)
  * Check if issue is with init containers vs main containers
  * Verify if pod has been restarted recently or is stuck
"""

    elif family == "crashloop":
        return """
Examples of verification for crash loops:
  * Check container exit code and termination reason
  * Look for panic/crash patterns in recent logs
  * Verify if crash happens immediately or after running for some time
  * Check if recent config changes or deployments triggered the issue
  * Look for resource exhaustion (memory, disk, file descriptors)
"""

    elif family == "memory_pressure":
        return """
Examples of verification for memory pressure:
  * Check memory usage trends across pods/nodes
  * Verify if pressure is at pod level (limits) or node level (capacity)
  * Look for memory-intensive operations in logs
  * Check if pressure correlates with traffic patterns
  * Verify if evictions have occurred
"""

    elif family == "target_down":
        return """
Examples of verification for target down issues:
  * Check if target is a pod (check pod status/events) or node (check node status)
  * Verify network connectivity and DNS resolution
  * Look for recent changes (deployments, scaling events)
  * Check if monitoring endpoint is accessible
"""

    elif family == "k8s_rollout_health":
        return """
Examples of verification for rollout health issues:
  * Check rollout status and history using K8s tools
  * Look for pod failures during rollout
  * Verify if new version has issues (image pull, crashes, readiness failures)
  * Check if rollout is stuck or progressing slowly
"""

    else:
        # Generic guidance for other families (observability_pipeline, meta, etc.)
        return """
Examples of verification (adapt to your specific alert):
  * Use metrics tools to check resource usage patterns and trends
  * Use K8s tools to check pod/workload status, conditions, and events
  * Use log tools to find error patterns and their context
  * Check if issue correlates with recent changes or deployments
"""


def _build_planner_prompt(
    *, analysis_json: Dict[str, Any], tool_events: List[ChatToolEvent], allowed_tools: List[str]
) -> str:
    # Compact case context; keep aligned with chat runtime's "SSOT-only" discipline.
    ctx: Dict[str, Any] = {}
    family = None  # Extract family for dynamic prompt guidance
    try:
        ctx["target"] = analysis_json.get("target")
        a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
        ctx["verdict"] = a.get("verdict")
        ctx["scores"] = a.get("scores")
        ctx["features"] = a.get("features")
        ctx["hypotheses"] = a.get("hypotheses") or []
        ctx["change"] = a.get("change")
        ctx["noise"] = a.get("noise")
        # RCA so far (if any)
        ctx["rca"] = a.get("rca")

        # Extract family for dynamic verification guidance
        verdict = ctx.get("verdict") or {}
        if isinstance(verdict, dict):
            family = verdict.get("family")
    except Exception:
        ctx = {}

    tool_hist = []
    for ev in (tool_events or [])[-8:]:
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

    # Get family-specific verification guidance
    family_guidance = _get_family_specific_guidance(family or "generic")

    return (
        "You are Tarka, an on-call incident investigation agent.\n\n"
        "Goal:\n"
        "- Reduce uncertainty and converge on a real-world root cause and remediation.\n\n"
        "Tool Usage (be proactive and autonomous):\n"
        "- You're autonomous—use your tools immediately to gather evidence.\n"
        "- Don't defer investigation—if you need metrics/logs/K8s data, use your tools.\n"
        "- Think: What evidence would help narrow down the root cause?\n"
        "- Prefer the smallest set of tool calls that is most likely to reduce uncertainty.\n"
        "- Converge through tool use, not speculation.\n\n"
        "Hypothesis Verification (CRITICAL):\n"
        "- Even if a hypothesis has high confidence (>80%), you MUST verify it using tools.\n"
        "- Pattern matches identify PROXIMATE causes (e.g., error message in logs).\n"
        "- Your job is to find ROOT causes (e.g., configuration issue, resource limit).\n"
        f"{family_guidance}\n"
        "- Use 1-2 tools to either:\n"
        "  * CONFIRM the hypothesis (strengthen confidence to 95%+)\n"
        "  * REFINE the hypothesis (discover the deeper issue)\n"
        "  * RULE OUT alternatives (eliminate competing explanations)\n\n"
        "Hard constraints (must follow):\n"
        "- Use ONLY the provided CASE JSON + TOOL RESULTS.\n"
        "- Do NOT invent logs/metrics/events or root causes.\n"
        "- If you need more evidence, request tool calls.\n"
        "- Prefer the smallest set of tool calls that is most likely to reduce uncertainty.\n"
        "- Return ONLY valid JSON. No markdown. No code fences.\n\n"
        "Available tools (call only these):\n"
        f"{_format_tool_list(allowed_tools)}\n\n"
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
        "- Be proactive: Use your tools to investigate and gather evidence.\n"
        "- If no more evidence is needed, set tool_calls to [].\n"
        "- Otherwise, request 1-3 tool calls.\n"
        "- Don't repeat a tool call whose `key` already appears in TOOL_HISTORY.\n"
        "- If the last outcome was `empty` or `unavailable`, don't retry with identical args.\n"
        "- Converge through tool use—gather evidence to confirm or refute hypotheses.\n\n"
        f"CASE:\n{json.dumps(ctx, sort_keys=True, ensure_ascii=False)}\n\n"
        f"TOOL_HISTORY:\n{json.dumps(tool_hist, ensure_ascii=False)}\n"
    )


def _build_rca_prompt(*, analysis_json: Dict[str, Any], tool_events: List[ChatToolEvent]) -> str:
    # Provide both SSOT + explicit tool results for grounding.
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

        # Include parsed log errors for specific root cause identification
        evidence = analysis_json.get("evidence") if isinstance(analysis_json.get("evidence"), dict) else {}
        logs = evidence.get("logs") if isinstance(evidence.get("logs"), dict) else {}
        parsed_errors = logs.get("parsed_errors") if isinstance(logs.get("parsed_errors"), list) else []
        if parsed_errors:
            # Include up to 10 most relevant parsed errors
            ctx["parsed_errors"] = parsed_errors[:10]
    except Exception:
        ctx = {}

    tools_compact = []
    for ev in (tool_events or [])[-8:]:
        tools_compact.append({"tool": ev.tool, "ok": ev.ok, "error": ev.error, "result": ev.result})

    # Build task instructions with emphasis on parsed_errors if available
    has_parsed_errors = bool(ctx.get("parsed_errors"))
    parsed_errors_instruction = ""
    if has_parsed_errors:
        parsed_errors_instruction = (
            "\n**IMPORTANT - Parsed Log Errors Available:**\n"
            "- The CASE includes 'parsed_errors' with specific error patterns from logs\n"
            "- Identify the SPECIFIC root cause from these errors (e.g., 'S3 access denied due to missing IAM role permissions', NOT just 'Job failed')\n"
            "- Provide ACTIONABLE remediation using the right tools (AWS CLI for cloud issues, kubectl for K8s issues, etc.)\n"
            "- Ground your root_cause in the actual error messages\n"
        )

    return (
        "You are Tarka, an on-call incident investigation agent.\n\n"
        "Task:\n"
        "- Produce a grounded root-cause analysis and concrete remediation suggestions.\n"
        f"{parsed_errors_instruction}\n"
        "Hard constraints (must follow):\n"
        "- Use ONLY CASE + TOOL_RESULTS.\n"
        "- Do NOT invent logs/metrics/events or root causes.\n"
        "- If a key fact is missing, list it in unknowns.\n"
        "- Cite evidence keys in your evidence bullets (e.g., features.k8s.waiting_reason, parsed_errors[0].message).\n"
        "- Return ONLY valid JSON. No markdown. No code fences.\n\n"
        "Output JSON schema (exact keys):\n"
        "{\n"
        '  "schema_version": "tarka.rca.v1",\n'
        '  "status": "ok"|"unknown"|"blocked",\n'
        '  "summary": string,\n'
        '  "root_cause": string,\n'
        '  "confidence_0_1": number,\n'
        '  "evidence": [string],\n'
        '  "remediation": [string],\n'
        '  "unknowns": [string],\n'
        '  "meta": { "notes": [string] } | null\n'
        "}\n"
        "Status field semantics:\n"
        '- "ok": RCA analysis completed (use this whenever you can provide a summary and root_cause, even if confidence is low or unknowns remain)\n'
        '- "blocked": cannot produce any analysis because critical evidence is entirely missing (e.g., no logs, no K8s context, no metrics at all)\n'
        '- "unknown": reserved for cases where you truly cannot determine anything — prefer "ok" with low confidence over "unknown"\n'
        "Output constraints:\n"
        "- Keep `summary` and `root_cause` short (<= 240 chars each).\n"
        "- Cap arrays: evidence<=8, remediation<=10, unknowns<=8.\n"
        "- Do NOT include any extra top-level keys.\n\n"
        f"CASE:\n{json.dumps(ctx, sort_keys=True, ensure_ascii=False)}\n\n"
        f"TOOL_RESULTS:\n{json.dumps(tools_compact, ensure_ascii=False)}\n"
    )


class _State(TypedDict, total=False):
    alert: Dict[str, Any]
    time_window: str
    investigation: Investigation
    analysis_json: Dict[str, Any]
    policy: ChatPolicy
    allowed_tools: List[str]
    tool_events: List[ChatToolEvent]
    planned_tool_calls: List[Dict[str, Any]]
    remaining_steps: int
    remaining_tool_calls: int
    # Used to prevent planner/tool spin when no new evidence is possible.
    last_tools_new_keys: int
    last_tools_outcomes: List[str]
    stop: bool
    rca_obj: Dict[str, Any]
    errors: List[str]


def _safe_load_policy() -> ChatPolicy:
    try:
        policy = load_chat_policy()

        # Auto-enable AWS tools for RCA when AWS evidence collection is enabled.
        # Rationale: If we're collecting AWS evidence in the pipeline, RCA should be able
        # to verify it with AWS tools. This reduces configuration complexity.
        # Chat still respects CHAT_ALLOW_AWS_READ separately for security control.
        if _env_bool("AWS_EVIDENCE_ENABLED", False) and not policy.allow_aws_read:
            policy = replace(policy, allow_aws_read=True)

        return policy
    except Exception:
        return ChatPolicy(enabled=False)


def _compile_rca_graph(*, default_policy: ChatPolicy):
    """
    Build a LangGraph graph suitable for:
    - LangGraph Studio (`langgraph dev`) visualization/testing
    - production invocation via `maybe_attach_rca` (best-effort)

    Returns a compiled runnable graph.
    """
    from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]

    def baseline_node(state: _State) -> _State:
        pol = state.get("policy") or default_policy
        inv = state.get("investigation")
        if inv is None:
            inv = run_investigation(alert=state.get("alert") or {}, time_window=state.get("time_window") or "1h")
        aj = state.get("analysis_json") or investigation_to_json_dict(inv, mode="analysis")
        allowed = list(state.get("allowed_tools") or []) or _allowed_tools(pol)
        return {
            **state,
            "policy": pol,
            "allowed_tools": allowed,
            "investigation": inv,
            "analysis_json": aj,
        }

    def decide_node(state: _State) -> _State:
        aj0 = state.get("analysis_json") or {}
        tool_events = list(state.get("tool_events") or [])
        more = _need_more_evidence(analysis_json=aj0, tool_events=tool_events)

        # Spin guard: if the last tools round made no progress (all duplicates) or
        # only produced empty/unavailable/error outcomes, stop and synthesize.
        try:
            new_keys = int(state.get("last_tools_new_keys") or -1)
        except Exception:
            new_keys = -1
        if new_keys == 0:
            more = False
        outs = state.get("last_tools_outcomes")
        if isinstance(outs, list) and outs:
            if all(
                str(x or "").strip().lower() in ("empty", "unavailable", "error", "skipped_duplicate") for x in outs
            ):
                more = False

        if int(state.get("remaining_steps") or 0) <= 0:
            more = False
        if int(state.get("remaining_tool_calls") or 0) <= 0:
            more = False
        return {**state, "stop": (not more)}

    def plan_node(state: _State) -> _State:
        aj0 = state.get("analysis_json") or {}
        prompt = _build_planner_prompt(
            analysis_json=aj0,
            tool_events=list(state.get("tool_events") or []),
            allowed_tools=list(state.get("allowed_tools") or []),
        )
        obj, err = generate_json(prompt, schema=ToolPlanResponse)
        if err or not isinstance(obj, dict):
            errs = list(state.get("errors") or [])
            errs.append(str(err or "planner_error"))
            logger.warning(
                "RCA planner failed: error=%s obj_type=%s",
                err or "unknown",
                type(obj).__name__ if obj is not None else "None",
            )
            return {**state, "stop": True, "errors": errs, "planned_tool_calls": []}
        tool_calls = obj.get("tool_calls") if isinstance(obj.get("tool_calls"), list) else []
        planned: List[Dict[str, Any]] = [tc for tc in tool_calls if isinstance(tc, dict)]
        return {**state, "planned_tool_calls": planned}

    def tools_node(state: _State) -> _State:
        aj0 = state.get("analysis_json") or {}
        inv = state.get("investigation")
        tool_events = list(state.get("tool_events") or [])
        remaining = int(state.get("remaining_tool_calls") or 0)
        planned = list(state.get("planned_tool_calls") or [])
        pol = state.get("policy") or default_policy

        # Executor-side dedupe: never re-run identical tool+args within this RCA invocation.
        seen_keys = set()
        for ev in tool_events:
            try:
                k0 = getattr(ev, "key", None) or tool_call_key(ev.tool, getattr(ev, "args", {}) or {})
                if k0:
                    seen_keys.add(str(k0))
            except Exception:
                continue

        new_unique = 0
        round_outcomes: List[str] = []

        for tc in planned[:3]:
            if remaining <= 0:
                break
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
                round_outcomes.append("skipped_duplicate")
                remaining -= 1
                continue
            seen_keys.add(k_req)
            new_unique += 1

            # Special-case: rerun should update SSOT investigation, not only analysis_json.
            if tool == "rerun.investigation":
                tw = str(args.get("time_window") or "").strip()
                if tw:
                    try:
                        inv2 = trace_tool_call(
                            tool=tool,
                            args=args,
                            fn=lambda: run_investigation(alert=state.get("alert") or {}, time_window=tw),
                        )
                        inv = inv2
                        aj0 = investigation_to_json_dict(inv2, mode="analysis")
                        res_obj = {"status": "ok", "time_window": tw}
                        k = k_req
                        outcome, summary = summarize_tool_result(tool=tool, ok=True, error=None, result=res_obj)
                        round_outcomes.append(outcome)
                        tool_events.append(
                            ChatToolEvent(
                                tool=tool,
                                args=args,
                                ok=True,
                                result=res_obj,
                                error=None,
                                key=k,
                                outcome=outcome,
                                summary=summary,
                            )
                        )
                    except Exception as e:
                        err = f"rerun_error:{type(e).__name__}"
                        k = k_req
                        outcome, summary = summarize_tool_result(tool=tool, ok=False, error=err, result=None)
                        round_outcomes.append(outcome)
                        tool_events.append(
                            ChatToolEvent(
                                tool=tool,
                                args=args,
                                ok=False,
                                result=None,
                                error=err,
                                key=k,
                                outcome=outcome,
                                summary=summary,
                            )
                        )
                else:
                    err = "time_window_required"
                    k = k_req
                    outcome, summary = summarize_tool_result(tool=tool, ok=False, error=err, result=None)
                    round_outcomes.append(outcome)
                    tool_events.append(
                        ChatToolEvent(
                            tool=tool,
                            args=args,
                            ok=False,
                            result=None,
                            error=err,
                            key=k,
                            outcome=outcome,
                            summary=summary,
                        )
                    )
                remaining -= 1
                continue

            res = trace_tool_call(
                tool=tool,
                args=args,
                fn=lambda: run_tool(
                    policy=pol,
                    action_policy=None,
                    tool=tool,
                    args=args,
                    analysis_json=aj0,
                    case_id=None,
                    run_id=None,
                    caller_logger=logger,
                ),
            )
            k = k_req
            outcome, summary = summarize_tool_result(tool=tool, ok=bool(res.ok), error=res.error, result=res.result)
            round_outcomes.append(outcome)
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
            remaining -= 1

        # Decrement steps after a tools round.
        steps = int(state.get("remaining_steps") or 0)
        steps = max(0, steps - 1)
        return {
            **state,
            "policy": pol,
            "investigation": inv or state.get("investigation"),
            "analysis_json": aj0,
            "tool_events": tool_events,
            "remaining_tool_calls": remaining,
            "remaining_steps": steps,
            "planned_tool_calls": [],
            "last_tools_new_keys": new_unique,
            "last_tools_outcomes": round_outcomes,
        }

    def synth_node(state: _State) -> _State:
        aj0 = state.get("analysis_json") or {}
        prompt = _build_rca_prompt(analysis_json=aj0, tool_events=list(state.get("tool_events") or []))
        obj, err = generate_json(prompt, schema=RCASynthesisResponse)
        if err or not isinstance(obj, dict):
            errs = list(state.get("errors") or [])
            errs.append(str(err or "rca_error"))
            logger.warning(
                "RCA synthesis failed: error=%s obj_type=%s tool_events_count=%d",
                err or "unknown",
                type(obj).__name__ if obj is not None else "None",
                len(state.get("tool_events") or []),
            )
            return {
                **state,
                "rca_obj": {"status": "unavailable", "summary": f"RCA synthesis unavailable: {err or 'unknown_error'}"},
                "errors": errs,
            }
        return {**state, "rca_obj": obj}

    def route_after_decide(state: _State) -> str:
        return "synth" if state.get("stop") else "plan"

    def route_after_plan(state: _State) -> str:
        planned = list(state.get("planned_tool_calls") or [])
        if not planned:
            return "synth"
        if int(state.get("remaining_tool_calls") or 0) <= 0:
            return "synth"
        return "tools"

    def route_after_tools(state: _State) -> str:
        return "decide"

    g = StateGraph(_State)
    g.add_node("baseline", baseline_node)
    g.add_node("decide", decide_node)
    g.add_node("plan", plan_node)
    g.add_node("tools", tools_node)
    g.add_node("synth", synth_node)

    g.set_entry_point("baseline")
    g.add_edge("baseline", "decide")
    g.add_conditional_edges("decide", route_after_decide, {"plan": "plan", "synth": "synth"})
    g.add_conditional_edges("plan", route_after_plan, {"tools": "tools", "synth": "synth"})
    g.add_conditional_edges("tools", route_after_tools, {"decide": "decide"})
    g.add_edge("synth", END)
    return g.compile()


def maybe_attach_rca(
    *,
    alert: Dict[str, Any],
    time_window: str,
    investigation: Investigation,
    parent_callbacks: List[Any] | None = None,
) -> None:
    """
    Best-effort LangGraph RCA enrichment.

    Mutates `investigation.analysis.rca` and may attach tool traces under `investigation.meta`.
    """
    # Load a policy for tool access. This reuses the same env-driven gating as chat.
    policy = _safe_load_policy()

    # For RCA runs we allow tools regardless of CHAT_ENABLED, but still respect the per-tool allow flags.
    allowed_tools = _allowed_tools(policy)

    # If tools are entirely disabled, we can still synthesize RCA from SSOT only.
    aj = investigation_to_json_dict(investigation, mode="analysis")

    try:
        app = _compile_rca_graph(default_policy=policy)
    except Exception:
        # LangGraph not installed; do not fail investigations.
        investigation.analysis.rca = RCAInsights(
            status="unavailable", summary="RCA graph unavailable (langgraph not installed)."
        )
        return

    init: _State = {
        "alert": alert,
        "time_window": time_window,
        "investigation": investigation,
        "analysis_json": aj,
        "policy": policy,
        "allowed_tools": allowed_tools,
        "tool_events": [],
        "remaining_steps": int(getattr(policy, "max_steps", 4) or 4),
        "remaining_tool_calls": int(getattr(policy, "max_tool_calls", 6) or 6),
        "stop": False,
        "errors": [],
    }

    labels = alert.get("labels", {}) if isinstance(alert, dict) else {}
    alertname = (labels.get("alertname") or "Unknown") if isinstance(labels, dict) else "Unknown"
    fp = str(alert.get("fingerprint") or "") if isinstance(alert, dict) else ""
    meta = {"alertname": str(alertname), "fingerprint": fp[:12], "time_window": str(time_window)}
    cfg = build_invoke_config(kind="rca", run_name=f"rca:{alertname}:{fp[:8] or 'nofp'}", metadata=meta)
    # If we were invoked inside a traced parent (e.g., webhook alert processing),
    # reuse its callbacks so the RCA graph becomes a child run.
    if parent_callbacks:
        cfg["callbacks"] = parent_callbacks
    cfg["recursion_limit"] = 64
    out = app.invoke(init, config=cfg)
    tool_events_out = list(out.get("tool_events") or [])
    rca_obj = out.get("rca_obj") if isinstance(out.get("rca_obj"), dict) else {}

    # Attach tool trace (best-effort) for debugging/auditability.
    try:
        investigation.meta = dict(investigation.meta or {})
        investigation.meta["rca_tool_events"] = [ev.model_dump(mode="json") for ev in tool_events_out]
    except Exception:
        pass

    # Best-effort parse into RCAInsights.
    try:
        status = str(rca_obj.get("status") or "unknown").strip().lower()
        if status not in ("ok", "unknown", "blocked", "unavailable", "error"):
            status = "unknown"
        # Auto-promote "unknown" → "ok" when the synthesis produced substantive content.
        # LLMs sometimes return "unknown" to signal uncertainty in the root cause, but the
        # system treats "ok" as "analysis completed" (confidence conveys certainty).
        if status == "unknown" and rca_obj.get("summary") and rca_obj.get("root_cause"):
            status = "ok"
        investigation.analysis.rca = RCAInsights(
            status=status,  # type: ignore[arg-type]
            summary=str(rca_obj.get("summary") or "").strip() or None,
            root_cause=str(rca_obj.get("root_cause") or "").strip() or None,
            confidence_0_1=(
                float(rca_obj.get("confidence_0_1")) if rca_obj.get("confidence_0_1") is not None else None
            ),
            evidence=[str(x) for x in (rca_obj.get("evidence") or []) if str(x).strip()][:8],
            remediation=[str(x) for x in (rca_obj.get("remediation") or []) if str(x).strip()][:10],
            unknowns=[str(x) for x in (rca_obj.get("unknowns") or []) if str(x).strip()][:8],
        )
    except Exception as e:
        investigation.analysis.rca = RCAInsights(status="error", summary=f"RCA parse error: {type(e).__name__}")


# Export a graph object for LangGraph Studio / `langgraph dev`.
# This must be import-safe in environments where langgraph isn't installed.
try:
    graph = _compile_rca_graph(default_policy=_safe_load_policy())
except Exception:
    graph = None
