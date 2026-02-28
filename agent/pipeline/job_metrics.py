"""Compute job-specific metrics for Evidence display."""

from agent.core.models import Investigation


def compute_job_metrics(investigation: Investigation):
    """Compute job-specific metrics for Evidence display.

    This populates features.job_metrics which the UI can display
    instead of generic CPU/HTTP metrics for job_failed alerts.
    """
    f = investigation.analysis.features
    if not f or f.family != "job_failed":
        return

    # Create job_metrics dict
    job_metrics = {}

    # Exit code
    if f.k8s.container_last_terminated_top:
        term = f.k8s.container_last_terminated_top[0]
        if term.exit_code is not None:
            job_metrics["exit_code"] = term.exit_code
        if term.reason:
            job_metrics["exit_reason"] = term.reason

    # Attempts (from rollout_status)
    rs = investigation.evidence.k8s.rollout_status or {}
    if isinstance(rs, dict):
        failed = rs.get("failed") or 0
        active = rs.get("active") or 0
        if failed or active:
            job_metrics["attempts"] = failed + active

        spec = rs.get("spec", {})
        if isinstance(spec, dict):
            backoff_limit = spec.get("backoffLimit")
            if backoff_limit is not None:
                job_metrics["backoff_limit"] = backoff_limit

    # Service account
    pod_info = investigation.evidence.k8s.pod_info
    if isinstance(pod_info, dict):
        sa_name = pod_info.get("service_account_name")
        if sa_name:
            job_metrics["service_account"] = sa_name

    # Error summary
    if f.logs.error_hits:
        job_metrics["error_count"] = f.logs.error_hits

    # Store in features
    if job_metrics:
        f.job_metrics = job_metrics
