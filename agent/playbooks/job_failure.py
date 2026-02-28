"""Job failure playbook."""

from __future__ import annotations

from agent.collectors.job_failure import collect_job_failure_evidence
from agent.core.models import Investigation


def investigate_job_failure_playbook(investigation: Investigation) -> None:
    """Playbook for KubeJobFailed alerts.

    Routes to dedicated Job failure collector which handles TTL-deleted pods.
    """
    collect_job_failure_evidence(investigation)


__all__ = ["investigate_job_failure_playbook"]
