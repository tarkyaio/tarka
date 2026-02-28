"""Target parsing helpers.

Alert labels often include scrape/job metadata (e.g. kube-state-metrics) that should NOT be treated
as the incident target container. These helpers implement conservative heuristics so investigations
don't accidentally over-filter metrics/logs.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def extract_target_container(labels: Dict[str, Any]) -> Optional[str]:
    """
    Best-effort extraction of the *target* container name from alert labels.

    Heuristics:
    - Ignore scrape-side container labels like `kube-state-metrics` (commonly present on KSM-driven alerts)
    - Ignore empty/POD pseudo-container values
    """
    if not isinstance(labels, dict):
        return None

    raw = labels.get("container") or labels.get("Container") or labels.get("container_name")
    c = str(raw).strip() if raw is not None else ""
    if not c:
        return None

    # Common pseudo-container values / scrape metadata
    lower = c.lower()
    if lower in ("pod",):
        return None

    job = labels.get("job")
    job_s = str(job).strip().lower() if job is not None else ""

    # For kube-state-metrics-driven alerts, `container=kube-state-metrics` is scrape metadata, not target.
    if lower == "kube-state-metrics" and job_s == "kube-state-metrics":
        return None

    return c


def should_ignore_pod_label_for_jobs(labels: Dict[str, Any]) -> bool:
    """Check if this is a Job alert where pod label is incorrect.

    KubeJobFailed alerts have pod=<kube-state-metrics-pod> which is wrong.
    The actual Job name is in job_name label.
    The job_failure collector will find the correct pod using job-name label selector.
    """
    if not isinstance(labels, dict):
        return False

    alertname = str(labels.get("alertname", "")).lower()
    has_job_name = "job_name" in labels

    # KubeJobFailed/JobFailed alerts should ignore pod label
    return alertname in ("kubejobfailed", "jobfailed") and has_job_name
