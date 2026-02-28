from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from agent.authz.policy import ActionPolicy, ChatPolicy, redact_text
from agent.core.models import AlertInstance, Investigation, TargetRef, TimeWindow
from agent.dump import investigation_to_json_dict
from agent.memory.case_retrieval import find_similar_runs
from agent.memory.skills import match_skills
from agent.pipeline.pipeline import run_investigation
from agent.providers.k8s_provider import get_k8s_provider
from agent.providers.logs_provider import fetch_recent_logs
from agent.providers.prom_provider import query_prometheus_instant

logger = logging.getLogger(__name__)


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _ensure_allowed_scope(policy: ChatPolicy, *, namespace: Optional[str], cluster: Optional[str]) -> Tuple[bool, str]:
    if policy.namespace_allowlist is not None and namespace:
        if str(namespace) not in policy.namespace_allowlist:
            return False, f"namespace_not_allowed:{namespace}"
    if policy.cluster_allowlist is not None and cluster:
        if str(cluster) not in policy.cluster_allowlist:
            return False, f"cluster_not_allowed:{cluster}"
    return True, "ok"


def _ensure_allowed_scope_actions(
    action_policy: ActionPolicy, *, namespace: Optional[str], cluster: Optional[str]
) -> Tuple[bool, str]:
    if action_policy.namespace_allowlist is not None and namespace:
        if str(namespace) not in action_policy.namespace_allowlist:
            return False, f"namespace_not_allowed:{namespace}"
    if action_policy.cluster_allowlist is not None and cluster:
        if str(cluster) not in action_policy.cluster_allowlist:
            return False, f"cluster_not_allowed:{cluster}"
    return True, "ok"


def _cap_list(xs: Any, cap: int) -> Any:
    if isinstance(xs, list) and len(xs) > cap:
        return xs[:cap]
    return xs


def _compact(obj: Any, *, max_chars: int = 6000) -> Any:
    """
    Best-effort compaction so tool results don't explode prompts/UI.
    """
    try:
        s = json.dumps(obj, ensure_ascii=False)
        if len(s) <= max_chars:
            return obj
        # Truncate by string representation (safe for display, not structured parsing).
        return {"truncated": True, "preview": s[:max_chars]}
    except Exception:
        txt = str(obj)
        if len(txt) <= max_chars:
            return txt
        return txt[:max_chars]


def _is_valid_repo_format(repo: str) -> bool:
    """Validate GitHub repo format is 'org/repo'."""
    if not repo or "/" not in repo:
        return False
    parts = repo.split("/")
    if len(parts) != 2:
        return False
    org, name = parts
    return bool(org and name)


def _discover_repo_for_chat(analysis_json: Dict[str, Any], *, workload_hint: str = "") -> Tuple[Optional[str], str]:
    """Discover GitHub repo from analysis context when evidence.github.repo is missing.

    Args:
        analysis_json: The full analysis JSON dict.
        workload_hint: Optional workload/service name passed by the LLM as a repo guess.
                       Used as an extra lookup key in the service catalog.

    Falls back through: evidence.github → alert labels → service catalog → naming convention.

    Returns:
        (repo, source) tuple where source describes how the repo was discovered.
    """
    try:
        # 1. Try evidence.github.repo (pipeline-discovered)
        gh = analysis_json.get("evidence", {}).get("github") or {}
        if gh.get("repo"):
            logger.info("Repo discovery: found %s via evidence.github", gh["repo"])
            return gh["repo"], "evidence.github"

        # 2. Try alert labels
        labels = analysis_json.get("alert", {}).get("labels", {})
        repo = labels.get("github_repo") or labels.get("github_repository")
        if repo and _is_valid_repo_format(repo):
            logger.info("Repo discovery: found %s via alert labels", repo)
            return repo, "alert_labels"

        # 3. Try service catalog using target workload name + optional LLM hint
        from agent.collectors.github_context import _discover_from_service_catalog

        tgt = analysis_json.get("target", {})
        workload = tgt.get("workload_name") or tgt.get("pod") or ""

        # Try each candidate against the service catalog
        candidates = [w for w in [workload, workload_hint] if w]
        for candidate in candidates:
            repo = _discover_from_service_catalog(candidate)
            if repo:
                logger.info("Repo discovery: found %s via service_catalog for candidate=%s", repo, candidate)
                return repo, "service_catalog"

        # 4. Try naming convention (GITHUB_DEFAULT_ORG/bare_name)
        from agent.collectors.github_context import _discover_from_naming_convention

        for candidate in candidates:
            repo = _discover_from_naming_convention(candidate)
            if repo:
                logger.info("Repo discovery: found %s via naming_convention for candidate=%s", repo, candidate)
                return repo, "naming_convention"

        logger.warning("Repo discovery: no repo found (tried candidates=%s)", candidates)
        return None, "not_found"
    except Exception:
        logger.warning("Repo discovery: unexpected error during discovery", exc_info=True)
        return None, "not_found"


def _resolve_github_repo(args: Dict[str, Any], analysis_json: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Resolve a valid org/repo string for GitHub tool calls.

    Handles three scenarios:
    1. LLM passed a valid org/repo → clean the name part and use it.
    2. LLM passed an invalid value (bare workload name) → use it as a hint for discovery.
    3. LLM passed nothing → run discovery from analysis_json context.

    Returns:
        (repo, source) tuple. source is one of: "args", "evidence.github",
        "alert_labels", "service_catalog", "naming_convention", "not_found".
    """
    from agent.collectors.github_context import _extract_base_service_name

    raw = str(args.get("repo") or "").strip()
    if raw and _is_valid_repo_format(raw):
        # LLM may pass a Job pod name as org/repo (e.g. "myorg/batch-etl-job-57992-0").
        # Clean the repo-name part to strip K8s instance suffixes, then try discovery
        # with the cleaned name to find the real repo via catalog/convention.
        org, name = raw.split("/", 1)
        clean_name = _extract_base_service_name(name)
        if clean_name != name:
            # Name was cleaned (had K8s suffixes) — run discovery with cleaned hint
            # so the service catalog fuzzy matching can find the real repo.
            logger.info(
                "github repo arg cleaned: %s/%s → hint=%s (stripped K8s suffix)",
                org,
                name,
                clean_name,
            )
            repo, source = _discover_repo_for_chat(analysis_json, workload_hint=clean_name)
            if repo:
                return repo, source
            # Discovery failed; fall back to cleaned org/repo
            return f"{org}/{clean_name}", "args_cleaned"
        return raw, "args"
    # raw is either empty or an invalid format (e.g. bare workload name).
    # Use it as a discovery hint.
    return _discover_repo_for_chat(analysis_json, workload_hint=raw)


def _build_investigation_from_analysis_json(analysis_json: Dict[str, Any]) -> Investigation:
    """
    Create a minimal Investigation used for memory similarity and reruns.
    """
    alert = analysis_json.get("alert") if isinstance(analysis_json.get("alert"), dict) else {}
    labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
    annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
    fp = str(alert.get("fingerprint") or "")
    starts_at = alert.get("starts_at")  # Preserve original alert timestamp
    ends_at = alert.get("ends_at")
    generator_url = alert.get("generator_url")
    state = alert.get("state")

    target_json = analysis_json.get("target") if isinstance(analysis_json.get("target"), dict) else {}
    target = TargetRef(**{k: v for k, v in (target_json or {}).items() if k in TargetRef.model_fields})

    # Use a tiny default window; tools that need a window supply one separately.
    now = datetime.now(timezone.utc)
    tw = TimeWindow(window="15m", start_time=now - timedelta(minutes=15), end_time=now)

    inv = Investigation(
        alert=AlertInstance(
            fingerprint=fp,
            labels=labels,
            annotations=annotations,
            starts_at=starts_at,  # Include original timestamps for reruns
            ends_at=ends_at,
            generator_url=generator_url,
            state=state,
        ),
        time_window=tw,
        target=target,
    )
    # If family is present, attach to analysis.features for memory retrieval filtering.
    fam = None
    a = analysis_json.get("analysis") if isinstance(analysis_json.get("analysis"), dict) else {}
    feats = a.get("features") if isinstance(a.get("features"), dict) else None
    if isinstance(feats, dict):
        fam = feats.get("family")
    if fam:
        try:
            from agent.core.models import DerivedFeatures

            inv.analysis.features = DerivedFeatures(family=str(fam))
        except Exception:
            pass
    return inv


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    result: Any = None
    error: Optional[str] = None
    updated_analysis: Optional[Dict[str, Any]] = None


def run_tool(
    *,
    policy: ChatPolicy,
    action_policy: Optional[ActionPolicy],
    tool: str,
    args: Dict[str, Any],
    analysis_json: Dict[str, Any],
    case_id: Optional[str] = None,
    run_id: Optional[str] = None,
    caller_logger: Optional[logging.Logger] = None,
) -> ToolResult:
    """
    Execute a single chat tool call with policy enforcement.

    Args:
        caller_logger: Optional logger to use instead of the default chat logger.
                      This allows RCA and other callers to use their own logger
                      for clearer log attribution.
    """
    tool = (tool or "").strip()
    if not tool:
        return ToolResult(ok=False, error="tool_missing")

    # Use caller's logger if provided, otherwise use module logger
    log = caller_logger or logger

    # Log tool invocation with compact args (for debugging)
    compact_args = {
        k: v for k, v in (args or {}).items() if k in ["namespace", "pod", "kind", "name", "query", "limit", "repo"]
    }
    log.info(f"Tool call: {tool} args={compact_args} case_id={case_id}")

    tgt = analysis_json.get("target") if isinstance(analysis_json.get("target"), dict) else {}
    namespace = tgt.get("namespace") if isinstance(tgt, dict) else None
    cluster = tgt.get("cluster") if isinstance(tgt, dict) else None
    ok_scope, why_scope = _ensure_allowed_scope(
        policy, namespace=str(namespace) if namespace else None, cluster=str(cluster) if cluster else None
    )
    if not ok_scope and tool.startswith(("k8s.", "logs.", "rerun.", "memory.")):
        log.warning(f"Tool {tool} blocked by scope policy: {why_scope}")
        return ToolResult(ok=False, error=why_scope)

    # --------------------
    # promql.*
    # --------------------
    if tool == "promql.instant":
        if not policy.allow_promql:
            return ToolResult(ok=False, error="tool_not_allowed")
        q = str(args.get("query") or "").strip()
        if not q:
            return ToolResult(ok=False, error="query_required")
        at = _parse_iso(str(args.get("at") or "")) or datetime.now(timezone.utc)
        try:
            res = query_prometheus_instant(q, at)
            res = _cap_list(res, policy.max_promql_series)
            return ToolResult(ok=True, result=_compact({"at": at.isoformat(), "query": q, "result": res}))
        except Exception as e:
            log.warning(f"PromQL query failed: query={q[:100]} error={str(e)[:200]}")
            return ToolResult(ok=False, error=f"promql_error:{type(e).__name__}")

    # --------------------
    # k8s.*
    # --------------------
    if tool == "k8s.pod_context":
        if not policy.allow_k8s_read:
            return ToolResult(ok=False, error="tool_not_allowed")
        pod = str(args.get("pod") or "").strip() or (tgt.get("pod") if isinstance(tgt, dict) else None)
        ns = str(args.get("namespace") or "").strip() or (tgt.get("namespace") if isinstance(tgt, dict) else None)

        # Support Jobs: if no pod but we have a Job workload, find the pods created by the Job
        if not pod and ns and isinstance(tgt, dict):
            workload_kind = tgt.get("kind")
            workload_name = tgt.get("workload")
            if workload_kind == "Job" and workload_name:
                try:
                    k8s = get_k8s_provider()
                    # Find pods created by this Job using label selector
                    pods = k8s.list_pods(namespace=ns, label_selector=f"job-name={workload_name}")
                    if pods:
                        # Use the most recent pod (Jobs may have multiple pods if they failed and restarted)
                        pod = (
                            max(pods, key=lambda p: p.get("metadata", {}).get("creationTimestamp", ""))
                            .get("metadata", {})
                            .get("name")
                        )
                except Exception:
                    pass  # Fall through to error below

        if not pod or not ns:
            missing = []
            if not pod:
                missing.append("pod_name")
            if not ns:
                missing.append("namespace")
            return ToolResult(ok=False, error=f"missing_required_args:{','.join(missing)}")
        try:
            from agent.playbooks.k8s_context import gather_pod_context

            out = gather_pod_context(str(pod), str(ns), events_limit=int(args.get("events_limit") or 20))
            return ToolResult(ok=True, result=_compact(out))
        except Exception as e:
            log.warning(f"K8s pod context failed: pod={pod} ns={ns} error={str(e)[:200]}")
            return ToolResult(ok=False, error=f"k8s_error:{type(e).__name__}")

    if tool == "k8s.rollout_status":
        if not policy.allow_k8s_read:
            return ToolResult(ok=False, error="tool_not_allowed")
        ns = str(args.get("namespace") or "").strip() or (tgt.get("namespace") if isinstance(tgt, dict) else None)
        kind = str(args.get("kind") or "").strip() or (tgt.get("workload_kind") if isinstance(tgt, dict) else None)
        name = str(args.get("name") or "").strip() or (tgt.get("workload_name") if isinstance(tgt, dict) else None)
        if not ns or not kind or not name:
            return ToolResult(ok=False, error="namespace_kind_name_required")
        try:
            k8s = get_k8s_provider()
            rs = k8s.get_workload_rollout_status(namespace=str(ns), kind=str(kind), name=str(name))
            return ToolResult(ok=True, result=_compact(rs))
        except Exception as e:
            # Include error message for debugging, not just exception type
            error_msg = str(e)[:200]  # Truncate to avoid token bloat
            log.warning(f"K8s rollout status failed: ns={ns} kind={kind} name={name} error={error_msg}")
            return ToolResult(ok=False, error=f"k8s_error:{type(e).__name__}:{error_msg}")

    if tool == "k8s.events":
        if not policy.allow_k8s_events:
            return ToolResult(ok=False, error="tool_not_allowed")
        ns = str(args.get("namespace") or "").strip() or (tgt.get("namespace") if isinstance(tgt, dict) else None)
        if not ns:
            return ToolResult(ok=False, error="namespace_required")

        # Optional resource_type and resource_name (defaults to namespace-wide events)
        resource_type = str(args.get("resource_type") or "").strip() or None
        resource_name = str(args.get("resource_name") or "").strip() or None

        # If no resource specified, try to default to investigation target
        if not resource_type and not resource_name and isinstance(tgt, dict):
            # Try pod first
            pod = tgt.get("pod")
            if pod:
                resource_type = "pod"
                resource_name = str(pod)
            else:
                # Try workload
                workload_kind = tgt.get("workload_kind")
                workload_name = tgt.get("workload_name")
                if workload_kind and workload_name:
                    resource_type = str(workload_kind).lower()
                    resource_name = str(workload_name)

        limit = int(args.get("limit") or 30)
        limit = max(5, min(limit, 100))  # Clamp between 5-100

        try:
            k8s = get_k8s_provider()
            events = k8s.get_events(
                namespace=str(ns), resource_type=resource_type, resource_name=resource_name, limit=limit
            )
            return ToolResult(
                ok=True,
                result=_compact(
                    {
                        "namespace": ns,
                        "resource_type": resource_type or "namespace-wide",
                        "resource_name": resource_name or "all",
                        "events": events,
                    }
                ),
            )
        except Exception as e:
            log.warning(
                f"K8s events query failed: ns={ns} type={resource_type} name={resource_name} error={str(e)[:200]}"
            )
            return ToolResult(ok=False, error=f"k8s_error:{type(e).__name__}")

    # --------------------
    # aws.*
    # --------------------
    if tool == "aws.ec2_status":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        instance_id = str(args.get("instance_id") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata if not provided
        if not instance_id or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if not instance_id and aws_metadata.get("ec2_instances"):
                instance_id = aws_metadata["ec2_instances"][0]
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not instance_id:
            return ToolResult(ok=False, error="instance_id_required")

        # Region allowlist check
        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_ec2_instance_status(instance_id, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.ebs_health":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        volume_id = str(args.get("volume_id") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata
        if not volume_id or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if not volume_id and aws_metadata.get("ebs_volumes"):
                volume_id = aws_metadata["ebs_volumes"][0]
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not volume_id:
            return ToolResult(ok=False, error="volume_id_required")

        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_ebs_volume_health(volume_id, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.elb_health":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        load_balancer = str(args.get("load_balancer") or "").strip()
        target_group_arn = str(args.get("target_group_arn") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata
        if (not load_balancer and not target_group_arn) or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if not load_balancer and not target_group_arn:
                if aws_metadata.get("elb_names"):
                    load_balancer = aws_metadata["elb_names"][0]
                elif aws_metadata.get("elbv2_target_groups"):
                    target_group_arn = aws_metadata["elbv2_target_groups"][0]
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not load_balancer and not target_group_arn:
            return ToolResult(ok=False, error="load_balancer_or_target_group_required")

        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            if target_group_arn:
                result = aws.get_elbv2_target_health(target_group_arn, region)
            else:
                result = aws.get_elb_target_health(load_balancer, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.rds_status":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        db_instance_id = str(args.get("db_instance_id") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata
        if not db_instance_id or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if not db_instance_id and aws_metadata.get("rds_instances"):
                db_instance_id = aws_metadata["rds_instances"][0]
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not db_instance_id:
            return ToolResult(ok=False, error="db_instance_id_required")

        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_rds_instance_status(db_instance_id, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.ecr_image":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        repository = str(args.get("repository") or "").strip()
        image_tag = str(args.get("image_tag") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata
        if (not repository or not image_tag) or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if (not repository or not image_tag) and aws_metadata.get("ecr_repositories"):
                ecr_ref = aws_metadata["ecr_repositories"][0]
                if isinstance(ecr_ref, dict):
                    repository = repository or ecr_ref.get("repository", "")
                    image_tag = image_tag or ecr_ref.get("tag", "")
                    region = region or ecr_ref.get("region", "us-east-1")
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not repository or not image_tag:
            return ToolResult(ok=False, error="repository_and_image_tag_required")

        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_ecr_image_scan_findings(repository, image_tag, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.security_group":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        security_group_id = str(args.get("security_group_id") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata
        if not security_group_id or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if not security_group_id and aws_metadata.get("security_groups"):
                security_group_id = aws_metadata["security_groups"][0]
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not security_group_id:
            return ToolResult(ok=False, error="security_group_id_required")

        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_security_group_rules(security_group_id, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.nat_gateway":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        nat_gateway_id = str(args.get("nat_gateway_id") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata
        if not nat_gateway_id or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if not nat_gateway_id and aws_metadata.get("nat_gateways"):
                nat_gateway_id = aws_metadata["nat_gateways"][0]
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not nat_gateway_id:
            return ToolResult(ok=False, error="nat_gateway_id_required")

        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_nat_gateway_status(nat_gateway_id, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.vpc_endpoint":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        vpc_endpoint_id = str(args.get("vpc_endpoint_id") or "").strip()
        region = str(args.get("region") or "").strip()

        # Auto-discover from investigation metadata
        if not vpc_endpoint_id or not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            if not vpc_endpoint_id and aws_metadata.get("vpc_endpoints"):
                vpc_endpoint_id = aws_metadata["vpc_endpoints"][0]
            if not region:
                region = aws_metadata.get("region", "us-east-1")

        if not vpc_endpoint_id:
            return ToolResult(ok=False, error="vpc_endpoint_id_required")

        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_vpc_endpoint_status(vpc_endpoint_id, region)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.cloudtrail_events":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        start_time_str = str(args.get("start_time") or "").strip()
        end_time_str = str(args.get("end_time") or "").strip()
        resource_ids_str = str(args.get("resource_ids") or "").strip()
        max_results = int(args.get("max_results") or 20)
        region = str(args.get("region") or "").strip()

        # Cap max_results
        max_results = min(max_results, 100)

        # Auto-discover region from investigation metadata
        if not region:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            region = aws_metadata.get("region", "us-east-1")

        # Region allowlist check
        if policy.aws_region_allowlist and region not in policy.aws_region_allowlist:
            return ToolResult(ok=False, error=f"region_not_allowed:{region}")

        # Parse time window (defaults to investigation window + 30m lookback)
        alert = analysis_json.get("alert", {})
        if start_time_str:
            # Parse relative time or ISO timestamp
            if start_time_str.endswith("m") or start_time_str.endswith("h"):
                # Relative time (e.g., "30m", "2h")
                from agent.core.time_window import parse_time_window

                tw = parse_time_window(start_time_str, datetime.now(timezone.utc))
                start_time = tw.start_time
            else:
                # ISO timestamp
                start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        else:
            # Default: investigation start - 30m
            alert_start_str = alert.get("starts_at")
            if alert_start_str:
                alert_start = datetime.fromisoformat(alert_start_str.replace("Z", "+00:00"))
                start_time = alert_start - timedelta(minutes=30)
            else:
                start_time = datetime.now(timezone.utc) - timedelta(hours=1)

        if end_time_str:
            # Parse relative time or ISO timestamp
            if end_time_str.endswith("m") or end_time_str.endswith("h"):
                from agent.core.time_window import parse_time_window

                tw = parse_time_window(end_time_str, datetime.now(timezone.utc))
                end_time = tw.end_time
            else:
                end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
        else:
            # Default: investigation end or now
            alert_end_str = alert.get("ends_at")
            if alert_end_str:
                end_time = datetime.fromisoformat(alert_end_str.replace("Z", "+00:00"))
            else:
                end_time = datetime.now(timezone.utc)

        # Parse resource IDs
        resource_ids = None
        if resource_ids_str:
            resource_ids = [rid.strip() for rid in resource_ids_str.split(",") if rid.strip()]

        # Auto-discover resource IDs if not provided
        if not resource_ids:
            aws_metadata = analysis_json.get("evidence", {}).get("aws", {}).get("metadata", {})
            resource_ids = []
            resource_ids.extend(aws_metadata.get("ec2_instances", []))
            resource_ids.extend(aws_metadata.get("ebs_volumes", []))
            resource_ids.extend(aws_metadata.get("rds_instances", []))
            resource_ids = resource_ids or None

        try:
            from agent.collectors.aws_context import _group_cloudtrail_events
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            events = aws.lookup_cloudtrail_events(region, start_time, end_time, resource_ids, max_results)

            # Check for errors
            if isinstance(events, list) and len(events) == 1 and isinstance(events[0], dict) and events[0].get("error"):
                return ToolResult(ok=False, error=events[0]["error"])

            # Group by category
            grouped = _group_cloudtrail_events(events)

            result = {
                "events": events,
                "grouped": grouped,
                "metadata": {
                    "time_window": f"{start_time.isoformat()} to {end_time.isoformat()}",
                    "event_count": len(events),
                    "region": region,
                },
            }

            return ToolResult(ok=True, result=result)
        except Exception as e:
            log.warning(f"CloudTrail events query failed: region={region} error={str(e)[:400]}")
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}:{str(e)[:200]}")

    if tool == "aws.s3_bucket_location":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        bucket = str(args.get("bucket") or "").strip()

        # Auto-extract bucket name from parsed_errors if not provided
        if not bucket:
            parsed_errors = analysis_json.get("evidence", {}).get("logs", {}).get("parsed_errors", [])
            for error in parsed_errors:
                # Match patterns like: "bucket: foo" or "for bucket foo" or "bucket=foo" or "for example-bucket.example.com:"
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                # Try multiple patterns to extract bucket name (most specific first)
                patterns = [
                    r"for\s+([a-z0-9.-]+):",  # "for example-bucket.example.com:" (most specific)
                    r"bucket[:=]\s*([a-z0-9.-]+)",  # "bucket: foo" or "bucket=foo" (require : or =)
                    r"bucket\s+([a-z0-9.-]+)",  # "bucket foo" (fallback)
                ]
                for pattern in patterns:
                    match = re.search(pattern, message, re.IGNORECASE)
                    if match:
                        bucket = match.group(1)
                        break
                if bucket:
                    break

        if not bucket:
            return ToolResult(ok=False, error="bucket_name_required")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_s3_bucket_location(bucket)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    if tool == "aws.iam_role_permissions":
        if not policy.allow_aws_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        role_name = str(args.get("role_name") or "").strip()
        service_account = str(args.get("service_account") or "").strip()

        # Track whether we attempted to extract from service account but found no annotation
        sa_checked_no_annotation = False

        # If service_account provided, fetch its annotations to get role ARN
        if not role_name and service_account:
            namespace = str(args.get("namespace") or "").strip()
            if not namespace:
                # Try to get namespace from target
                namespace = analysis_json.get("target", {}).get("namespace")

            if namespace:
                try:
                    from agent.providers.k8s_provider import get_service_account_info

                    sa_info = get_service_account_info(namespace, service_account)
                    if sa_info and isinstance(sa_info.get("annotations"), dict):
                        role_arn = sa_info["annotations"].get("eks.amazonaws.com/role-arn") or sa_info[
                            "annotations"
                        ].get("iam.amazonaws.com/role")
                        if role_arn and "/" in role_arn:
                            role_name = role_arn.split("/")[-1]
                        else:
                            # Service account exists but has no IAM role annotation
                            sa_checked_no_annotation = True
                    else:
                        # Service account exists but has no annotations dict
                        sa_checked_no_annotation = True
                except Exception:
                    pass  # Fall through to try pod_info extraction

        # Try to extract from pod_info annotation (if available in evidence)
        if not role_name:
            pod_info = analysis_json.get("evidence", {}).get("k8s", {}).get("pod_info", {})

            # Try to extract role from service account annotations if available
            # Format: eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/MyRole
            annotations = pod_info.get("annotations", {})
            role_arn = annotations.get("eks.amazonaws.com/role-arn") or annotations.get("iam.amazonaws.com/role")
            if role_arn:
                # Extract role name from ARN (arn:aws:iam::123456789012:role/MyRole)
                if "/" in role_arn:
                    role_name = role_arn.split("/")[-1]

        if not role_name:
            # Return more specific error if we checked service account but found no IRSA annotation
            if sa_checked_no_annotation and service_account:
                return ToolResult(
                    ok=False,
                    error="no_iam_role_annotation",
                    result={
                        "message": f"Service account {service_account} has no IAM role annotation (IRSA not configured)",
                        "remediation": "Configure IRSA by adding eks.amazonaws.com/role-arn annotation to the service account",
                    },
                )
            return ToolResult(ok=False, error="role_name_required")

        try:
            from agent.providers.aws_provider import get_aws_provider

            aws = get_aws_provider()
            result = aws.get_iam_role_permissions(role_name)
            return ToolResult(ok=True, result=_compact(result))
        except Exception as e:
            return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")

    # --------------------
    # github.*
    # --------------------
    if tool == "github.recent_commits":
        if not policy.allow_github_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        branch = str(args.get("branch") or "main").strip()
        since_str = str(args.get("since") or "")
        limit = min(int(args.get("limit", 20)), 30)  # hard cap at 30

        repo, source = _resolve_github_repo(args, analysis_json)
        if not repo:
            log.warning("github.recent_commits: repo not discovered (raw=%s, source=%s)", args.get("repo"), source)
            return ToolResult(
                ok=False,
                error="repo_not_discovered: could not resolve repo. Try 'org/repo' format, or add to service-catalog.yaml",
            )

        log.info("github.recent_commits: using repo=%s (source=%s)", repo, source)

        # Check repo allowlist
        if policy.github_repo_allowlist and repo not in policy.github_repo_allowlist:
            log.warning("github.recent_commits: repo not in allowlist: %s", repo)
            return ToolResult(ok=False, error=f"repo_not_allowed:{repo} - not in configured allowlist")

        # Parse time window
        until = datetime.now(timezone.utc)
        using_default_window = not since_str
        if since_str:
            since = _parse_iso(since_str)
        else:
            since = until - timedelta(hours=2)

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            commits = github.get_recent_commits(repo=repo, since=since, until=until, branch=branch)

            # Filter out error responses
            if (
                commits
                and isinstance(commits, list)
                and len(commits) >= 1
                and isinstance(commits[0], dict)
                and "error" in commits[0]
            ):
                err_msg = commits[0].get("message", "")
                log.warning("github.recent_commits: provider error for repo=%s: %s", repo, err_msg)
                return ToolResult(ok=False, error=f"{commits[0].get('error', 'github_error')}: {err_msg}")

            # Auto-widen: if default 2h window returned 0 commits, retry with 24h
            if not commits and using_default_window:
                since = until - timedelta(hours=24)
                log.info("github.recent_commits: 0 commits in 2h window, auto-widening to 24h for repo=%s", repo)
                commits = github.get_recent_commits(repo=repo, since=since, until=until, branch=branch)
                # Check for error on retry too
                if (
                    commits
                    and isinstance(commits, list)
                    and len(commits) >= 1
                    and isinstance(commits[0], dict)
                    and "error" in commits[0]
                ):
                    err_msg = commits[0].get("message", "")
                    log.warning("github.recent_commits: provider error for repo=%s: %s", repo, err_msg)
                    return ToolResult(ok=False, error=f"{commits[0].get('error', 'github_error')}: {err_msg}")

            searched_hours = round((until - since).total_seconds() / 3600)
            total = len(commits)
            commits = commits[:limit]
            return ToolResult(
                ok=True,
                result=_compact(
                    {
                        "repo": repo,
                        "branch": branch,
                        "commits": commits,
                        "total_available": total,
                        "returned": len(commits),
                        "searched_window_hours": searched_hours,
                    }
                ),
            )
        except Exception as e:
            log.warning("github.recent_commits failed: repo=%s error=%s", repo, str(e)[:200])
            return ToolResult(ok=False, error=f"github_error:{type(e).__name__}")

    if tool == "github.workflow_runs":
        if not policy.allow_github_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        since_str = str(args.get("since") or "")
        limit = int(args.get("limit", 10))

        repo, source = _resolve_github_repo(args, analysis_json)
        if not repo:
            log.warning("github.workflow_runs: repo not discovered (raw=%s, source=%s)", args.get("repo"), source)
            return ToolResult(
                ok=False,
                error="repo_not_discovered: could not resolve repo. Try 'org/repo' format, or add to service-catalog.yaml",
            )

        log.info("github.workflow_runs: using repo=%s (source=%s)", repo, source)

        if policy.github_repo_allowlist and repo not in policy.github_repo_allowlist:
            log.warning("github.workflow_runs: repo not in allowlist: %s", repo)
            return ToolResult(ok=False, error=f"repo_not_allowed:{repo} - not in configured allowlist")

        # Parse time window
        if since_str:
            since = _parse_iso(since_str)
        else:
            since = datetime.now(timezone.utc) - timedelta(hours=2)

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            runs = github.get_workflow_runs(repo=repo, since=since, limit=limit)

            # Filter out error responses
            if runs and isinstance(runs, list) and len(runs) >= 1 and isinstance(runs[0], dict) and "error" in runs[0]:
                err_msg = runs[0].get("message", "")
                log.warning("github.workflow_runs: provider error for repo=%s: %s", repo, err_msg)
                return ToolResult(ok=False, error=f"{runs[0].get('error', 'github_error')}: {err_msg}")

            return ToolResult(ok=True, result=_compact({"repo": repo, "workflow_runs": runs}))
        except Exception as e:
            log.warning("github.workflow_runs failed: repo=%s error=%s", repo, str(e)[:200])
            return ToolResult(ok=False, error=f"github_error:{type(e).__name__}")

    if tool == "github.workflow_logs":
        if not policy.allow_github_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        run_id = int(args.get("run_id", 0))
        job_id = int(args.get("job_id", 0))

        repo, source = _resolve_github_repo(args, analysis_json)
        if not repo:
            log.warning("github.workflow_logs: repo not discovered (raw=%s, source=%s)", args.get("repo"), source)
            return ToolResult(
                ok=False,
                error="repo_not_discovered: could not resolve repo. Try 'org/repo' format, or add to service-catalog.yaml",
            )

        log.info("github.workflow_logs: using repo=%s (source=%s)", repo, source)

        if not run_id or not job_id:
            return ToolResult(ok=False, error="run_id_and_job_id_required")

        if policy.github_repo_allowlist and repo not in policy.github_repo_allowlist:
            log.warning("github.workflow_logs: repo not in allowlist: %s", repo)
            return ToolResult(ok=False, error=f"repo_not_allowed:{repo} - not in configured allowlist")

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            logs = github.get_workflow_run_logs(repo=repo, run_id=run_id, job_id=job_id)

            # Check if logs contain error message
            if logs.startswith("Error fetching logs"):
                return ToolResult(ok=False, error="github_error:log_fetch_failed")

            return ToolResult(
                ok=True, result=_compact({"repo": repo, "run_id": run_id, "job_id": job_id, "logs": logs})
            )
        except Exception as e:
            log.warning("github.workflow_logs failed: repo=%s error=%s", repo, str(e)[:200])
            return ToolResult(ok=False, error=f"github_error:{type(e).__name__}")

    if tool == "github.read_file":
        if not policy.allow_github_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        path = str(args.get("path") or "").strip()
        ref = str(args.get("ref") or "main").strip()

        repo, source = _resolve_github_repo(args, analysis_json)
        if not repo:
            log.warning("github.read_file: repo not discovered (raw=%s, source=%s)", args.get("repo"), source)
            return ToolResult(
                ok=False,
                error="repo_not_discovered: could not resolve repo. Try 'org/repo' format, or add to service-catalog.yaml",
            )

        log.info("github.read_file: using repo=%s (source=%s)", repo, source)

        if not path:
            return ToolResult(ok=False, error="path_required")

        # Path validation (prevent directory traversal)
        if ".." in path or path.startswith("/"):
            return ToolResult(ok=False, error="invalid_path")

        if policy.github_repo_allowlist and repo not in policy.github_repo_allowlist:
            log.warning("github.read_file: repo not in allowlist: %s", repo)
            return ToolResult(ok=False, error=f"repo_not_allowed:{repo} - not in configured allowlist")

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            content = github.get_file_contents(repo=repo, path=path, ref=ref)

            return ToolResult(ok=True, result=_compact({"repo": repo, "path": path, "ref": ref, "content": content}))
        except Exception as e:
            log.warning("github.read_file failed: repo=%s error=%s", repo, str(e)[:200])
            return ToolResult(ok=False, error=f"github_error:{type(e).__name__}")

    if tool == "github.commit_diff":
        if not policy.allow_github_read:
            return ToolResult(ok=False, error="tool_not_allowed")

        sha = str(args.get("sha") or "").strip()
        if not sha:
            return ToolResult(ok=False, error="sha_required")

        repo, source = _resolve_github_repo(args, analysis_json)
        if not repo:
            log.warning("github.commit_diff: repo not discovered (raw=%s, source=%s)", args.get("repo"), source)
            return ToolResult(
                ok=False,
                error="repo_not_discovered: could not resolve repo. Try 'org/repo' format, or add to service-catalog.yaml",
            )

        log.info("github.commit_diff: using repo=%s sha=%s (source=%s)", repo, sha, source)

        if policy.github_repo_allowlist and repo not in policy.github_repo_allowlist:
            log.warning("github.commit_diff: repo not in allowlist: %s", repo)
            return ToolResult(ok=False, error=f"repo_not_allowed:{repo} - not in configured allowlist")

        try:
            from agent.providers.github_provider import get_github_provider

            github = get_github_provider()
            diff = github.get_commit_diff(repo=repo, sha=sha)

            # Check for error dict from provider
            if isinstance(diff, dict) and "error" in diff:
                err_msg = diff.get("message", "")
                log.warning("github.commit_diff: provider error for repo=%s sha=%s: %s", repo, sha, err_msg)
                return ToolResult(ok=False, error=f"{diff.get('error', 'github_error')}: {err_msg}")

            return ToolResult(ok=True, result=_compact({"repo": repo, **diff}))
        except Exception as e:
            log.warning("github.commit_diff failed: repo=%s sha=%s error=%s", repo, sha, str(e)[:200])
            return ToolResult(ok=False, error=f"github_error:{type(e).__name__}")

    # --------------------
    # logs.*
    # --------------------
    if tool == "logs.tail":
        if not policy.allow_logs_query:
            return ToolResult(ok=False, error="tool_not_allowed")
        pod = str(args.get("pod") or "").strip() or (tgt.get("pod") if isinstance(tgt, dict) else None)
        ns = str(args.get("namespace") or "").strip() or (tgt.get("namespace") if isinstance(tgt, dict) else None)

        # Support Jobs: if no pod but we have a Job workload, find the pods created by the Job
        if not pod and ns and isinstance(tgt, dict):
            workload_kind = tgt.get("kind")
            workload_name = tgt.get("workload")
            if workload_kind == "Job" and workload_name:
                try:
                    k8s = get_k8s_provider()
                    # Find pods created by this Job using label selector
                    pods = k8s.list_pods(namespace=ns, label_selector=f"job-name={workload_name}")
                    if pods:
                        # Use the most recent pod (Jobs may have multiple pods if they failed and restarted)
                        pod = (
                            max(pods, key=lambda p: p.get("metadata", {}).get("creationTimestamp", ""))
                            .get("metadata", {})
                            .get("name")
                        )
                except Exception:
                    pass  # Fall through to error below

        if not pod or not ns:
            missing = []
            if not pod:
                missing.append("pod_name")
            if not ns:
                missing.append("namespace")
            return ToolResult(ok=False, error=f"missing_required_args:{','.join(missing)}")
        try:
            end_time = _parse_iso(str(args.get("end_time") or "")) or datetime.now(timezone.utc)
            start_time = _parse_iso(str(args.get("start_time") or "")) or (end_time - timedelta(minutes=15))
            limit = int(args.get("limit") or policy.max_log_lines)
            limit = max(10, min(limit, policy.max_log_lines))
            container = str(args.get("container") or "").strip() or None
            out = fetch_recent_logs(str(pod), str(ns), start_time, end_time, container=container, limit=limit)
            # Redact messages before returning to chat.
            if policy.redact_secrets and isinstance(out, dict) and isinstance(out.get("entries"), list):
                entries = []
                for e in out.get("entries") or []:
                    if not isinstance(e, dict):
                        continue
                    msg = e.get("message")
                    if isinstance(msg, str):
                        e = dict(e)
                        e["message"] = redact_text(msg)
                    entries.append(e)
                out = dict(out)
                out["entries"] = entries
            return ToolResult(ok=True, result=_compact(out))
        except Exception as e:
            return ToolResult(ok=False, error=f"logs_error:{type(e).__name__}")

    # --------------------
    # memory.*
    # --------------------
    if tool == "memory.similar_cases":
        if not policy.allow_memory_read:
            return ToolResult(ok=False, error="tool_not_allowed")
        try:
            inv = _build_investigation_from_analysis_json(analysis_json)
            ok, msg, sims = find_similar_runs(inv, limit=int(args.get("limit") or 5))
            if not ok:
                return ToolResult(ok=False, error=f"memory_unavailable:{msg}")
            items = []
            for s in sims or []:
                items.append(
                    {
                        "case_id": s.case_id,
                        "run_id": s.run_id,
                        "created_at": s.created_at,
                        "one_liner": s.one_liner,
                        "s3_report_key": s.s3_report_key,
                        "resolution_category": getattr(s, "resolution_category", None),
                        "resolution_summary": getattr(s, "resolution_summary", None),
                        "postmortem_link": getattr(s, "postmortem_link", None),
                    }
                )
            return ToolResult(ok=True, result={"status": "ok", "items": items})
        except Exception as e:
            return ToolResult(ok=False, error=f"memory_error:{type(e).__name__}")

    if tool == "memory.skills":
        if not policy.allow_memory_read:
            return ToolResult(ok=False, error="tool_not_allowed")
        try:
            inv = _build_investigation_from_analysis_json(analysis_json)
            ok, msg, matches = match_skills(inv, max_matches=int(args.get("max_matches") or 5))
            if not ok:
                return ToolResult(ok=False, error=f"memory_unavailable:{msg}")
            items = []
            for m in matches or []:
                items.append({"name": m.skill.name, "version": m.skill.version, "rendered": m.rendered})
            return ToolResult(ok=True, result={"status": "ok", "items": items})
        except Exception as e:
            return ToolResult(ok=False, error=f"memory_error:{type(e).__name__}")

    # --------------------
    # actions.*
    # --------------------
    if tool == "actions.list":
        if action_policy is None or not action_policy.enabled:
            return ToolResult(ok=False, error="tool_not_allowed")
        if not case_id:
            return ToolResult(ok=False, error="case_id_required")
        ok_scope_a, why_scope_a = _ensure_allowed_scope_actions(
            action_policy, namespace=str(namespace) if namespace else None, cluster=str(cluster) if cluster else None
        )
        if not ok_scope_a:
            return ToolResult(ok=False, error=why_scope_a)
        try:
            from agent.memory.actions import list_case_actions

            ok, msg, items = list_case_actions(case_id=str(case_id), limit=int(args.get("limit") or 50))
            if not ok:
                return ToolResult(ok=False, error=f"actions_unavailable:{msg}")
            out = []
            for a in items:
                out.append(a.__dict__)
            return ToolResult(ok=True, result={"status": "ok", "items": out})
        except Exception as e:
            return ToolResult(ok=False, error=f"actions_error:{type(e).__name__}")

    if tool == "actions.propose":
        if action_policy is None or not action_policy.enabled:
            return ToolResult(ok=False, error="tool_not_allowed")
        if not case_id:
            return ToolResult(ok=False, error="case_id_required")
        ok_scope_a, why_scope_a = _ensure_allowed_scope_actions(
            action_policy, namespace=str(namespace) if namespace else None, cluster=str(cluster) if cluster else None
        )
        if not ok_scope_a:
            return ToolResult(ok=False, error=why_scope_a)
        atype = str(args.get("action_type") or "").strip().lower()
        title = str(args.get("title") or "").strip()
        if not atype or not title:
            missing = []
            if not atype:
                missing.append("action_type")
            if not title:
                missing.append("title")
            return ToolResult(ok=False, error=f"missing_required_args:{','.join(missing)}")
        if action_policy.action_type_allowlist is not None and atype not in action_policy.action_type_allowlist:
            return ToolResult(ok=False, error="action_type_not_allowed")
        try:
            # Enforce max actions per case
            from agent.memory.actions import create_case_action, list_case_actions

            okc, _msgc, existing = list_case_actions(case_id=str(case_id), limit=action_policy.max_actions_per_case + 1)
            if okc and len(existing) >= int(action_policy.max_actions_per_case):
                return ToolResult(ok=False, error="case_action_limit_reached")

            ok, msg, action_id = create_case_action(
                case_id=str(case_id),
                run_id=str(run_id) if run_id else (str(args.get("run_id")) if args.get("run_id") else None),
                hypothesis_id=str(args.get("hypothesis_id")) if args.get("hypothesis_id") else None,
                action_type=atype,
                title=title,
                risk=str(args.get("risk")) if args.get("risk") else None,
                preconditions=(
                    list(args.get("preconditions") or []) if isinstance(args.get("preconditions"), list) else []
                ),
                execution_payload=(
                    args.get("execution_payload") if isinstance(args.get("execution_payload"), dict) else {}
                ),
                proposed_by=str(args.get("actor")) if args.get("actor") else "chat",
            )
            if not ok:
                return ToolResult(ok=False, error=f"actions_unavailable:{msg}")
            return ToolResult(ok=True, result={"status": "ok", "action_id": action_id})
        except Exception as e:
            return ToolResult(ok=False, error=f"actions_error:{type(e).__name__}")

    # --------------------
    # rerun.*
    # --------------------
    if tool == "rerun.investigation":
        if not policy.allow_report_rerun:
            return ToolResult(ok=False, error="tool_not_allowed")
        tw = str(args.get("time_window") or "").strip()
        if not tw:
            return ToolResult(ok=False, error="time_window_required")

        # reference_time controls whether to investigate historical state or current state
        reference_time = str(args.get("reference_time") or "original").strip().lower()
        if reference_time not in ("original", "now"):
            return ToolResult(ok=False, error="reference_time_must_be_original_or_now")

        # Enforce max window by parsing through agent's parser indirectly: run with a guard.
        # We approximate: allow only a bounded set in seconds by parsing after the run (cheap guard).
        # If the user passes a huge window, run_investigation will still parse; guard below rejects if too large.
        inv0 = _build_investigation_from_analysis_json(analysis_json)

        # Build alert dict based on reference_time mode
        if reference_time == "original":
            # Historical mode: use original alert timestamp (what happened when alert fired)
            alert = {
                "fingerprint": inv0.alert.fingerprint,
                "labels": inv0.alert.labels or {},
                "annotations": inv0.alert.annotations or {},
                "starts_at": inv0.alert.starts_at,  # Critical: use original alert time
                "ends_at": inv0.alert.ends_at,
                "generator_url": inv0.alert.generator_url,
                "status": {"state": inv0.alert.state or "active"},
            }
        else:
            # Current state mode: use "now" as reference time
            now = datetime.now(timezone.utc)
            alert = {
                "fingerprint": inv0.alert.fingerprint,
                "labels": inv0.alert.labels or {},
                "annotations": inv0.alert.annotations or {},
                "starts_at": now.isoformat(),  # Use current time as reference
                "ends_at": "0001-01-01T00:00:00Z",  # Active alert
                "generator_url": inv0.alert.generator_url,
                "status": {"state": "active"},
            }

        inv2 = run_investigation(alert=alert, time_window=tw)
        # Guard the effective window (parsed by pipeline).
        try:
            dt = inv2.time_window.end_time - inv2.time_window.start_time
            if dt.total_seconds() > float(policy.max_time_window_seconds):
                return ToolResult(ok=False, error="time_window_too_large")
        except Exception:
            pass
        aj = investigation_to_json_dict(inv2, mode="analysis")
        # Return only analysis slice (smaller) for chat use.
        return ToolResult(ok=True, result={"status": "ok"}, updated_analysis=aj.get("analysis"))

    # --------------------
    # argocd.*
    # --------------------
    if tool.startswith("argocd."):
        if not policy.allow_argocd_read:
            log.warning(f"Tool {tool} not allowed by policy")
            return ToolResult(ok=False, error="tool_not_allowed")
        # Provider placeholder exists but is not implemented.
        log.warning(f"Tool {tool} not implemented")
        return ToolResult(ok=False, error="argocd_not_implemented")

    log.warning(f"Unknown tool: {tool}")
    return ToolResult(ok=False, error="unknown_tool")
