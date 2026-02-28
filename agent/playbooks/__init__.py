"""Playbook router + registry.

Contract:
- playbooks mutate an Investigation in-place (populate evidence + errors)
- playbooks never raise (best-effort); they append errors to `investigation.errors`
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from agent.core.models import Investigation

PlaybookFunc = Callable[[Investigation], None]

# Playbook registry - maps alert patterns to investigation functions
PLAYBOOKS: Dict[str, PlaybookFunc] = {}


def register_playbook(alert_pattern: str, playbook_func: PlaybookFunc) -> None:
    """Register a playbook function for a specific alert pattern."""
    PLAYBOOKS[alert_pattern] = playbook_func


def get_playbook_for_alert(alertname: str) -> Optional[PlaybookFunc]:
    """Get the appropriate playbook function for an alert."""
    if alertname in PLAYBOOKS:
        return PLAYBOOKS[alertname]

    # Pattern matching (simple substring for now)
    for pattern, playbook in PLAYBOOKS.items():
        if pattern in alertname or alertname in pattern:
            return playbook

    return PLAYBOOKS.get("default")


from .baseline_nonpod import nonpod_baseline_playbook  # noqa: E402

# Import playbooks (definition only; registration happens below).
from .baseline_pod import pod_baseline_playbook  # noqa: E402
from .cpu_throttling import investigate_cpu_throttling_playbook  # noqa: E402
from .crashloop import investigate_crashloop_playbook  # noqa: E402
from .http_5xx import investigate_http_5xx_playbook  # noqa: E402
from .job_failure import investigate_job_failure_playbook  # noqa: E402
from .memory_pressure import investigate_memory_pressure_playbook  # noqa: E402
from .oom_killer import investigate_oom_killer_playbook  # noqa: E402
from .pod_not_healthy import investigate_pod_not_healthy_playbook  # noqa: E402


def default_playbook(investigation: Investigation) -> None:
    investigation.target.playbook = "default"
    pod_baseline_playbook(investigation, events_limit=20)


# Register playbooks
register_playbook("CPUThrottlingHigh", investigate_cpu_throttling_playbook)
register_playbook("KubePodCPUThrottling", investigate_cpu_throttling_playbook)
register_playbook("cpu_throttling", investigate_cpu_throttling_playbook)
register_playbook("CPUThrottling", investigate_cpu_throttling_playbook)
register_playbook("ContainerCpuThrottled", investigate_cpu_throttling_playbook)
register_playbook("KubernetesContainerOomKiller", investigate_oom_killer_playbook)
register_playbook("Http5xxRateHigh", investigate_http_5xx_playbook)
register_playbook("Http5xxRateWarning", investigate_http_5xx_playbook)
register_playbook("KubePodCrashLooping", investigate_crashloop_playbook)
register_playbook("KubePodCrashLoopingStrict", investigate_crashloop_playbook)
register_playbook("KubeJobFailed", investigate_job_failure_playbook)
register_playbook("JobFailed", investigate_job_failure_playbook)
register_playbook("MemoryPressure", investigate_memory_pressure_playbook)
register_playbook("KubernetesPodNotHealthy", investigate_pod_not_healthy_playbook)
register_playbook("KubernetesPodNotHealthyCritical", investigate_pod_not_healthy_playbook)
register_playbook("default", default_playbook)


__all__ = [
    "PlaybookFunc",
    "PLAYBOOKS",
    "default_playbook",
    "get_playbook_for_alert",
    "nonpod_baseline_playbook",
    "pod_baseline_playbook",
    "register_playbook",
]
