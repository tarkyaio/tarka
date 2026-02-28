"""Back-compat wrapper: pod-not-healthy collector now lives in `agent.collectors`."""

from __future__ import annotations

from agent.collectors.pod_not_healthy import collect_pod_not_healthy
from agent.core.models import Investigation


def investigate_pod_not_healthy_playbook(investigation: Investigation) -> None:
    collect_pod_not_healthy(investigation)


__all__ = ["investigate_pod_not_healthy_playbook"]
