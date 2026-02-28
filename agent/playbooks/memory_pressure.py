"""Back-compat wrapper: memory pressure collector now lives in `agent.collectors`."""

from __future__ import annotations

from agent.collectors.memory_pressure import collect_memory_pressure
from agent.core.models import Investigation


def investigate_memory_pressure_playbook(investigation: Investigation) -> None:
    collect_memory_pressure(investigation)


__all__ = ["investigate_memory_pressure_playbook"]
