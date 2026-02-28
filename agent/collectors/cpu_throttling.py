"""CPU throttling evidence collector."""

from __future__ import annotations

from agent.collectors.pod_baseline import _container_from_investigation, _require_pod_target, collect_pod_baseline
from agent.core.models import Investigation
from agent.providers.prom_provider import query_cpu_throttling


def collect_cpu_throttling(investigation: Investigation) -> None:
    investigation.target.playbook = "cpu_throttling"
    target = _require_pod_target(investigation, "cpu_throttling")
    if target is None:
        return
    pod, namespace = target
    start_time = investigation.time_window.start_time
    end_time = investigation.time_window.end_time

    collect_pod_baseline(investigation, events_limit=20)
    container = _container_from_investigation(investigation)

    try:
        investigation.evidence.metrics.throttling_data = query_cpu_throttling(
            pod, namespace, start_time, end_time, container=container
        )
    except Exception as e:
        investigation.errors.append(f"Failed to query throttling: {e}")


__all__ = ["collect_cpu_throttling"]
