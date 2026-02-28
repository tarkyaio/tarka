"""Back-compat wrapper: OOM killer collector now lives in `agent.collectors`."""

from __future__ import annotations

from agent.collectors.oom_killer import collect_oom_killer
from agent.core.models import Investigation


def investigate_oom_killer_playbook(investigation: Investigation) -> None:
    collect_oom_killer(investigation)


__all__ = ["investigate_oom_killer_playbook"]
