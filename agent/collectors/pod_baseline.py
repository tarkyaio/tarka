"""Shared pod-scoped baseline evidence collector."""

from __future__ import annotations

from typing import Optional

from agent.collectors.k8s_context import gather_pod_context
from agent.collectors.log_parser import parse_log_entries
from agent.core.models import Investigation
from agent.providers.logs_provider import fetch_recent_logs
from agent.providers.prom_provider import (
    query_cpu_usage_and_limits,
    query_memory_usage_and_limits,
    query_pod_not_healthy,
    query_pod_restarts,
)


def _container_from_investigation(investigation: Investigation) -> Optional[str]:
    if investigation.target and investigation.target.container:
        return investigation.target.container
    labels = investigation.alert.labels or {}
    if isinstance(labels, dict):
        return labels.get("container") or labels.get("Container") or labels.get("container_name") or None
    return None


def _require_pod_target(investigation: Investigation, where: str) -> Optional[tuple[str, str]]:
    pod = investigation.target.pod
    ns = investigation.target.namespace
    if not pod or not ns or pod == "Unknown" or ns == "Unknown":
        investigation.errors.append(f"{where}: missing pod/namespace target (add pod+namespace labels to alert)")
        return None
    return (pod, ns)


def _apply_k8s_context(investigation: Investigation, pod: str, namespace: str, *, events_limit: int) -> None:
    kctx = gather_pod_context(pod, namespace, events_limit=events_limit)
    investigation.evidence.k8s.pod_info = kctx.get("pod_info")
    investigation.evidence.k8s.pod_conditions = kctx.get("pod_conditions") or []
    investigation.evidence.k8s.pod_events = kctx.get("pod_events") or []
    investigation.evidence.k8s.owner_chain = kctx.get("owner_chain")
    investigation.evidence.k8s.rollout_status = kctx.get("rollout_status")
    for err in kctx.get("errors") or []:
        investigation.errors.append(f"K8s context: {err}")


def collect_pod_baseline(investigation: Investigation, *, events_limit: int = 20) -> None:
    """
    Shared pod-scoped baseline evidence collector (full baseline).

    Best-effort, idempotent:
    - populates K8s context, logs, restarts, cpu/memory usage+limits, and pod phase signal
    - does not overwrite evidence that already exists
    - never raises; appends to investigation.errors
    """
    investigation.target.playbook = investigation.target.playbook or "default"
    target = _require_pod_target(investigation, "pod_baseline")
    if target is None:
        return
    pod, namespace = target
    start_time = investigation.time_window.start_time
    end_time = investigation.time_window.end_time
    container = _container_from_investigation(investigation)

    # K8s context
    if investigation.evidence.k8s.pod_info is None:
        _apply_k8s_context(investigation, pod, namespace, events_limit=events_limit)

    # Metrics baseline
    if investigation.evidence.metrics.pod_phase_signal is None:
        try:
            investigation.evidence.metrics.pod_phase_signal = query_pod_not_healthy(
                namespace, pod, start_time, end_time
            )
        except Exception as e:
            investigation.errors.append(f"Failed to query pod phase signal: {e}")

    if investigation.evidence.metrics.restart_data is None:
        try:
            investigation.evidence.metrics.restart_data = query_pod_restarts(
                namespace, pod, start_time, end_time, container=container
            )
        except Exception as e:
            investigation.errors.append(f"Failed to query restart signal: {e}")

    if investigation.evidence.metrics.cpu_metrics is None:
        try:
            investigation.evidence.metrics.cpu_metrics = query_cpu_usage_and_limits(
                pod, namespace, start_time, end_time, container=container
            )
        except Exception as e:
            investigation.errors.append(f"Failed to query CPU metrics: {e}")

    if investigation.evidence.metrics.memory_metrics is None:
        try:
            investigation.evidence.metrics.memory_metrics = query_memory_usage_and_limits(
                pod_name=pod,
                namespace=namespace,
                start_time=start_time,
                end_time=end_time,
                container=container,
            )
        except Exception as e:
            investigation.errors.append(f"Failed to query memory metrics: {e}")

    # Logs baseline (avoid re-attempting if already attempted)
    if investigation.evidence.logs.logs_status is None and not investigation.evidence.logs.logs:
        try:
            # For crashloop-ish families, 100 lines often captures only startup banners.
            # We parse a larger window and then the report renderer selects an actionable snippet.
            logs_result = fetch_recent_logs(pod, namespace, start_time, end_time, container=container, limit=400)
            investigation.evidence.logs.logs = logs_result.get("entries", [])
            investigation.evidence.logs.logs_status = logs_result.get("status")
            investigation.evidence.logs.logs_reason = logs_result.get("reason")
            investigation.evidence.logs.logs_backend = logs_result.get("backend")
            investigation.evidence.logs.logs_query = logs_result.get("query_used")
        except Exception as e:
            investigation.errors.append(f"Failed to fetch logs: {e}")
            investigation.evidence.logs.logs = []
            investigation.evidence.logs.logs_status = "unavailable"
            investigation.evidence.logs.logs_reason = "unexpected_error"

    # Parse logs for ERROR/FATAL/Exception patterns (universal)
    if investigation.evidence.logs.logs and not investigation.evidence.logs.parsed_errors:
        try:
            parse_result = parse_log_entries(investigation.evidence.logs.logs, limit=50)
            investigation.evidence.logs.parsed_errors = parse_result["parsed_errors"]
            investigation.evidence.logs.parsing_metadata = parse_result["metadata"]
        except Exception as e:
            investigation.errors.append(f"Log parsing failed: {e}")


__all__ = [
    "_container_from_investigation",
    "_require_pod_target",
    "collect_pod_baseline",
]
