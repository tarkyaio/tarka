"""Back-compat wrapper: non-pod baseline collector now lives in `agent.collectors`."""

from __future__ import annotations

from agent.collectors.nonpod_baseline import collect_nonpod_baseline
from agent.core.models import Investigation


def nonpod_baseline_playbook(investigation: Investigation) -> None:
    collect_nonpod_baseline(investigation)


__all__ = ["nonpod_baseline_playbook"]
