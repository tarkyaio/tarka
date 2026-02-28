"""Job failure evidence collector."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from agent.collectors.historical_fallback import apply_historical_fallback
from agent.collectors.k8s_context import gather_pod_context
from agent.collectors.log_parser import parse_log_entries
from agent.core.models import Investigation
from agent.providers.k8s_provider import get_events, get_k8s_provider, get_workload_rollout_status
from agent.providers.logs_provider import fetch_recent_logs


def _find_job_pods(namespace: str, job_name: str) -> List[Dict[str, Any]]:
    """Find pods created by a Job using job-name label selector.

    Kubernetes Jobs automatically add 'job-name=<job-name>' label to their pods.
    This works even if the Job object is deleted (pods may still exist briefly).
    """
    try:
        k8s = get_k8s_provider()
        pods = k8s.list_pods(namespace=namespace, label_selector=f"job-name={job_name}")
        # Sort by creation time, newest first (Jobs may retry)
        return sorted(pods, key=lambda p: p.get("metadata", {}).get("creationTimestamp", ""), reverse=True)
    except Exception:
        return []


def _adjust_time_window_for_job(investigation: Investigation) -> None:
    """Adjust investigation time window to use Job start time instead of 'now - 1h'.

    Critical for TTL-deleted Jobs: the default time window is calculated from 'now',
    but the Job may have started hours ago and been deleted. We need to look at the
    actual Job lifecycle: job.start_time → alert.time.
    """
    ns = investigation.target.namespace
    wk = investigation.target.workload_kind
    wn = investigation.target.workload_name

    if not (ns and wk == "Job" and wn):
        return

    try:
        # Fetch Job status from K8s API (includes start_time, completion_time)
        rs = get_workload_rollout_status(namespace=ns, kind="Job", name=wn)
        job_start_str = rs.get("start_time")

        if job_start_str:
            # Parse ISO timestamp (may have Z or +00:00)
            job_start = datetime.fromisoformat(job_start_str.replace("Z", "+00:00"))
            alert_end = investigation.time_window.end_time

            # Only adjust if job started BEFORE alert (normal case)
            if job_start < alert_end:
                investigation.time_window.start_time = job_start
                # Update window label for debugging
                duration_s = int((alert_end - job_start).total_seconds())
                investigation.time_window.window = f"job_lifetime_{duration_s}s"
                investigation.meta["time_window_adjusted"] = "job_start_time"
    except Exception as e:
        # Best-effort: if Job API fails, use default window
        investigation.errors.append(f"Failed to adjust time window for Job: {e}")


def collect_job_failure_evidence(investigation: Investigation) -> None:
    """Job failure evidence collector.

    Handles TTL-deleted Jobs by:
    1. Extracting Job identity from alert labels (job_name)
    2. Adjusting time window to Job start time (not "now - 1h")
    3. Finding Job pods via label selector (job-name=X)
    4. Collecting logs, events, and K8s context for Job pods
    5. Collecting events for Job resource itself
    6. Gracefully entering blocked mode when evidence unavailable

    Mutates investigation.evidence in-place. Never raises exceptions.
    """
    investigation.target.playbook = "job_failure"

    # Extract Job identity from alert labels BEFORE validation
    # KubeJobFailed alerts have job_name label with the actual Job resource name
    # (Don't confuse with 'job' label which is Prometheus scrape job like 'kube-state-metrics')
    alert_labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    job_name_from_label = alert_labels.get("job_name")

    # If we have job_name in alert labels, set workload identity for evidence collection
    if job_name_from_label:
        investigation.target.workload_kind = "Job"
        investigation.target.workload_name = str(job_name_from_label).strip()
        investigation.target.target_type = "pod"  # Jobs are pod-scoped

        # Clear service/job/instance fields - for Job alerts these refer to kube-state-metrics (scrape target)
        # not the actual Job workload, which causes confusion in UI "Affected Components"
        investigation.target.service = None
        investigation.target.job = None
        investigation.target.instance = None

    ns = investigation.target.namespace
    wk = investigation.target.workload_kind
    wn = investigation.target.workload_name

    # Validate Job identity (now should succeed after extraction)
    if not (ns and wk == "Job" and wn):
        investigation.errors.append(
            f"job_failure: missing Job identity - namespace={ns}, workload_kind={wk}, "
            f"workload_name={wn}. Alert labels: {list(alert_labels.keys())}"
        )
        return

    # Step 1: Adjust time window to job lifecycle
    _adjust_time_window_for_job(investigation)
    start_time = investigation.time_window.start_time
    end_time = investigation.time_window.end_time

    # Step 2: Get Job rollout status (start_time, completion_time, failed count)
    if investigation.evidence.k8s.rollout_status is None:
        try:
            investigation.evidence.k8s.rollout_status = get_workload_rollout_status(namespace=ns, kind="Job", name=wn)
        except Exception as e:
            investigation.errors.append(f"Failed to fetch Job rollout status: {e}")

    # Step 2.5: Collect K8s events for the Job resource (do this early, before pod checks)
    # Job events persist longer than pods and may contain critical failure info
    # (DeadlineExceeded, BackoffLimitExceeded, FailedCreate, etc.)
    try:
        job_events = get_events(namespace=ns, resource_type="job", resource_name=wn, limit=20)
        if job_events:
            # Initialize pod_events list if needed
            if investigation.evidence.k8s.pod_events is None:
                investigation.evidence.k8s.pod_events = []
            # Add Job events (will merge with pod events later if pods exist)
            investigation.evidence.k8s.pod_events.extend(job_events)
            investigation.meta["job_events_collected"] = len(job_events)
    except Exception as e:
        investigation.errors.append(f"Failed to fetch Job events: {e}")

    # Step 3: Find Job pods using label selector
    pods = _find_job_pods(ns, wn)
    pod_name = None

    if not pods:
        investigation.errors.append(
            f"No pods found for Job {wn} in namespace {ns} (may be TTL-deleted or never created)"
        )

        # NEW: Try historical fallback before giving up
        # Set pod name from Job name for fallback to extract prefix
        if not investigation.target.pod and wn:
            investigation.target.pod = wn

        apply_historical_fallback(investigation, pod_404=True)

        # Still no evidence after fallback? Enter blocked mode
        if not investigation.evidence.logs.logs:
            investigation.meta["blocked_mode"] = "job_pods_not_found"
            # Parse what we have before returning (even if empty)
            _parse_logs_universal(investigation)
            return

        # If we got logs from historical fallback, skip pod-specific collection
        investigation.meta["skipped_pod_collection"] = "historical_fallback_used"
        # Parse logs and return (skip steps 4-8)
        _parse_logs_universal(investigation)
        return

    # Step 4: Use most recent pod (Jobs may have multiple attempts due to retries)
    most_recent_pod = pods[0]
    pod_name = most_recent_pod.get("metadata", {}).get("name")

    if not pod_name:
        investigation.errors.append("Found Job pod but no name available")
        return

    # Step 5: Populate target.pod for downstream consumers (metrics, diagnostics)
    investigation.target.pod = pod_name
    investigation.target.target_type = "pod"
    investigation.meta["job_pods_found"] = len(pods)
    investigation.meta["job_pod_investigated"] = pod_name

    # Step 6: Collect K8s context for the pod
    if investigation.evidence.k8s.pod_info is None:
        k_ctx = gather_pod_context(pod_name, ns, events_limit=20)
        investigation.evidence.k8s.pod_info = k_ctx.get("pod_info")
        investigation.evidence.k8s.pod_conditions = k_ctx.get("pod_conditions") or []
        investigation.evidence.k8s.pod_events = k_ctx.get("pod_events") or []

        # Merge errors from K8s context gathering
        for err in k_ctx.get("errors") or []:
            investigation.errors.append(f"K8s context: {err}")

    # Step 7: Job events already collected in Step 2.5 (no longer needed here)

    # Step 8: Collect logs using adjusted time window
    if investigation.evidence.logs.logs_status is None and not investigation.evidence.logs.logs:
        try:
            container = investigation.target.container  # May be None (fetch all containers)
            logs_result = fetch_recent_logs(pod_name, ns, start_time, end_time, container=container, limit=400)
            investigation.evidence.logs.logs = logs_result.get("entries", [])
            investigation.evidence.logs.logs_status = logs_result.get("status")
            investigation.evidence.logs.logs_reason = logs_result.get("reason")
            investigation.evidence.logs.logs_backend = logs_result.get("backend")
            investigation.evidence.logs.logs_query = logs_result.get("query_used")
        except Exception as e:
            investigation.errors.append(f"Failed to fetch logs: {e}")
            investigation.evidence.logs.logs_status = "unavailable"
            investigation.evidence.logs.logs_reason = "unexpected_error"

    # Step 9: Parse logs (normal path)
    _parse_logs_universal(investigation)

    # Step 10: Optional AWS validation (if logs indicate cloud failures)
    _validate_aws_resources(investigation)


def _validate_aws_resources(investigation: Investigation) -> None:
    """
    Optional AWS resource validation when logs indicate cloud service failures.

    Only runs when:
    1. AWS_EVIDENCE_ENABLED=true
    2. Parsed errors exist
    3. Logs indicate S3/IAM/cloud issues

    Validates:
    - S3 bucket existence and accessibility
    - IAM role configuration (IRSA setup)
    - S3 permissions simulation
    """
    import os
    import re

    # Only run if AWS evidence is enabled
    if os.getenv("AWS_EVIDENCE_ENABLED") != "true":
        return

    # Check if we have parsed errors
    if not investigation.evidence.logs or not investigation.evidence.logs.parsed_errors:
        return

    parsed_errors = investigation.evidence.logs.parsed_errors
    error_text = "\n".join(e.get("message", "") for e in parsed_errors)

    # Check if logs indicate S3 issues
    has_s3_error = bool(
        re.search(r"(?:403|404|Forbidden|NoSuchBucket).*(?:s3|bucket)", error_text, re.IGNORECASE)
        or re.search(r"botocore\.exceptions\.ClientError.*(?:403|404)", error_text, re.IGNORECASE)
    )

    if not has_s3_error:
        return

    # Initialize AWS metadata if not exists
    if not investigation.evidence.aws.metadata:
        investigation.evidence.aws.metadata = {}

    # Extract bucket name from logs
    bucket_match = re.search(r"bucket[:\s]+([a-z0-9.-]+)", error_text, re.IGNORECASE)
    if bucket_match:
        bucket_name = bucket_match.group(1)

        # Validate S3 bucket
        try:
            from agent.providers.aws_s3_validator import check_s3_bucket_exists, get_s3_bucket_location

            s3_validation = check_s3_bucket_exists(bucket_name)
            investigation.evidence.aws.metadata["s3_validation"] = s3_validation

            # If bucket exists but access denied, try to get region
            if s3_validation.get("error_code") == "403":
                location = get_s3_bucket_location(bucket_name)
                if location.get("region"):
                    investigation.evidence.aws.metadata["s3_bucket_region"] = location["region"]

        except Exception as e:
            investigation.errors.append(f"AWS S3 validation failed: {e}")

    # Get service account IAM role (for IRSA validation)
    if investigation.evidence.k8s and investigation.evidence.k8s.pod_info:
        sa_name = investigation.evidence.k8s.pod_info.get("service_account_name")
        if sa_name:
            try:
                # Get service account to extract IAM role annotation
                from agent.providers.k8s_provider import get_service_account_info

                sa = get_service_account_info(investigation.target.namespace, sa_name)

                # Check for IRSA annotation
                annotations = sa.get("annotations", {})
                role_arn = annotations.get("eks.amazonaws.com/role-arn")

                if role_arn:
                    investigation.evidence.aws.metadata["irsa_role_arn"] = role_arn

                    # Get IAM role details
                    from agent.providers.aws_iam_validator import (
                        check_irsa_trust_policy,
                        extract_role_name_from_arn,
                        get_iam_role_info,
                    )

                    role_name = extract_role_name_from_arn(role_arn)
                    iam_info = get_iam_role_info(role_name)
                    investigation.evidence.aws.metadata["iam_role_info"] = iam_info

                    # Check IRSA trust policy
                    if iam_info.get("trust_policy"):
                        trust_check = check_irsa_trust_policy(iam_info["trust_policy"])
                        investigation.evidence.aws.metadata["irsa_trust_check"] = trust_check

                    # Policy documents are now included in iam_role_info
                    # The LLM/diagnostics can analyze the policy documents directly
                    # to determine if the bucket is allowed, rather than using
                    # iam:SimulatePrincipalPolicy which has API limitations

                else:
                    investigation.evidence.aws.metadata["irsa_role_arn"] = None
                    investigation.evidence.aws.metadata["irsa_issue"] = "No IRSA annotation found on service account"

            except Exception as e:
                investigation.errors.append(f"AWS IAM validation failed: {e}")


def _parse_logs_universal(investigation: Investigation) -> None:
    """
    Universal log parsing for job_failure playbook.

    Runs for both paths: historical fallback and normal pod collection.
    Only parses if logs exist and haven't been parsed yet.
    """
    if investigation.evidence.logs.logs and not investigation.evidence.logs.parsed_errors:
        try:
            parse_result = parse_log_entries(investigation.evidence.logs.logs, limit=50)
            investigation.evidence.logs.parsed_errors = parse_result["parsed_errors"]
            investigation.evidence.logs.parsing_metadata = parse_result["metadata"]
        except Exception as e:
            investigation.errors.append(f"Log parsing failed: {e}")


__all__ = ["collect_job_failure_evidence"]
