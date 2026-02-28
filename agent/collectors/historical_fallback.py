"""
Universal historical fallback for TTL-deleted pods.

When Kubernetes returns 404 for a pod (e.g., Job pods with ttlSecondsAfterFinished),
this module activates a best-effort investigation mode using:
- alert.starts_at for time window anchoring
- Pod name extraction from alert annotations
- Regex log queries
- Historical metrics via PromQL label selectors
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from agent.core.time_window import parse_time_window

if TYPE_CHECKING:
    from agent.core.models import Investigation


def apply_historical_fallback(
    investigation: "Investigation",
    *,
    pod_404: bool = False,
) -> None:
    """
    Universal historical fallback when K8s API returns 404 for pod.

    Strategy:
    1. Use alert.starts_at as time anchor (NOT "now - 1h")
    2. Extract pod name from alert annotations as backup
    3. Use regex patterns for logs (pod=~"prefix-.*")
    4. Mark investigation with historical_mode metadata
    5. Collect what we can (logs, metrics via PromQL label selectors)

    Args:
        investigation: Investigation object to enrich
        pod_404: Set to True if K8s API returned 404 for the target pod
    """
    if not pod_404:
        # Only activate on confirmed 404
        return

    # Mark as historical mode
    investigation.meta["historical_mode"] = True
    investigation.meta["historical_reason"] = "pod_not_found_in_k8s"

    # Step 1: Adjust time window to use alert.starts_at
    alert_starts_at = getattr(investigation.alert, "starts_at", None)
    if alert_starts_at and investigation.time_window:
        try:
            alert_start = datetime.fromisoformat(alert_starts_at.replace("Z", "+00:00"))

            # Calculate lookback duration using existing parse_time_window
            temp_start, temp_end = parse_time_window(investigation.time_window.window)
            duration = temp_end - temp_start

            # Use alert start as anchor
            investigation.time_window.start_time = alert_start - duration
            investigation.time_window.end_time = alert_start

            investigation.meta["time_window_adjusted_to_alert"] = True
        except Exception as e:
            investigation.errors.append(f"Historical fallback: failed to adjust time window: {e}")

    # Step 2: Try to extract pod name from alert annotations
    if not investigation.target.pod:
        pod_name = _extract_pod_name_from_alert(investigation.alert)
        if pod_name:
            investigation.target.pod = pod_name
            investigation.meta["pod_name_source"] = "alert_annotations"

    # Step 3: Try to collect logs using regex patterns
    if investigation.target.pod and investigation.target.namespace:
        _collect_logs_with_regex(investigation)


def _extract_pod_name_from_alert(alert) -> Optional[str]:
    """
    Try to extract pod name from alert annotations.

    Looks for pod names in:
    - summary field
    - description field
    - message field

    Patterns:
    - pod: <name>
    - pod <name>
    - Pod <name>
    - Kubernetes pod `<name>`
    """
    # Handle both dict and Pydantic model
    if hasattr(alert, "annotations"):
        annotations = alert.annotations
    elif isinstance(alert, dict):
        annotations = alert.get("annotations", {})
    else:
        return None

    if not isinstance(annotations, dict):
        return None

    # Try common annotation fields
    for field in ["summary", "description", "message"]:
        text = annotations.get(field, "")
        if not isinstance(text, str):
            continue

        # Try various patterns
        patterns = [
            r'pod[:\s]+[`"]?([a-z0-9][-a-z0-9]*)[`"]?',  # pod: name or pod name
            r'Pod[:\s]+[`"]?([a-z0-9][-a-z0-9]*)[`"]?',  # Pod: name
            r'Kubernetes pod[:\s]+[`"]?([a-z0-9][-a-z0-9]*)[`"]?',  # Kubernetes pod name
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                pod_name = match.group(1)
                # Validate it looks like a pod name (not just 'is' or 'the')
                if len(pod_name) > 3 and "-" in pod_name:
                    return pod_name

    return None


def _collect_logs_with_regex(investigation: "Investigation") -> None:
    """
    Collect logs using regex pattern matching on pod name.

    Strategy:
    - Extract pod name prefix (everything before the last hash)
    - Query logs with regex: {namespace="X", pod=~"prefix-.*"}
    """
    from agent.providers.logs_provider import get_logs_provider

    pod_name = investigation.target.pod
    namespace = investigation.target.namespace

    if not pod_name or not namespace:
        return

    # Extract pod name prefix for regex
    # Examples:
    #   batch-etl-job-57438-0-lmwj3 -> batch-etl-job-57438-0-.*
    #   myapp-6d4b8c9f7-xk5p2 -> myapp-6d4b8c9f7-.*
    pod_prefix = _extract_pod_prefix(pod_name)
    if not pod_prefix:
        return

    # Build regex pattern
    pod_pattern = f"{pod_prefix}.*"

    try:
        logs_provider = get_logs_provider()
        result = logs_provider.fetch_recent_logs(
            pod_name=pod_pattern,
            namespace=namespace,
            start_time=investigation.time_window.start_time,
            end_time=investigation.time_window.end_time,
            limit=400,
            use_regex=True,  # Enable regex matching
        )

        # Populate evidence
        investigation.evidence.logs.logs = result.get("entries", [])
        investigation.evidence.logs.logs_status = result.get("status")
        investigation.evidence.logs.logs_reason = result.get("reason")
        investigation.evidence.logs.logs_backend = result.get("backend")
        investigation.evidence.logs.logs_query = result.get("query_used")

        if result.get("status") == "ok":
            investigation.meta["historical_logs_collected"] = True
    except Exception as e:
        investigation.errors.append(f"Historical fallback: logs collection failed: {e}")


def _extract_pod_prefix(pod_name: str) -> Optional[str]:
    """
    Extract pod name prefix for regex matching.

    Examples:
        batch-etl-job-57438-0-lmwj3 -> batch-etl-job-57438-0
        myapp-6d4b8c9f7-xk5p2 -> myapp-6d4b8c9f7
        prometheus-kube-state-metrics-99bf89fcf-z5rmg -> prometheus-kube-state-metrics-99bf89fcf
    """
    # Remove the last segment (usually random suffix)
    parts = pod_name.rsplit("-", 1)
    if len(parts) == 2:
        return parts[0]

    # Fallback: use full name
    return pod_name
