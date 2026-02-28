"""
Multi-backend logs client supporting VictoriaLogs (LogsQL) and Loki (LogQL).

Auto-detects backend based on LOGS_URL:
- If URL contains "loki" -> uses Loki API (/loki/api/v1/query_range) with LogQL syntax
- Otherwise -> uses VictoriaLogs API (/select/logsql/query) with LogsQL syntax

Override auto-detection with LOGS_BACKEND=loki or LOGS_BACKEND=victorialogs.
"""

from __future__ import annotations

import json
from datetime import datetime
from heapq import heappush, heappushpop
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple, TypedDict, runtime_checkable

import requests

# Default VictoriaLogs base URL (can be overridden via environment variable).
VICTORIALOGS_URL_DEFAULT = "http://localhost:19471"


class LogFetchResult(TypedDict):
    entries: List[Dict[str, Any]]
    status: Literal["ok", "empty", "unavailable"]
    reason: Optional[str]
    backend: Optional[Literal["victorialogs", "loki"]]
    query_used: Optional[str]


@runtime_checkable
class LogsProvider(Protocol):
    def fetch_recent_logs(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 400,
        container: Optional[str] = None,
        use_regex: bool = False,
    ) -> LogFetchResult: ...


class DefaultLogsProvider:
    def fetch_recent_logs(
        self,
        pod_name: str,
        namespace: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 400,
        container: Optional[str] = None,
        use_regex: bool = False,
    ) -> LogFetchResult:
        return fetch_recent_logs(
            pod_name=pod_name,
            namespace=namespace,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            container=container,
            use_regex=use_regex,
        )


def get_logs_provider() -> LogsProvider:
    """Seam for swapping provider implementations later (e.g., MCP-backed)."""
    return DefaultLogsProvider()


def _detect_backend(logs_url: str) -> Literal["loki", "victorialogs"]:
    """
    Auto-detect logs backend based on URL.

    Loki: URL contains 'loki' (e.g., loki-distributed, loki-gateway)
    VictoriaLogs: Default fallback

    Can be overridden with LOGS_BACKEND env var.
    """
    import os

    backend_override = (os.getenv("LOGS_BACKEND") or "").strip().lower()
    if backend_override in ("loki", "victorialogs"):
        return backend_override  # type: ignore[return-value]

    # Auto-detect from URL
    if "loki" in logs_url.lower():
        return "loki"
    return "victorialogs"


def fetch_recent_logs(
    pod_name: str,
    namespace: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 400,
    container: Optional[str] = None,
    use_regex: bool = False,
) -> LogFetchResult:
    """
    Fetch recent logs from VictoriaLogs (LogsQL) or Loki (LogQL) for a pod.

    Auto-detects backend based on LOGS_URL (or use LOGS_BACKEND env var to override).

    Args:
        pod_name: Pod name (or regex pattern if use_regex=True)
        namespace: Kubernetes namespace
        start_time: Start of time window
        end_time: End of time window
        limit: Maximum number of log entries to return
        container: Optional container name filter
        use_regex: If True, treat pod_name as a regex pattern.
                   For Loki: uses =~ operator ({pod=~"pattern"})
                   For VictoriaLogs: uses re() function (pod:re("pattern"))

    Returns:
        Dict with:
          - entries: List of log entries with timestamp/message/labels
          - status: "ok" | "empty" | "unavailable"
          - reason: Optional minimal reason string
          - backend: "victorialogs" | "loki"
          - query_used: the LogSQL/LogQL query attempted (prefer primary query for empty results)
    """
    import os

    logs_url = (os.getenv("LOGS_URL") or "").strip()
    # Allow faster fail-fast behavior in environments where logs backend is slow/unreachable.
    try:
        timeout_s = float((os.getenv("LOGS_TIMEOUT_SECONDS") or "").strip() or "10")
        timeout_s = max(1.0, min(60.0, timeout_s))
    except Exception:
        timeout_s = 10.0

    if not logs_url:
        # Local dev convenience: fall back to the module default when NOT running in-cluster.
        # In-cluster you typically MUST set LOGS_URL explicitly.
        in_cluster = bool((os.getenv("KUBERNETES_SERVICE_HOST") or "").strip())
        if not in_cluster:
            logs_url = (VICTORIALOGS_URL_DEFAULT or "").strip()
        if not logs_url:
            return {
                "entries": [],
                "status": "unavailable",
                "reason": "not_configured",
                "backend": None,
                "query_used": None,
            }

    # Detect backend
    backend = _detect_backend(logs_url)

    if backend == "loki":
        return _fetch_from_loki(
            logs_url=logs_url,
            pod_name=pod_name,
            namespace=namespace,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            container=container,
            timeout_s=timeout_s,
            use_regex=use_regex,
        )
    else:
        return _fetch_from_victorialogs(
            logs_url=logs_url,
            pod_name=pod_name,
            namespace=namespace,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            container=container,
            timeout_s=timeout_s,
            use_regex=use_regex,
        )


def _fetch_from_loki(
    logs_url: str,
    pod_name: str,
    namespace: str,
    start_time: datetime,
    end_time: datetime,
    limit: int,
    container: Optional[str],
    timeout_s: float,
    use_regex: bool = False,
) -> LogFetchResult:
    """
    Loki implementation (LogQL syntax).

    Loki API: /loki/api/v1/query_range
    Query syntax: {namespace="default", pod="my-pod"}
    Time: Unix nanoseconds
    """

    def _labels_to_logql(labels: Dict[str, str], regex_fields: Optional[set] = None) -> str:
        """
        Convert dict to LogQL label selector: {k="v", k2="v2"}

        Args:
            labels: Label filters
            regex_fields: Set of field names to use regex matching (=~) instead of exact (=)
        """
        if not labels:
            return "{}"
        regex_fields = regex_fields or set()
        parts = []
        for k, v in labels.items():
            if not k or not v:
                continue
            if k in regex_fields:
                # Use regex matching: pod=~"pattern"
                parts.append(f'{k}=~"{v}"')
            else:
                # Use exact matching: pod="exact"
                parts.append(f'{k}="{v}"')
        return "{" + ", ".join(parts) + "}"

    def _parse_loki_response(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse Loki JSON response structure:
        {
          "data": {
            "result": [
              {
                "stream": {"namespace": "...", "pod": "..."},
                "values": [["timestamp_ns", "log line"], ...]
              }
            ]
          }
        }
        """
        entries = []
        try:
            results = data.get("data", {}).get("result", [])
            for result in results:
                if not isinstance(result, dict):
                    continue
                stream = result.get("stream", {})
                values = result.get("values", [])

                for value in values:
                    if not isinstance(value, list) or len(value) < 2:
                        continue
                    timestamp_ns, message = value[0], value[1]

                    # Parse timestamp (Unix nanoseconds)
                    try:
                        ts = datetime.fromtimestamp(int(timestamp_ns) / 1e9)
                    except (ValueError, TypeError, OSError):
                        ts = start_time

                    # Extract labels (support both standard and k8s_ prefixed variants)
                    labels = {}
                    # Map alternative label names to standard names
                    label_mappings = {
                        "pod": ["pod", "k8s_pod", "pod_name"],
                        "namespace": ["namespace", "k8s_namespace"],
                        "container": ["container"],
                        "app": ["app"],
                        "job": ["job"],
                        "stream": ["stream"],
                        "node_name": ["node_name"],
                    }
                    for standard_name, variants in label_mappings.items():
                        for variant in variants:
                            if variant in stream:
                                labels[standard_name] = stream[variant]
                                break  # Use first match

                    entries.append(
                        {
                            "timestamp": ts,
                            "message": str(message),
                            "labels": labels,
                        }
                    )
        except Exception:
            return []

        # Sort by timestamp and limit
        entries.sort(key=lambda x: x["timestamp"])
        return entries[-limit:] if len(entries) > limit else entries

    def _try_loki(logql_query: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Query Loki API."""
        url = f"{logs_url}/loki/api/v1/query_range"
        q = (logql_query or "").strip()
        if not q:
            return [], None

        # Convert to Unix nanoseconds
        try:
            start_ns = int(start_time.timestamp() * 1e9)
            end_ns = int(end_time.timestamp() * 1e9)
        except Exception:
            return None, "time_conversion_error"

        params = {
            "query": q,
            "start": str(start_ns),
            "end": str(end_ns),
            "limit": str(limit),
            "direction": "backward",  # Most recent first
        }

        try:
            resp = requests.get(url, params=params, timeout=timeout_s)
            resp.raise_for_status()
            data = resp.json()
            logs = _parse_loki_response(data)
            return logs, None
        except requests.exceptions.Timeout:
            return None, "timeout"
        except requests.exceptions.HTTPError:
            return None, "http_error"
        except requests.exceptions.RequestException:
            return None, "connection_error"
        except Exception:
            return None, "unexpected_error"

    # Try different label combinations for Loki
    # Use same fallback strategy as VictoriaLogs to handle different scrape config label names
    attempts: List[Dict[str, str]] = []
    primary = {"namespace": namespace, "pod": pod_name}
    fallback_k8s = {"k8s_namespace": namespace, "k8s_pod": pod_name}
    fallback_pod_name = {"namespace": namespace, "pod_name": pod_name}

    # Primary: standard Kubernetes labels
    if container:
        attempts.append({**primary, "container": container})
    attempts.append(primary)

    # Fallback 1: k8s_ prefixed labels (common in Loki scrape configs)
    if container:
        attempts.append({**fallback_k8s, "container": container})
    attempts.append(fallback_k8s)

    # Fallback 2: pod_name label (some scrape configs use this)
    if container:
        attempts.append({**fallback_pod_name, "container": container})
    attempts.append(fallback_pod_name)

    first_query: Optional[str] = None
    last_query: Optional[str] = None

    # Determine which fields should use regex matching
    regex_fields = set()
    if use_regex:
        # Use regex for pod-related fields
        regex_fields = {"pod", "k8s_pod", "pod_name"}

    for labels in attempts:
        logql = _labels_to_logql(labels, regex_fields=regex_fields)
        if first_query is None:
            first_query = logql
        last_query = logql

        logs, reason = _try_loki(logql)
        if logs is None:
            return {
                "entries": [],
                "status": "unavailable",
                "reason": reason or "unexpected_error",
                "backend": "loki",
                "query_used": logql,
            }
        if logs:
            return {
                "entries": logs,
                "status": "ok",
                "reason": "ok",
                "backend": "loki",
                "query_used": logql,
            }

    return {
        "entries": [],
        "status": "empty",
        "reason": "empty",
        "backend": "loki",
        "query_used": first_query or last_query,
    }


def _fetch_from_victorialogs(
    logs_url: str,
    pod_name: str,
    namespace: str,
    start_time: datetime,
    end_time: datetime,
    limit: int,
    container: Optional[str],
    timeout_s: float,
    use_regex: bool = False,
) -> LogFetchResult:
    """VictoriaLogs implementation (LogsQL syntax)."""

    def _parse_vmlogs_ndjson(text: str) -> List[Dict[str, Any]]:
        """
        VictoriaLogs commonly returns NDJSON (one JSON object per line).
        Each line is a log entry dict containing keys like _time, _msg and arbitrary fields.
        """
        # IMPORTANT:
        # We must treat `limit` as "keep the most recent N entries in the window" (tail), not
        # "stop after parsing N lines". Otherwise we can miss late-window errors.
        newest: List[tuple[float, int, Dict[str, Any]]] = []
        seq = 0
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue

            timestamp = entry.get("_time", entry.get("time", ""))
            # Prefer VictoriaLogs `_msg`, then common alternates.
            message = None
            for k in ("_msg", "message", "msg", "log", "text"):
                if k in entry and entry.get(k) is not None:
                    message = entry.get(k)
                    break
            if message is None:
                message = str(entry)

            # Keep a small subset of metadata. Avoid exploding the report with all fields.
            labels = {}
            for k in ("pod", "namespace", "container", "app", "job", "stream", "node_name", "_stream", "_stream_id"):
                if k in entry:
                    labels[k] = entry.get(k)

            try:
                if isinstance(timestamp, (int, float)):
                    ts = datetime.fromtimestamp(timestamp / 1e9 if timestamp > 1e12 else timestamp)
                else:
                    # Use dateutil when available to handle nanosecond timestamps.
                    try:
                        from dateutil import parser as date_parser  # type: ignore

                        ts = date_parser.isoparse(str(timestamp))
                    except Exception:
                        ts = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            except (ValueError, TypeError, OSError):
                ts = start_time

            try:
                ts_key = float(getattr(ts, "timestamp")())
            except Exception:
                ts_key = 0.0
            seq += 1
            item = {"timestamp": ts, "message": message, "labels": labels}
            if len(newest) < limit:
                heappush(newest, (ts_key, seq, item))
            else:
                heappushpop(newest, (ts_key, seq, item))

        # Sort ascending by timestamp so downstream tail selection is stable.
        return [t[2] for t in sorted(newest, key=lambda x: (x[0], x[1]))]

    def _escape_logsql_value(v: str) -> str:
        # Minimal escaping for quotes and backslashes in LogSQL string values.
        return (v or "").replace("\\", "\\\\").replace('"', '\\"')

    def _labels_to_logsql(labels: Dict[str, str], regex_fields: Optional[set] = None) -> str:
        """
        Convert a dict of field matchers into a LogSQL expression:
          k:"v" AND k2:"v2"

        Args:
            labels: Label filters
            regex_fields: Set of field names to use regex matching (re()) instead of exact match
        """
        regex_fields = regex_fields or set()
        parts: List[str] = []
        for k, v in labels.items():
            if not k or not v:
                continue
            if k in regex_fields:
                # Use regex matching: pod:re("pattern")
                parts.append(f'{k}:re("{_escape_logsql_value(v)}")')
            else:
                # Use exact matching: pod:"exact"
                parts.append(f'{k}:"{_escape_logsql_value(v)}"')
        return " AND ".join(parts)

    def _try_vmlogs(logsql_query: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """
        VictoriaLogs LogSQL query.

        IMPORTANT:
        - Prefer field-based LogSQL (pod:"x" AND namespace:"y")
        - VictoriaLogs often returns NDJSON; do NOT assume resp.json() works.
        - Time-bound via HTTP `start`/`end` params (RFC3339 Z).
        """
        url = f"{logs_url}/select/logsql/query"
        q = (logsql_query or "").strip()
        if not q:
            return [], None

        def _to_rfc3339_z(dt: datetime) -> str:
            try:
                dt_utc = dt.astimezone(datetime.UTC)  # type: ignore[attr-defined]
            except Exception:
                from datetime import timezone

                dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")

        start_param = _to_rfc3339_z(start_time)
        end_param = _to_rfc3339_z(end_time)

        try:
            resp = requests.get(url, params={"query": q, "start": start_param, "end": end_param}, timeout=timeout_s)
            resp.raise_for_status()

            # NDJSON (most common)
            text = resp.text or ""
            logs = _parse_vmlogs_ndjson(text)
            if logs:
                return logs, None

            # Best-effort JSON fallback for other response shapes.
            try:
                data = resp.json()
            except Exception:
                return [], None
            if isinstance(data, list):
                try:
                    text2 = "\n".join(json.dumps(x) for x in data if isinstance(x, dict))
                    return _parse_vmlogs_ndjson(text2), None
                except Exception:
                    return [], None
            return [], None
        except requests.exceptions.Timeout:
            return None, "timeout"
        except requests.exceptions.HTTPError:
            return None, "http_error"
        except requests.exceptions.RequestException:
            return None, "connection_error"
        except Exception:
            return None, "unexpected_error"

    # VictoriaLogs-only: attempt a small set of LogSQL field conventions.
    # Primary convention is the known-working one: namespace/pod.
    attempts: List[Dict[str, str]] = []
    primary = {"namespace": namespace, "pod": pod_name}
    fallback = {"k8s_namespace": namespace, "k8s_pod": pod_name}

    if container:
        attempts.append({**primary, "container": container})
    attempts.append(primary)
    if container:
        attempts.append({**fallback, "container": container})
    attempts.append(fallback)

    first_query: Optional[str] = None
    last_query: Optional[str] = None
    last_reason: Optional[str] = None

    # Determine which fields should use regex matching
    regex_fields = set()
    if use_regex:
        # Use regex for pod-related fields
        regex_fields = {"pod", "k8s_pod"}

    for labels in attempts:
        logsql = _labels_to_logsql(labels, regex_fields=regex_fields)
        if first_query is None:
            first_query = logsql
        last_query = logsql

        logs, reason = _try_vmlogs(logsql)
        if logs is None:
            return {
                "entries": [],
                "status": "unavailable",
                "reason": reason or "unexpected_error",
                "backend": "victorialogs",
                "query_used": logsql,
            }
        if logs:
            return {"entries": logs, "status": "ok", "reason": "ok", "backend": "victorialogs", "query_used": logsql}

        # Empty: if container was part of the query, retry without container before moving on.
        if container and "container" in labels:
            labels_wo = dict(labels)
            labels_wo.pop("container", None)
            logsql_wo = _labels_to_logsql(labels_wo)
            last_query = logsql_wo
            logs2, reason2 = _try_vmlogs(logsql_wo)
            if logs2 is None:
                return {
                    "entries": [],
                    "status": "unavailable",
                    "reason": reason2 or "unexpected_error",
                    "backend": "victorialogs",
                    "query_used": logsql_wo,
                }
            if logs2:
                return {
                    "entries": logs2,
                    "status": "ok",
                    "reason": "ok",
                    "backend": "victorialogs",
                    "query_used": logsql_wo,
                }

        last_reason = "empty"

    return {
        "entries": [],
        "status": "empty",
        "reason": last_reason,
        "backend": "victorialogs",
        "query_used": first_query or last_query,
    }
