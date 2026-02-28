"""HTTP 5xx evidence collector."""

from __future__ import annotations

from agent.collectors.pod_baseline import _require_pod_target, collect_pod_baseline
from agent.core.models import Investigation
from agent.providers.prom_provider import query_http_5xx_generic


def collect_http_5xx(investigation: Investigation) -> None:
    investigation.target.playbook = "http_5xx"
    start_time = investigation.time_window.start_time
    end_time = investigation.time_window.end_time

    # Optional K8s context if this is also pod-scoped.
    target = _require_pod_target(investigation, "http_5xx")
    if target is not None:
        collect_pod_baseline(investigation, events_limit=20)

    try:
        labels = investigation.alert.labels or {}
        investigation.evidence.metrics.http_5xx = query_http_5xx_generic(
            labels=labels, start_time=start_time, end_time=end_time
        )
    except Exception as e:
        investigation.errors.append(f"Failed to query http 5xx metrics: {e}")
        investigation.evidence.metrics.http_5xx = {"error": str(e), "series": [], "query_used": None}


__all__ = ["collect_http_5xx"]
