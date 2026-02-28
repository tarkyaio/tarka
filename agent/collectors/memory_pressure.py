"""Memory pressure evidence collector."""

from __future__ import annotations

from agent.collectors.pod_baseline import _require_pod_target, collect_pod_baseline
from agent.core.models import Investigation


def collect_memory_pressure(investigation: Investigation) -> None:
    investigation.target.playbook = "memory_pressure"
    target = _require_pod_target(investigation, "memory_pressure")
    if target is None:
        return
    collect_pod_baseline(investigation, events_limit=20)


__all__ = ["collect_memory_pressure"]
