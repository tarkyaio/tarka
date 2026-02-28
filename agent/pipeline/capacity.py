"""Capacity / rightsizing report (Prometheus-only, read-only).

This module produces pragmatic signals:
- requests vs usage (CPU cores, memory bytes)
- top over/under-provisioned pod/container pairs

It is best-effort and designed to work in many environments using common cAdvisor + kube-state-metrics.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from agent.core.models import CapacityReport, Investigation
from agent.providers.prom_provider import query_prometheus_instant


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _label_key(metric: Dict[str, Any]) -> Tuple[str, str]:
    return (str(metric.get("pod") or ""), str(metric.get("container") or ""))


def _vector_to_map(vec: List[Dict[str, Any]]) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = {}
    for r in vec or []:
        metric = r.get("metric") or {}
        value = r.get("value")
        if not isinstance(metric, dict) or not (isinstance(value, list) and len(value) >= 2):
            continue
        v = _to_float(value[1])
        if v is None:
            continue
        out[_label_key(metric)] = float(v)
    return out


def _pod_regex_for_workload(kind: Optional[str], name: Optional[str]) -> Optional[str]:
    if not kind or not name:
        return None
    k = str(kind)
    n = str(name)
    if k == "StatefulSet":
        return f"^{n}-[0-9]+$"
    if k in ("Deployment", "ReplicaSet", "Job", "DaemonSet"):
        return f"^{n}-.*"
    return None


def _millicores(cores: float) -> int:
    return int(round(float(cores) * 1000.0))


def _fmt_millicores(cores: float) -> str:
    return f"{_millicores(cores)}m"


def _round_down_millicores(cores: float, *, step_m: int = 5) -> float:
    """Round down to nearest step in millicores (e.g., 5m) and return cores."""
    m = max(0, int(float(cores) * 1000.0))
    if step_m <= 1:
        return m / 1000.0
    m = (m // step_m) * step_m
    return m / 1000.0


def _percentile(vals: List[float], p: float) -> Optional[float]:
    if not vals:
        return None
    if p <= 0:
        return min(vals)
    if p >= 1:
        return max(vals)
    s = sorted(vals)
    idx = int(round((len(s) - 1) * p))
    idx = max(0, min(len(s) - 1, idx))
    return float(s[idx])


def _series_values(series: List[Dict[str, Any]]) -> List[float]:
    out: List[float] = []
    for s in series or []:
        values = s.get("values") or []
        for pair in values:
            if not (isinstance(pair, list) and len(pair) >= 2):
                continue
            v = _to_float(pair[1])
            if v is None:
                continue
            out.append(float(v))
    return out


def _maybe_rightsize_cpu_request(
    *,
    pod: str,
    container: str,
    cpu_request_cores: float,
    usage_p95_cores: float,
    ratio: float,
) -> Optional[Dict[str, Any]]:
    """
    Deterministic, conservative rightsizing recommendation.

    Trigger only when extremely over-requested:
    - usage/request < 0.1
    - request >= 0.1 cores (100m)
    """
    if cpu_request_cores <= 0:
        return None
    if cpu_request_cores < 0.1:
        return None
    if ratio >= 0.1:
        return None

    # Suggest a small but safer envelope above observed usage.
    # Lower bound ~6x p95 (min 20m), upper bound ~15x p95 (min 50m), capped at current request.
    low = max(0.02, usage_p95_cores * 6.0)
    high = max(0.05, usage_p95_cores * 15.0)
    high = min(high, cpu_request_cores)
    low = min(low, high)
    # Make the range human-friendly (millicore steps).
    low = _round_down_millicores(low, step_m=5)
    high = _round_down_millicores(high, step_m=5)
    low = min(low, high)

    return {
        "pod": pod,
        "container": container,
        "cpu_request_cores": cpu_request_cores,
        "cpu_usage_p95_cores": usage_p95_cores,
        "usage_request_ratio": ratio,
        "suggested_request_low_cores": low,
        "suggested_request_high_cores": high,
        "recommendation": (
            f"CPU request {_fmt_millicores(cpu_request_cores)} vs p95 usage ~{_fmt_millicores(usage_p95_cores)} "
            f"(usage/request≈{ratio:.3f}) → consider lowering request (e.g. {_fmt_millicores(low)}–{_fmt_millicores(high)}) if safe."
        ),
    }


def build_capacity_report_for_investigation(
    investigation: Investigation,
    *,
    end_time: datetime,
    top_n: int = 10,
) -> Dict[str, Any]:
    """
    Build a capacity report scoped to the owning workload when possible, else to the incident pod.
    """
    namespace = investigation.target.namespace or ""
    pod_name = investigation.target.pod or ""
    rollout_status = investigation.evidence.k8s.rollout_status or {}

    if not namespace:
        return {"status": "skipped", "reason": "missing_namespace"}

    wl_kind = rollout_status.get("kind") if isinstance(rollout_status, dict) else None
    wl_name = rollout_status.get("name") if isinstance(rollout_status, dict) else None
    pod_re = _pod_regex_for_workload(wl_kind, wl_name) or (f"^{pod_name}$" if pod_name else None)

    pod_matcher = f',pod=~"{pod_re}"' if pod_re else ""
    ns_matcher = f'namespace="{namespace}"'

    # CPU usage (cores) and requests (cores)
    q_cpu_usage = (
        f'sum by(pod,container) (rate(container_cpu_usage_seconds_total{{{ns_matcher}{pod_matcher},image!=""}}[5m]))'
    )
    q_cpu_req = (
        f'sum by(pod,container) (kube_pod_container_resource_requests{{{ns_matcher}{pod_matcher},resource="cpu"}})'
    )

    # Memory usage (bytes) and requests (bytes) - try unit=byte variant first
    q_mem_usage = f'sum by(pod,container) (container_memory_working_set_bytes{{{ns_matcher}{pod_matcher},image!=""}})'
    q_mem_req_candidates = [
        f'sum by(pod,container) (kube_pod_container_resource_requests{{{ns_matcher}{pod_matcher},resource="memory",unit="byte"}})',
        f'sum by(pod,container) (kube_pod_container_resource_requests{{{ns_matcher}{pod_matcher},resource="memory"}})',
    ]

    try:
        cpu_usage_vec = query_prometheus_instant(q_cpu_usage, at=end_time)
        cpu_req_vec = query_prometheus_instant(q_cpu_req, at=end_time)
        mem_usage_vec = query_prometheus_instant(q_mem_usage, at=end_time)

        mem_req_vec: List[Dict[str, Any]] = []
        mem_req_query_used: Optional[str] = None
        for q in q_mem_req_candidates:
            try:
                mem_req_vec = query_prometheus_instant(q, at=end_time)
                mem_req_query_used = q
                if mem_req_vec:
                    break
            except Exception:
                continue

        cpu_usage = _vector_to_map(cpu_usage_vec)
        cpu_req = _vector_to_map(cpu_req_vec)
        mem_usage = _vector_to_map(mem_usage_vec)
        mem_req = _vector_to_map(mem_req_vec)

        keys = set(cpu_usage.keys()) | set(cpu_req.keys()) | set(mem_usage.keys()) | set(mem_req.keys())

        rows = []
        for pod, container in sorted(keys):
            if not pod or not container or container == "POD":
                continue
            u_cpu = cpu_usage.get((pod, container), 0.0)
            r_cpu = cpu_req.get((pod, container), 0.0)
            u_mem = mem_usage.get((pod, container), 0.0)
            r_mem = mem_req.get((pod, container), 0.0)
            rows.append(
                {
                    "pod": pod,
                    "container": container,
                    "cpu_usage_cores": u_cpu,
                    "cpu_request_cores": r_cpu,
                    "cpu_over_request_cores": u_cpu - r_cpu,
                    "mem_usage_bytes": u_mem,
                    "mem_request_bytes": r_mem,
                    "mem_over_request_bytes": u_mem - r_mem,
                }
            )

        # Filter to avoid the same row showing up in both lists when the dataset is small.
        cpu_over_rows = [r for r in rows if (r.get("cpu_over_request_cores") or 0.0) > 0.0]
        cpu_under_rows = [r for r in rows if (r.get("cpu_over_request_cores") or 0.0) < 0.0]
        mem_over_rows = [r for r in rows if (r.get("mem_over_request_bytes") or 0.0) > 0.0]
        mem_under_rows = [r for r in rows if (r.get("mem_over_request_bytes") or 0.0) < 0.0]

        cpu_over = sorted(cpu_over_rows, key=lambda r: r.get("cpu_over_request_cores", 0.0), reverse=True)[:top_n]
        cpu_under = sorted(cpu_under_rows, key=lambda r: r.get("cpu_over_request_cores", 0.0))[:top_n]
        mem_over = sorted(mem_over_rows, key=lambda r: r.get("mem_over_request_bytes", 0.0), reverse=True)[:top_n]
        mem_under = sorted(mem_under_rows, key=lambda r: r.get("mem_over_request_bytes", 0.0))[:top_n]

        recommendations: List[str] = []
        rightsizing_cpu: List[Dict[str, Any]] = []

        # Deterministic rightsizing recommendation for the *incident pod* only.
        # We first use instant ratio as a cheap filter; only if extreme do we run a range query
        # to compute p95 usage over the investigation window.
        incident_pod = investigation.target.pod or ""
        if incident_pod:
            # Look at all containers for the incident pod from the instant maps.
            incident_keys = [k for k in keys if k[0] == incident_pod]
            for pod, container in incident_keys:
                if not container or container == "POD":
                    continue
                r_cpu = cpu_req.get((pod, container), 0.0)
                u_cpu_inst = cpu_usage.get((pod, container), 0.0)
                if r_cpu <= 0:
                    continue
                ratio_inst = u_cpu_inst / r_cpu if r_cpu > 0 else 1.0
                # Cheap gate: only proceed if already extremely under-utilized.
                if r_cpu >= 0.1 and ratio_inst < 0.1:
                    try:
                        # Range query to compute p95 usage over the window.
                        from agent.providers.prom_provider import query_cpu_usage_and_limits

                        cpu = query_cpu_usage_and_limits(
                            pod_name=pod,
                            namespace=namespace,
                            start_time=investigation.time_window.start_time,
                            end_time=investigation.time_window.end_time,
                            container=container,
                        )
                        series = (cpu.get("cpu_usage") if isinstance(cpu, dict) else None) or []
                        vals = _series_values(series if isinstance(series, list) else [])
                        usage_p95 = _percentile(vals, 0.95)
                        if usage_p95 is None:
                            continue
                        ratio = usage_p95 / r_cpu if r_cpu > 0 else 1.0
                        rr = _maybe_rightsize_cpu_request(
                            pod=pod,
                            container=container,
                            cpu_request_cores=float(r_cpu),
                            usage_p95_cores=float(usage_p95),
                            ratio=float(ratio),
                        )
                        if rr:
                            rightsizing_cpu.append({k: v for k, v in rr.items() if k != "recommendation"})
                            recommendations.append(str(rr["recommendation"]))
                    except Exception:
                        # Rightsizing is best-effort; never fail the capacity report.
                        pass

        return {
            "status": "ok",
            "scope": {
                "namespace": namespace,
                "workload_kind": wl_kind,
                "workload_name": wl_name,
                "pod_regex": pod_re,
            },
            "queries_used": {
                "cpu_usage": q_cpu_usage,
                "cpu_requests": q_cpu_req,
                "mem_usage": q_mem_usage,
                "mem_requests": mem_req_query_used,
            },
            "recommendations": recommendations,
            "rightsizing_cpu": rightsizing_cpu or None,
            "top_cpu_over_request": cpu_over,
            "top_cpu_under_request": cpu_under,
            "top_mem_over_request": mem_over,
            "top_mem_under_request": mem_under,
        }
    except Exception as e:
        return {"status": "unavailable", "error": str(e)}


def analyze_capacity(investigation: Investigation, *, top_n: int = 10) -> None:
    """Populate investigation.analysis.capacity (never raises)."""
    try:
        raw = build_capacity_report_for_investigation(
            investigation, end_time=investigation.time_window.end_time, top_n=top_n
        )
        investigation.analysis.capacity = (
            CapacityReport(**raw) if isinstance(raw, dict) else CapacityReport(status="unavailable")
        )
    except Exception as e:
        investigation.errors.append(f"Capacity: {e}")
        return
