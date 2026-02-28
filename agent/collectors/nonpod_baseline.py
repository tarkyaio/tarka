"""Shared non-pod baseline evidence collector."""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent.core.models import Investigation
from agent.providers.k8s_provider import get_workload_rollout_status
from agent.providers.prom_provider import query_prometheus_instant


def _infer_workload_from_labels(labels: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Best-effort workload identity inference for non-pod alerts.

    Returns:
      {"kind": <Kubernetes Kind>, "name": <workload name>}
    """
    if not isinstance(labels, dict):
        return None
    # Common conventions
    candidates = [
        ("Deployment", ["deployment", "deployment_name", "kubernetes_deployment"]),
        ("StatefulSet", ["statefulset", "statefulset_name", "kubernetes_statefulset"]),
        ("DaemonSet", ["daemonset", "daemonset_name", "kubernetes_daemonset"]),
        # IMPORTANT: do NOT treat the ubiquitous `job=` label as a Kubernetes Job; it is typically scrape metadata.
        ("Job", ["job_name", "kubernetes_job"]),
    ]
    for kind, keys in candidates:
        for k in keys:
            v = labels.get(k)
            if isinstance(v, str) and v.strip():
                return {"kind": kind, "name": v.strip()}

    # Explicit kind/name pairs
    wk = labels.get("workload_kind") or labels.get("k8s_workload_kind") or labels.get("kind")
    wn = labels.get("workload") or labels.get("workload_name") or labels.get("k8s_workload_name") or labels.get("name")
    if isinstance(wk, str) and isinstance(wn, str) and wk.strip() and wn.strip():
        mapping = {
            "deployment": "Deployment",
            "statefulset": "StatefulSet",
            "daemonset": "DaemonSet",
            "job": "Job",
        }
        wk_norm = mapping.get(wk.strip().lower(), wk.strip())
        return {"kind": wk_norm, "name": wn.strip()}

    return None


def _prom_instant_scalar(v: object) -> Optional[float]:
    """
    Best-effort scalar extraction from `query_prometheus_instant()` result:
      [{"metric": {...}, "value": [ts, "123"]}]
    """
    if not isinstance(v, list) or not v:
        return None
    first = v[0]
    if not isinstance(first, dict):
        return None
    val = first.get("value")
    if not isinstance(val, list) or len(val) != 2:
        return None
    try:
        return float(val[1])
    except Exception:
        return None


def _rollout_status_from_kube_state_metrics(*, namespace: str, kind: str, name: str, at) -> Optional[Dict[str, Any]]:
    """
    Fallback rollout/status summary derived from kube-state-metrics metrics (PromQL instant).
    """
    kind_norm = (kind or "").strip()
    if not namespace or not kind_norm or not name:
        return None

    def q(query: str) -> Optional[float]:
        try:
            return _prom_instant_scalar(query_prometheus_instant(query, at))
        except Exception:
            return None

    if kind_norm == "Deployment":
        replicas = q(f'kube_deployment_status_replicas{{namespace="{namespace}",deployment="{name}"}}')
        ready = q(f'kube_deployment_status_replicas_ready{{namespace="{namespace}",deployment="{name}"}}')
        updated = q(f'kube_deployment_status_replicas_updated{{namespace="{namespace}",deployment="{name}"}}')
        unavailable = q(f'kube_deployment_status_replicas_unavailable{{namespace="{namespace}",deployment="{name}"}}')
        observed = q(f'kube_deployment_status_observed_generation{{namespace="{namespace}",deployment="{name}"}}')
        return {
            "kind": "Deployment",
            "name": name,
            "replicas": int(replicas) if replicas is not None else None,
            "ready_replicas": int(ready) if ready is not None else None,
            "updated_replicas": int(updated) if updated is not None else None,
            "unavailable_replicas": int(unavailable) if unavailable is not None else None,
            "observed_generation": int(observed) if observed is not None else None,
            "source": "kube_state_metrics",
        }

    if kind_norm == "StatefulSet":
        replicas = q(f'kube_statefulset_status_replicas{{namespace="{namespace}",statefulset="{name}"}}')
        ready = q(f'kube_statefulset_status_replicas_ready{{namespace="{namespace}",statefulset="{name}"}}')
        current = q(f'kube_statefulset_status_replicas_current{{namespace="{namespace}",statefulset="{name}"}}')
        updated = q(f'kube_statefulset_status_replicas_updated{{namespace="{namespace}",statefulset="{name}"}}')
        return {
            "kind": "StatefulSet",
            "name": name,
            "replicas": int(replicas) if replicas is not None else None,
            "ready_replicas": int(ready) if ready is not None else None,
            "current_replicas": int(current) if current is not None else None,
            "updated_replicas": int(updated) if updated is not None else None,
            "source": "kube_state_metrics",
        }

    if kind_norm == "DaemonSet":
        desired = q(f'kube_daemonset_status_desired_number_scheduled{{namespace="{namespace}",daemonset="{name}"}}')
        ready = q(f'kube_daemonset_status_number_ready{{namespace="{namespace}",daemonset="{name}"}}')
        updated = q(f'kube_daemonset_status_updated_number_scheduled{{namespace="{namespace}",daemonset="{name}"}}')
        return {
            "kind": "DaemonSet",
            "name": name,
            "desired_number_scheduled": int(desired) if desired is not None else None,
            "number_ready": int(ready) if ready is not None else None,
            "updated_number_scheduled": int(updated) if updated is not None else None,
            "source": "kube_state_metrics",
        }

    if kind_norm == "Job":
        failed = q(f'kube_job_status_failed{{namespace="{namespace}",job_name="{name}"}}')
        active = q(f'kube_job_status_active{{namespace="{namespace}",job_name="{name}"}}')
        succeeded = q(f'kube_job_status_succeeded{{namespace="{namespace}",job_name="{name}"}}')
        return {
            "kind": "Job",
            "name": name,
            "failed": int(failed) if failed is not None else None,
            "active": int(active) if active is not None else None,
            "succeeded": int(succeeded) if succeeded is not None else None,
            "source": "kube_state_metrics",
        }

    return None


def collect_nonpod_baseline(investigation: Investigation) -> None:
    """
    Shared non-pod baseline evidence collector.

    Best-effort, idempotent:
    - Prometheus instant checks derived from alert labels (job/instance/service)
    - Read-only Kubernetes rollout status when workload identity exists
    - Never assumes pod/namespace exist; never emits “missing pod target” errors
    """
    investigation.target.playbook = investigation.target.playbook or "nonpod_baseline"
    labels = investigation.alert.labels or {}
    if not isinstance(labels, dict):
        labels = {}

    # Workload identity (best-effort)
    if not (investigation.target.workload_kind and investigation.target.workload_name):
        wl = _infer_workload_from_labels(labels)
        if wl:
            investigation.target.workload_kind = wl.get("kind")
            investigation.target.workload_name = wl.get("name")
            investigation.target.target_type = "workload"

    # Kubernetes rollout status (read-only) when we have namespace + workload identity
    if investigation.evidence.k8s.rollout_status is None:
        ns = investigation.target.namespace or (
            labels.get("namespace") if isinstance(labels.get("namespace"), str) else None
        )
        wk = investigation.target.workload_kind
        wn = investigation.target.workload_name
        if ns and wk and wn:
            try:
                investigation.evidence.k8s.rollout_status = get_workload_rollout_status(namespace=ns, kind=wk, name=wn)
            except Exception as e:
                investigation.errors.append(f"K8s rollout status: {e}")
                # Fallback: derive a minimal rollout status from kube-state-metrics metrics via PromQL
                try:
                    rs = _rollout_status_from_kube_state_metrics(
                        namespace=ns, kind=wk, name=wn, at=investigation.time_window.end_time
                    )
                    if rs:
                        investigation.evidence.k8s.rollout_status = rs
                except Exception:
                    pass

    # Prometheus baseline (instant; safe, label-derived)
    at = investigation.time_window.end_time
    job = investigation.target.job or (labels.get("job") if isinstance(labels.get("job"), str) else None)
    instance = investigation.target.instance or (
        labels.get("instance") if isinstance(labels.get("instance"), str) else None
    )
    service = investigation.target.service or (
        labels.get("service") if isinstance(labels.get("service"), str) else None
    )
    namespace = investigation.target.namespace or (
        labels.get("namespace") if isinstance(labels.get("namespace"), str) else None
    )

    prom_baseline: Dict[str, Any] = (
        (investigation.evidence.metrics.prom_baseline or {})
        if hasattr(investigation.evidence.metrics, "prom_baseline")
        else {}
    )
    queries_used: Dict[str, str] = prom_baseline.get("queries_used") or {}

    def _q(name: str, qstr: str) -> None:
        if not qstr or name in prom_baseline:
            return
        try:
            prom_baseline[name] = query_prometheus_instant(qstr, at)
            queries_used[name] = qstr
        except Exception as e:
            investigation.errors.append(f"Prometheus baseline ({name}) failed: {e}")
            prom_baseline[name] = []
            queries_used[name] = qstr

    if job and instance:
        _q("up_job_instance", f'up{{job="{job}",instance="{instance}"}}')
    elif job:
        _q("up_job_down", f'sum(up{{job="{job}"}} == 0)')
        _q("up_job_total", f'count(up{{job="{job}"}})')

    if namespace and service:
        _q("up_service_down", f'sum(up{{namespace="{namespace}",service="{service}"}} == 0)')
        _q("up_service_total", f'count(up{{namespace="{namespace}",service="{service}"}})')

    prom_baseline["queries_used"] = queries_used
    # MetricsEvidence is allow-extra; attach baseline as an extra field
    investigation.evidence.metrics.prom_baseline = prom_baseline  # type: ignore[attr-defined]


__all__ = ["collect_nonpod_baseline"]
