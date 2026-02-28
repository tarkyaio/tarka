from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from agent.core.targets import extract_target_container
from agent.pipeline.families import detect_family

# Families where pod labels are commonly scrape metadata (avoid treating pod as incident identity).
_POD_IDENTITY_EXCLUDED_FAMILIES: set[str] = {
    "target_down",
    "k8s_rollout_health",
    "observability_pipeline",
    "meta",
    "job_failed",  # KubeJobFailed alerts have incorrect pod label (kube-state-metrics scraper)
}


def utcnow() -> datetime:
    """Seam for tests."""
    return datetime.now(timezone.utc)


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(v).strip()
    except Exception:
        return ""


def detect_family_for_labels(labels: Dict[str, Any]) -> str:
    # `detect_family` is tolerant of missing keys; we pass playbook=None in webhook contexts.
    if not isinstance(labels, dict):
        return "generic"
    try:
        return detect_family(labels, playbook=None)
    except Exception:
        return "generic"


def compute_utc_bucket_start(*, now: datetime, hours: int = 4) -> datetime:
    """
    Floor `now` to the start of its UTC `hours`-sized bucket.

    Example (hours=4): 2026-01-02T07:59Z -> 2026-01-02T04:00Z
    """
    if hours <= 0:
        raise ValueError("hours must be > 0")
    if now.tzinfo is None:
        # Treat naive values as UTC to keep behavior deterministic.
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    bucket_hour = (now_utc.hour // hours) * hours
    return now_utc.replace(hour=bucket_hour, minute=0, second=0, microsecond=0)


def format_bucket_label(bucket_start_utc: datetime) -> str:
    """
    Format bucket start as YYYYMMDDHH (UTC).
    """
    if bucket_start_utc.tzinfo is None:
        bucket_start_utc = bucket_start_utc.replace(tzinfo=timezone.utc)
    t = bucket_start_utc.astimezone(timezone.utc)
    return t.strftime("%Y%m%d%H")


def compute_utc_hour_bucket_label(*, now: datetime) -> str:
    """
    UTC hour bucket label (YYYYMMDDHH).
    """
    return format_bucket_label(compute_utc_bucket_start(now=now, hours=1))


def compute_queue_msg_id_for_workload_hour(
    *,
    workload_key: str,
    hour_bucket: str,
) -> str:
    """
    Stable msg-id for JetStream dedupe: workload identity + hour bucket.
    """
    wk = (workload_key or "").strip() or "unknown"
    hb = (hour_bucket or "").strip() or "unknown"
    raw = f"{wk}:{hb}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _extract_pod_namespace(labels: Dict[str, Any]) -> Tuple[str, str]:
    """
    Conservative extraction: only use explicit pod/namespace labels; never infer.
    Mirrors `agent.providers.alertmanager_provider.extract_pod_info_from_alert` behavior.
    """
    pod = (
        labels.get("pod")
        or labels.get("pod_name")
        or labels.get("podName")
        or labels.get("kubernetes_pod_name")
        or None
    )
    namespace = (
        labels.get("namespace")
        or labels.get("Namespace")
        or labels.get("kubernetes_namespace_name")
        or labels.get("k8s_namespace")
        or labels.get("kube_namespace")
        or None
    )
    return _safe_str(pod), _safe_str(namespace)


def _extract_service(labels: Dict[str, Any]) -> str:
    return _safe_str(labels.get("service") or labels.get("kubernetes_service_name"))


def compute_dedup_key(
    *,
    alertname: str,
    labels: Dict[str, Any],
    fingerprint: str,
    now: datetime,
    env_cluster: Optional[str] = None,
    bucket_hours: int = 4,
) -> str:
    """
    Compute a stable dedup key for an alert instance.

    Rules:
    - Always includes `alertname` + detected `family`.
    - Uses a fixed UTC time bucket (default: 4h).
    - Identity priority:
      1) job identity (cluster, namespace, job_name) for job_failed family when job_name+namespace exist
      2) pod identity (cluster, namespace, pod) when pod+namespace labels exist AND family not excluded
      3) service identity (cluster, service) when service label exists
      4) fallback identity (fingerprint)
    - Cluster comes from labels['cluster'] else env CLUSTER_NAME (caller passes env_cluster).
    """
    a = (alertname or "").strip() or "Unknown"
    labs = labels if isinstance(labels, dict) else {}
    fp = (fingerprint or "").strip()

    family = detect_family_for_labels(labs)

    cluster = _safe_str(labs.get("cluster")) or _safe_str(env_cluster) or "unknown"

    bucket_start = compute_utc_bucket_start(now=now, hours=bucket_hours)
    bucket = format_bucket_label(bucket_start)

    pod, namespace = _extract_pod_namespace(labs)
    service = _extract_service(labs)

    kind = "fingerprint"
    identity: Dict[str, str] = {"fingerprint": fp or "unknown"}

    # Special case: job_failed family uses job_name for identity (KubeJobFailed alerts)
    # Only use this path if job_name and namespace are available.
    job_name = _safe_str(labs.get("job_name")) if family == "job_failed" else ""
    if job_name and namespace and family == "job_failed":
        kind = "job"
        identity = {"cluster": cluster, "namespace": namespace, "job_name": job_name}
    elif pod and namespace and family not in _POD_IDENTITY_EXCLUDED_FAMILIES:
        kind = "pod"
        identity = {"cluster": cluster, "namespace": namespace, "pod": pod}
    elif service:
        kind = "service"
        identity = {"cluster": cluster, "service": service}

    payload = {
        "v": 1,
        "bucket_hours": int(bucket_hours),
        "bucket": bucket,
        "alertname": a,
        "family": family,
        "kind": kind,
        "identity": identity,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_rollout_workload_key(
    *,
    alertname: str,
    labels: Dict[str, Any],
    owner_chain: Dict[str, Any],
    env_cluster: Optional[str] = None,
    include_container: bool = False,
) -> Optional[str]:
    """
    Compute a stable workload-level key from a K8s owner chain.

    This is intended for rollout-noisy alerts where pod names/fingerprints churn but the owning
    controller (Deployment/StatefulSet/etc.) represents the incident scope.

    Returns a sha256 hex string, or None if workload identity is unavailable.
    """
    if not isinstance(owner_chain, dict):
        return None
    wl = owner_chain.get("workload")
    if not isinstance(wl, dict):
        return None
    wk = (wl.get("kind") or "").strip()
    wn = (wl.get("name") or "").strip()
    if not wk or not wn:
        return None

    labs = labels if isinstance(labels, dict) else {}
    family = detect_family_for_labels(labs)
    cluster = _safe_str(labs.get("cluster")) or _safe_str(env_cluster) or "unknown"
    namespace = _safe_str(labs.get("namespace") or labs.get("Namespace")) or "unknown"

    container = None
    if include_container:
        container = extract_target_container(labs)
        container = (container or "").strip() or None

    payload = {
        "v": 1,
        "scope": "workload",
        "alertname": (alertname or "").strip() or "Unknown",
        "family": family,
        "cluster": cluster,
        "namespace": namespace,
        "workload_kind": wk,
        "workload_name": wn,
        "container": container,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
