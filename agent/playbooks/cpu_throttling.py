"""Back-compat wrapper: CPU throttling collector now lives in `agent.collectors`."""

from __future__ import annotations

from agent.collectors.cpu_throttling import collect_cpu_throttling
from agent.core.models import Investigation


def investigate_cpu_throttling_playbook(investigation: Investigation) -> None:
    collect_cpu_throttling(investigation)


__all__ = ["investigate_cpu_throttling_playbook"]
