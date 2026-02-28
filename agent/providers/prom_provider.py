"""Prometheus client for querying CPU throttling and usage metrics."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import requests

# Default Prometheus URL (can be overridden via environment variable)
PROMETHEUS_URL = "http://localhost:18481/select/0/prometheus"


@runtime_checkable
class PromProvider(Protocol):
    def query_prometheus_instant(self, query: str, at: datetime) -> List[Dict[str, Any]]: ...

    def query_cpu_throttling(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def query_cpu_usage_and_limits(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def query_memory_usage_and_limits(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def query_pod_restarts(
        self,
        namespace: str,
        pod_name: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def query_pod_not_healthy(
        self,
        namespace: str,
        pod_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]: ...

    def query_http_5xx_generic(
        self,
        *,
        labels: Dict[str, Any],
        start_time: datetime,
        end_time: datetime,
    ) -> Dict[str, Any]: ...


class DefaultPromProvider:
    def query_prometheus_instant(self, query: str, at: datetime) -> List[Dict[str, Any]]:
        return query_prometheus_instant(query, at)

    def query_cpu_throttling(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]:
        return query_cpu_throttling(pod_name, namespace, start_time, end_time, container=container)

    def query_cpu_usage_and_limits(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]:
        return query_cpu_usage_and_limits(pod_name, namespace, start_time, end_time, container=container)

    def query_memory_usage_and_limits(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]:
        return query_memory_usage_and_limits(
            pod_name=pod_name,
            namespace=namespace,
            start_time=start_time,
            end_time=end_time,
            container=container,
        )

    def query_pod_restarts(
        self,
        namespace: str,
        pod_name: str,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
    ) -> Dict[str, Any]:
        return query_pod_restarts(namespace, pod_name, start_time, end_time, container=container)

    def query_pod_not_healthy(
        self, namespace: str, pod_name: str, start_time: datetime, end_time: datetime
    ) -> Dict[str, Any]:
        return query_pod_not_healthy(namespace, pod_name, start_time, end_time)

    def query_http_5xx_generic(
        self, *, labels: Dict[str, Any], start_time: datetime, end_time: datetime
    ) -> Dict[str, Any]:
        return query_http_5xx_generic(labels=labels, start_time=start_time, end_time=end_time)


def get_prom_provider() -> PromProvider:
    """Seam for swapping provider implementations later (e.g., MCP-backed)."""
    return DefaultPromProvider()


def _query_prometheus(query: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """
    Execute a PromQL query against Prometheus.

    Args:
        query: PromQL query string
        start: Start time for range query
        end: End time for range query

    Returns:
        List of time series results
    """
    import os

    prometheus_url = os.getenv("PROMETHEUS_URL", PROMETHEUS_URL)

    # Convert datetime to Unix timestamps
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    # Use range query
    url = f"{prometheus_url}/api/v1/query_range"
    params = {"query": query, "start": start_ts, "end": end_ts, "step": "30s"}  # 30 second resolution

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data["status"] != "success":
            raise Exception(f"Prometheus query failed: {data.get('error', 'Unknown error')}")

        results = []
        for result in data["data"]["result"]:
            results.append({"metric": result["metric"], "values": result["values"]})  # List of [timestamp, value] pairs

        return results

    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to query Prometheus: {str(e)}")


def _query_prometheus_instant(query: str, at: datetime) -> List[Dict[str, Any]]:
    """
    Execute an instant PromQL query against Prometheus.

    Returns a list of vector results:
      [{ "metric": {...}, "value": [ts, value] }, ...]
    """
    import os

    prometheus_url = os.getenv("PROMETHEUS_URL", PROMETHEUS_URL)
    url = f"{prometheus_url}/api/v1/query"
    params = {
        "query": query,
        "time": int(at.timestamp()),
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data["status"] != "success":
            raise Exception(f"Prometheus query failed: {data.get('error', 'Unknown error')}")

        result = (data.get("data") or {}).get("result") or []
        out: List[Dict[str, Any]] = []
        for r in result:
            out.append({"metric": r.get("metric", {}) or {}, "value": r.get("value")})
        return out
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to query Prometheus: {str(e)}")


def query_prometheus_instant(query: str, at: datetime) -> List[Dict[str, Any]]:
    """
    Public wrapper for instant Prometheus queries.

    This exists so other modules don't rely on private helpers.
    """
    return _query_prometheus_instant(query, at)


def query_cpu_throttling(
    pod_name: str,
    namespace: str,
    start_time: datetime,
    end_time: datetime,
    container: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query CPU throttling metrics for a pod/container.

    Matches the CPUThrottlingHigh alert logic (periods-based throttling %):

      100 *
        sum by(container,pod,namespace)(increase(container_cpu_cfs_throttled_periods_total{...}[5m])) /
        sum by(container,pod,namespace)(increase(container_cpu_cfs_periods_total{...}[5m]))

    Args:
        pod_name: Name of the pod
        namespace: Kubernetes namespace
        start_time: Start time for the query
        end_time: End time for the query
        container: Optional container name to scope the query (recommended for alert instance investigation)

    Returns:
        Dictionary containing throttling metrics
    """
    # Build cAdvisor label filter. If container is provided, we scope to it to avoid noise/unknown series.
    label_parts = [f'pod="{pod_name}"', f'namespace="{namespace}"', 'image!=""']
    if container:
        label_parts.append(f'container="{container}"')
    else:
        # Best-effort filter to avoid infra series
        label_parts.append('container!="POD"')
        label_parts.append('container!=""')
    labels = ",".join(label_parts)

    throttled_periods_query = (
        f"sum by(container,pod,namespace) (" f"increase(container_cpu_cfs_throttled_periods_total{{{labels}}}[5m])" f")"
    )

    total_periods_query = (
        f"sum by(container,pod,namespace) (" f"increase(container_cpu_cfs_periods_total{{{labels}}}[5m])" f")"
    )

    throttling_percentage_query = f"100 * {throttled_periods_query} / clamp_min({total_periods_query}, 1)"

    try:
        throttling_percentage = _query_prometheus(throttling_percentage_query, start_time, end_time)
        throttled_periods = _query_prometheus(throttled_periods_query, start_time, end_time)
        total_periods = _query_prometheus(total_periods_query, start_time, end_time)

        return {
            "throttling_percentage": throttling_percentage,
            "throttled_periods": throttled_periods,
            "total_periods": total_periods,
            "query_used": throttling_percentage_query,
        }
    except Exception as e:
        return {
            "error": str(e),
            "throttling_percentage": [],
            "throttled_periods": [],
            "total_periods": [],
        }


def query_cpu_usage_and_limits(
    pod_name: str,
    namespace: str,
    start_time: datetime,
    end_time: datetime,
    container: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query CPU usage and limits for a pod/container.

    Example PromQL queries:
    - container_cpu_usage_seconds_total: Total CPU usage
    - rate(container_cpu_usage_seconds_total[5m]): CPU usage rate
    - kube_pod_container_resource_limits: CPU limits

    Args:
        pod_name: Name of the pod
        namespace: Kubernetes namespace
        start_time: Start time for the query
        end_time: End time for the query
        container: Optional container name to scope the query (recommended for alert instance investigation)

    Returns:
        Dictionary containing CPU usage and limit metrics
    """
    # Build cAdvisor label filter. If container is provided, scope to it and aggregate away per-CPU series.
    cadvisor_label_parts = [f'pod="{pod_name}"', f'namespace="{namespace}"', 'image!=""']
    if container:
        cadvisor_label_parts.append(f'container="{container}"')
    else:
        cadvisor_label_parts.append('container!="POD"')
        cadvisor_label_parts.append('container!=""')
    cadvisor_labels = ",".join(cadvisor_label_parts)

    # CPU usage rate (CPU cores used). Sum to avoid duplicate per-CPU/extra-dim series.
    cpu_usage_query = (
        f"sum by(container,pod,namespace) (" f"rate(container_cpu_usage_seconds_total{{{cadvisor_labels}}}[5m])" f")"
    )

    # CPU limits (from kube-state-metrics)
    ksm_label_parts = [f'pod="{pod_name}"', f'namespace="{namespace}"', 'resource="cpu"']
    if container:
        ksm_label_parts.append(f'container="{container}"')
    ksm_labels = ",".join(ksm_label_parts)

    cpu_limit_query = f"max by(container,pod,namespace) (kube_pod_container_resource_limits{{{ksm_labels}}})"

    # CPU requests (from kube-state-metrics)
    cpu_request_query = f"max by(container,pod,namespace) (kube_pod_container_resource_requests{{{ksm_labels}}})"

    try:
        cpu_usage = _query_prometheus(cpu_usage_query, start_time, end_time)
        cpu_limits = _query_prometheus(cpu_limit_query, start_time, end_time)
        cpu_requests = _query_prometheus(cpu_request_query, start_time, end_time)

        return {
            "cpu_usage": cpu_usage,
            "cpu_limits": cpu_limits,
            "cpu_requests": cpu_requests,
            "queries_used": {"usage": cpu_usage_query, "limits": cpu_limit_query, "requests": cpu_request_query},
        }
    except Exception as e:
        return {"error": str(e), "cpu_usage": [], "cpu_limits": [], "cpu_requests": []}


def query_pod_not_healthy(
    namespace: str,
    pod_name: str,
    start_time: datetime,
    end_time: datetime,
) -> Dict[str, Any]:
    """
    Reproduce the KubernetesPodNotHealthy/KubernetesPodNotHealthyCritical signal.

    Alert query:
      kube_pod_status_phase{phase=~'Pending|Unknown|Failed'} > 0

    We scope it to a specific namespace+pod (alert instance).
    """
    query = (
        f"max by(pod, namespace, phase) ("
        f"kube_pod_status_phase{{"
        f'namespace="{namespace}",'
        f'pod="{pod_name}",'
        f'phase=~"Pending|Unknown|Failed"'
        f"}}"
        f") > 0"
    )

    try:
        series = _query_prometheus(query, start_time, end_time)
        return {
            "pod_phase_signal": series,
            "query_used": query,
        }
    except Exception as e:
        return {
            "error": str(e),
            "pod_phase_signal": [],
            "query_used": query,
        }


def query_http_5xx_generic(labels: Dict[str, Any], start_time: datetime, end_time: datetime) -> Dict[str, Any]:
    """
    Best-effort HTTP 5xx rate query.

    Since environments differ, this tries a small set of common metrics/label conventions and returns
    the first query that produces any series.

    Returns:
      { "series": [...], "query_used": str|None }
    """
    namespace = labels.get("namespace") or labels.get("kubernetes_namespace_name")
    pod = labels.get("pod") or labels.get("pod_name") or labels.get("kubernetes_pod_name")
    container = labels.get("container") or labels.get("container_name")
    service = labels.get("service") or labels.get("kubernetes_service_name")
    app = labels.get("app") or labels.get("app_kubernetes_io_name")

    selector_parts = []
    if namespace:
        selector_parts.append(f'namespace="{namespace}"')
    if pod:
        selector_parts.append(f'pod="{pod}"')
    if container:
        selector_parts.append(f'container="{container}"')
    if service:
        selector_parts.append(f'service="{service}"')
    if app:
        selector_parts.append(f'app="{app}"')

    def _selector(extra_matchers: Optional[List[str]] = None) -> str:
        parts = list(selector_parts)
        if extra_matchers:
            parts.extend(extra_matchers)
        inner = ",".join(parts)
        return f"{{{inner}}}" if inner else "{}"

    sel_status = _selector(['status=~"5.."'])
    sel_resp = _selector(['response_code=~"5.."'])

    candidates = [
        # Generic client/server HTTP metrics
        f"sum(rate(http_requests_total{sel_status}[5m]))",
        f"sum(rate(http_server_requests_seconds_count{sel_status}[5m]))",
        # NGINX ingress controller
        f"sum(rate(nginx_ingress_controller_requests{sel_status}[5m]))",
        # Istio
        f"sum(rate(istio_requests_total{sel_resp}[5m]))",
    ]

    for q in candidates:
        try:
            series = _query_prometheus(q, start_time, end_time)
            if series:
                return {"series": series, "query_used": q}
        except Exception:
            # If one candidate fails, try the next.
            continue

    return {"series": [], "query_used": None}


def query_pod_restarts(
    namespace: str,
    pod_name: str,
    start_time: datetime,
    end_time: datetime,
    container: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Best-effort restart signal for a pod/container.

    Uses kube-state-metrics:
      increase(kube_pod_container_status_restarts_total[5m])

    This returns a time series of "restarts observed in each 5m window" across the investigation window.
    """
    label_parts = [f'namespace="{namespace}"', f'pod="{pod_name}"']
    if container:
        label_parts.append(f'container="{container}"')
    labels = ",".join(label_parts)

    query = (
        f"sum by(container,pod,namespace) (" f"increase(kube_pod_container_status_restarts_total{{{labels}}}[5m])" f")"
    )

    try:
        series = _query_prometheus(query, start_time, end_time)
        return {"restart_increase_5m": series, "query_used": query}
    except Exception as e:
        return {"error": str(e), "restart_increase_5m": [], "query_used": query}


def query_memory_usage_and_limits(
    pod_name: str,
    namespace: str,
    start_time: datetime,
    end_time: datetime,
    container: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query memory usage and limits/requests for a pod/container (best-effort).

    Usage: container_memory_working_set_bytes (cAdvisor).
    Limits/requests: kube_pod_container_resource_limits/requests (kube-state-metrics).
    """
    cadvisor_label_parts = [f'pod="{pod_name}"', f'namespace="{namespace}"', 'image!=""']
    if container:
        cadvisor_label_parts.append(f'container="{container}"')
    else:
        cadvisor_label_parts.append('container!="POD"')
        cadvisor_label_parts.append('container!=""')
    cadvisor_labels = ",".join(cadvisor_label_parts)

    mem_usage_query = (
        f"sum by(container,pod,namespace) (" f"container_memory_working_set_bytes{{{cadvisor_labels}}}" f")"
    )

    # kube-state-metrics label conventions vary (some include unit="byte"). Try with and without.
    ksm_base = [f'pod="{pod_name}"', f'namespace="{namespace}"', 'resource="memory"']
    if container:
        ksm_base.append(f'container="{container}"')
    ksm_labels_no_unit = ",".join(ksm_base)
    ksm_labels_with_unit = ",".join(ksm_base + ['unit="byte"'])

    mem_limit_query_candidates = [
        f"max by(container,pod,namespace) (kube_pod_container_resource_limits{{{ksm_labels_with_unit}}})",
        f"max by(container,pod,namespace) (kube_pod_container_resource_limits{{{ksm_labels_no_unit}}})",
    ]
    mem_request_query_candidates = [
        f"max by(container,pod,namespace) (kube_pod_container_resource_requests{{{ksm_labels_with_unit}}})",
        f"max by(container,pod,namespace) (kube_pod_container_resource_requests{{{ksm_labels_no_unit}}})",
    ]

    try:
        mem_usage = _query_prometheus(mem_usage_query, start_time, end_time)

        mem_limits = []
        limit_query_used = None
        for q in mem_limit_query_candidates:
            try:
                mem_limits = _query_prometheus(q, start_time, end_time)
                limit_query_used = q
                # If the backend returns empty, try next candidate.
                if mem_limits:
                    break
            except Exception:
                continue

        mem_requests = []
        request_query_used = None
        for q in mem_request_query_candidates:
            try:
                mem_requests = _query_prometheus(q, start_time, end_time)
                request_query_used = q
                if mem_requests:
                    break
            except Exception:
                continue

        return {
            "memory_usage_bytes": mem_usage,
            "memory_limits_bytes": mem_limits,
            "memory_requests_bytes": mem_requests,
            "queries_used": {
                "usage": mem_usage_query,
                "limits": limit_query_used,
                "requests": request_query_used,
            },
        }
    except Exception as e:
        return {
            "error": str(e),
            "memory_usage_bytes": [],
            "memory_limits_bytes": [],
            "memory_requests_bytes": [],
        }
