"""Crashloop playbook."""

from __future__ import annotations

from agent.collectors.crashloop import collect_crashloop_evidence
from agent.core.models import Investigation


def investigate_crashloop_playbook(investigation: Investigation) -> None:
    """Playbook for KubePodCrashLooping alerts.

    Routes to dedicated crashloop collector which gathers previous container logs,
    probe failure detection, and crash timing analysis.
    """
    collect_crashloop_evidence(investigation)


__all__ = ["investigate_crashloop_playbook"]
