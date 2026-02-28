"""Back-compat wrapper: pod baseline collector now lives in `agent.collectors`."""

from __future__ import annotations

from agent.collectors.pod_baseline import (
    _container_from_investigation,
    _require_pod_target,
    collect_pod_baseline,
)
from agent.core.models import Investigation


def pod_baseline_playbook(investigation: Investigation, *, events_limit: int = 20) -> None:
    collect_pod_baseline(investigation, events_limit=events_limit)


__all__ = ["_container_from_investigation", "_require_pod_target", "pod_baseline_playbook"]
