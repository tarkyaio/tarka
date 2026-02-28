"""Feature extraction (compute once, reuse everywhere).

This module computes domain-grouped features from the investigation's evidence/analysis.
No rendering, no scoring, no external I/O.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.core.family import get_family
from agent.core.models import (
    DerivedFeatures,
    FeaturesChanges,
    FeaturesK8s,
    FeaturesLogs,
    FeaturesMetrics,
    FeaturesQuality,
    Investigation,
    K8sConditionSummary,
    K8sContainerLastTerminated,
    K8sContainerWaiting,
    K8sEventSummary,
)
from agent.pipeline.families import detect_family


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    values_sorted = sorted(values)
    idx = int((len(values_sorted) - 1) * p)
    return values_sorted[idx]


def _parse_iso_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # handle RFC3339 'Z'
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _truncate(s: Optional[str], n: int = 200) -> Optional[str]:
    if s is None:
        return None
    txt = str(s)
    if len(txt) <= n:
        return txt
    return txt[: max(0, n - 1)] + "…"


def _dig(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _series_values(series: List[Dict[str, Any]], *, container: Optional[str] = None) -> List[float]:
    out: List[float] = []
    for s in series or []:
        metric = s.get("metric", {}) or {}
        c = metric.get("container")
        if container and c and c != container:
            continue
        for _, v in s.get("values") or []:
            fv = _to_float(v)
            if fv is not None:
                out.append(fv)
    return out


def _detect_family(investigation: Investigation) -> str:
    # Prefer canonical family set by the pipeline (prevents drift between collectors/modules/scoring).
    fam = get_family(investigation, default="")
    if fam:
        return fam
    # Back-compat: many unit tests construct investigations directly without setting meta.
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    return detect_family(labels, investigation.target.playbook)


def _k8s_features(investigation: Investigation) -> FeaturesK8s:
    pod_info = investigation.evidence.k8s.pod_info if isinstance(investigation.evidence.k8s.pod_info, dict) else {}
    phase = pod_info.get("phase") if isinstance(pod_info, dict) else None
    status_reason = pod_info.get("status_reason") if isinstance(pod_info, dict) else None
    status_message = _truncate(pod_info.get("status_message") if isinstance(pod_info, dict) else None, 200)

    # Ready from conditions if present.
    ready = None
    for c in investigation.evidence.k8s.pod_conditions or []:
        if isinstance(c, dict) and c.get("type") == "Ready":
            ready = True if c.get("status") == "True" else False
            break

    # Conditions: keep only non-True ones (compact root-cause signal)
    not_ready_conditions: List[K8sConditionSummary] = []
    for c in investigation.evidence.k8s.pod_conditions or []:
        if not isinstance(c, dict):
            continue
        ctype = str(c.get("type") or "").strip()
        cstatus = str(c.get("status") or "").strip()
        if not ctype or not cstatus:
            continue
        if cstatus == "True":
            continue
        not_ready_conditions.append(
            K8sConditionSummary(
                type=ctype,
                status=cstatus,
                reason=(str(c.get("reason")) if c.get("reason") is not None else None),
            )
        )
    not_ready_conditions = sorted(not_ready_conditions, key=lambda x: x.type)

    waiting_reason = None
    restart_count = None
    statuses = pod_info.get("container_statuses") if isinstance(pod_info, dict) else None

    # Multi-container root-cause summaries
    waiting_summaries: List[K8sContainerWaiting] = []
    last_term_summaries: List[K8sContainerLastTerminated] = []

    if isinstance(statuses, list):
        for cs in statuses:
            if not isinstance(cs, dict):
                continue
            # Prefer the target container if known.
            if investigation.target.container and cs.get("name") != investigation.target.container:
                continue
            rc = cs.get("restart_count")
            if isinstance(rc, int):
                restart_count = rc
            state = cs.get("state") or {}
            if isinstance(state, dict) and state.get("waiting"):
                w = state.get("waiting") or {}
                waiting_reason = w.get("reason")
            # Collect waiting reason/message (do not stop; multi-container)
            cname = str(cs.get("name") or "").strip()
            w = _dig(cs, "state", "waiting")
            if cname and isinstance(w, dict):
                waiting_summaries.append(
                    K8sContainerWaiting(
                        container=cname,
                        reason=(str(w.get("reason")) if w.get("reason") is not None else None),
                        message=_truncate(w.get("message") if w.get("message") is not None else None, 200),
                    )
                )

            # Collect last termination signal (prefer last_state.terminated, fallback to state.terminated)
            t = _dig(cs, "last_state", "terminated")
            if not isinstance(t, dict):
                t = _dig(cs, "state", "terminated")
            if cname and isinstance(t, dict):
                exit_code = t.get("exit_code")
                if exit_code is None:
                    exit_code = t.get("exitCode")
                try:
                    exit_code_i = int(exit_code) if exit_code is not None else None
                except Exception:
                    exit_code_i = None
                last_term_summaries.append(
                    K8sContainerLastTerminated(
                        container=cname,
                        reason=(str(t.get("reason")) if t.get("reason") is not None else None),
                        exit_code=exit_code_i,
                    )
                )

    # Rank waiting reasons by usefulness (lower is higher priority)
    waiting_priority = {
        "ImagePullBackOff": 0,
        "ErrImagePull": 1,
        "CreateContainerConfigError": 2,
        "CreateContainerError": 3,
        "CrashLoopBackOff": 4,
        "RunContainerError": 5,
        "ContainerCreating": 20,
    }
    waiting_summaries = sorted(
        waiting_summaries,
        key=lambda w: (waiting_priority.get(str(w.reason or ""), 100), w.container),
    )[:3]

    term_priority = {
        "OOMKilled": 0,
        "Error": 1,
        "Completed": 50,
    }
    last_term_summaries = sorted(
        last_term_summaries,
        key=lambda t: (term_priority.get(str(t.reason or ""), 100), t.container),
    )[:3]

    warning_events_count = 0
    oom_killed_events = 0
    evicted = False
    for ev in investigation.evidence.k8s.pod_events or []:
        if isinstance(ev, dict) and (ev.get("type") or "").lower() == "warning":
            warning_events_count += 1
        if isinstance(ev, dict):
            reason = str(ev.get("reason") or "").lower()
            msg = str(ev.get("message") or "").lower()
            if "oom" in reason or "oomkill" in reason or "oomkilled" in msg:
                oom_killed_events += 1
            if "evict" in reason or "evicted" in msg:
                evicted = True

    def _event_sort_key(ev: Dict[str, Any]) -> tuple[float, int, str]:
        # Most recent first; fall back to count, then reason.
        t = (
            _parse_iso_datetime(ev.get("last_timestamp"))
            or _parse_iso_datetime(ev.get("event_time"))
            or _parse_iso_datetime(ev.get("first_timestamp"))
        )
        ts = t.timestamp() if t else 0.0
        cnt = int(ev.get("count") or 0) if isinstance(ev.get("count"), int) else 0
        r = str(ev.get("reason") or "")
        return (ts, cnt, r)

    # Build event summaries deterministically using raw event timestamps/counts when present.
    raw_events = [e for e in (investigation.evidence.k8s.pod_events or []) if isinstance(e, dict)]
    raw_events_sorted = sorted(raw_events, key=_event_sort_key, reverse=True)[:5]
    recent_event_reasons_top = [
        K8sEventSummary(
            reason=(str(e.get("reason")) if e.get("reason") is not None else None),
            count=(int(e.get("count")) if isinstance(e.get("count"), int) else None),
            type=(str(e.get("type")) if e.get("type") is not None else None),
            message=_truncate(e.get("message") if e.get("message") is not None else None, 200),
        )
        for e in raw_events_sorted
    ]

    # OOMKilled can also be reflected in container termination reason.
    oom_killed = False
    if waiting_reason and "oom" in str(waiting_reason).lower():
        oom_killed = True
    if oom_killed_events > 0:
        oom_killed = True
    if any((t.reason or "").strip().lower() == "oomkilled" for t in last_term_summaries):
        oom_killed = True

    return FeaturesK8s(
        pod_phase=phase,
        ready=ready,
        waiting_reason=waiting_reason,
        restart_count=restart_count,
        warning_events_count=warning_events_count,
        oom_killed=oom_killed,
        oom_killed_events=oom_killed_events,
        evicted=evicted,
        status_reason=(str(status_reason) if status_reason is not None else None),
        status_message=status_message,
        not_ready_conditions=not_ready_conditions,
        container_waiting_reasons_top=waiting_summaries,
        container_last_terminated_top=last_term_summaries,
        recent_event_reasons_top=recent_event_reasons_top,
    )


def _metrics_features(investigation: Investigation) -> FeaturesMetrics:
    container = investigation.target.container

    # CPU throttling p95
    throttling = investigation.evidence.metrics.throttling_data or {}
    t_series = throttling.get("throttling_percentage") if isinstance(throttling, dict) else []
    t_vals = _series_values(t_series or [], container=container)
    throttle_p95 = _percentile(t_vals, 0.95)

    # Container inference for throttling: find top container by p95 throttling.
    top_container = None
    top_container_p95 = None
    if isinstance(t_series, list) and t_series:
        by_container: Dict[str, List[float]] = {}
        for s in t_series:
            if not isinstance(s, dict):
                continue
            metric = s.get("metric", {}) or {}
            c = str(metric.get("container") or "")
            if not c or c == "POD":
                continue
            vals = []
            for _, v in s.get("values") or []:
                fv = _to_float(v)
                if fv is not None:
                    vals.append(fv)
            if vals:
                by_container.setdefault(c, []).extend(vals)
        for c, vals in by_container.items():
            p95 = _percentile(vals, 0.95)
            if p95 is None:
                continue
            if top_container_p95 is None or p95 > top_container_p95:
                top_container_p95 = p95
                top_container = c

    # CPU usage p95
    cpu = investigation.evidence.metrics.cpu_metrics or {}
    u_series = cpu.get("cpu_usage") if isinstance(cpu, dict) else []
    u_vals = _series_values(u_series or [], container=container)
    cpu_usage_p95 = _percentile(u_vals, 0.95)

    # CPU limit from first sample
    cpu_limit = None
    if isinstance(cpu, dict):
        for s in cpu.get("cpu_limits") or []:
            metric = s.get("metric", {}) or {}
            c = metric.get("container")
            if container and c and c != container:
                continue
            values = s.get("values") or []
            if values and len(values) > 0:
                cpu_limit = _to_float(values[0][1])
                break

    cpu_near_limit = None
    if cpu_usage_p95 is not None and cpu_limit and cpu_limit > 0:
        cpu_near_limit = (cpu_usage_p95 / cpu_limit) >= 0.8

    # Top throttled container usage/limit ratio (features-only inference)
    top_ratio = None
    if top_container:
        top_u_vals = _series_values(u_series or [], container=top_container)
        top_u_p95 = _percentile(top_u_vals, 0.95)
        top_lim = None
        if isinstance(cpu, dict):
            for s in cpu.get("cpu_limits") or []:
                metric = s.get("metric", {}) or {}
                c = metric.get("container")
                if c and c != top_container:
                    continue
                values = s.get("values") or []
                if values:
                    top_lim = _to_float(values[0][1])
                    break
        if top_u_p95 is not None and top_lim and top_lim > 0:
            top_ratio = float(top_u_p95) / float(top_lim)

    # Pod unhealthy observed
    unhealthy = False
    pps = investigation.evidence.metrics.pod_phase_signal or {}
    series = pps.get("pod_phase_signal") if isinstance(pps, dict) else []
    for s in series or []:
        values = s.get("values") or []
        for _, v in values:
            fv = _to_float(v)
            if fv is not None and fv > 0:
                unhealthy = True
                break
        if unhealthy:
            break

    http_p95, http_max = _http_5xx_features(investigation)
    mem_p95, mem_limit, mem_near = _memory_features(investigation)

    return FeaturesMetrics(
        cpu_throttle_p95_pct=throttle_p95,
        cpu_usage_p95_cores=cpu_usage_p95,
        cpu_limit_cores=cpu_limit,
        cpu_near_limit=cpu_near_limit,
        pod_unhealthy_phase_observed=unhealthy,
        http_5xx_rate_p95=http_p95,
        http_5xx_rate_max=http_max,
        memory_usage_p95_bytes=mem_p95,
        memory_limit_bytes=mem_limit,
        memory_near_limit=mem_near,
        cpu_throttle_top_container=top_container,
        cpu_throttle_top_container_p95_pct=top_container_p95,
        cpu_throttle_top_container_usage_limit_ratio=top_ratio,
    )


def _http_5xx_features(investigation: Investigation) -> tuple[Optional[float], Optional[float]]:
    h = investigation.evidence.metrics.http_5xx or {}
    series = h.get("series") if isinstance(h, dict) else []
    vals = _series_values(series or [], container=investigation.target.container)
    if not vals:
        return None, None
    return _percentile(vals, 0.95), max(vals)


def _memory_features(investigation: Investigation) -> tuple[Optional[float], Optional[float], Optional[bool]]:
    container = investigation.target.container
    m = investigation.evidence.metrics.memory_metrics or {}
    usage_series = m.get("memory_usage_bytes") if isinstance(m, dict) else []
    u_vals = _series_values(usage_series or [], container=container)
    usage_p95 = _percentile(u_vals, 0.95)

    limit = None
    if isinstance(m, dict):
        for s in m.get("memory_limits_bytes") or []:
            metric = s.get("metric", {}) or {}
            c = metric.get("container")
            if container and c and c != container:
                continue
            values = s.get("values") or []
            if values:
                limit = _to_float(values[0][1])
                break

    near = None
    if usage_p95 is not None and limit and limit > 0:
        near = (usage_p95 / limit) >= 0.9
    return usage_p95, limit, near


def _logs_features(investigation: Investigation) -> FeaturesLogs:
    status = investigation.evidence.logs.logs_status
    backend = investigation.evidence.logs.logs_backend
    reason = investigation.evidence.logs.logs_reason
    query_used = investigation.evidence.logs.logs_query
    timeout_hits = None
    error_hits = None
    if investigation.evidence.logs.logs:
        timeout_hits = 0
        error_hits = 0
        for e in investigation.evidence.logs.logs:
            if not isinstance(e, dict):
                continue
            msg = str(e.get("message") or "").lower()
            if "timeout" in msg or "timed out" in msg:
                timeout_hits += 1
            if "error" in msg or "exception" in msg:
                error_hits += 1
    return FeaturesLogs(
        status=status,
        backend=backend,
        reason=reason,
        query_used=query_used,
        timeout_hits=timeout_hits,
        error_hits=error_hits,
    )


def _changes_features(investigation: Investigation) -> FeaturesChanges:
    rollout_within_window = investigation.analysis.change.has_recent_change if investigation.analysis.change else None
    last_change_ts = investigation.analysis.change.last_change_time if investigation.analysis.change else None
    wk = None
    wn = None
    tl = investigation.analysis.change.timeline if investigation.analysis.change else None
    if tl and isinstance(tl.workload, dict):
        wk = tl.workload.get("kind")
        wn = tl.workload.get("name")
    return FeaturesChanges(
        rollout_within_window=rollout_within_window,
        last_change_ts=last_change_ts,
        workload_kind=wk,
        workload_name=wn,
    )


def _quality_features(
    investigation: Investigation, family: str, k8s: FeaturesK8s, metrics: FeaturesMetrics, logs: FeaturesLogs
) -> FeaturesQuality:
    missing: List[str] = []
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    missing_label_keys: List[str] = []

    # C) Missing labels: if the alert lacks namespace/pod for pod-scoped families, quality is low.
    if family in ("crashloop", "pod_not_healthy", "cpu_throttling"):
        if not (labels.get("namespace") or labels.get("Namespace")):
            missing.append("labels.namespace")
            missing_label_keys.append("namespace")
        if not (labels.get("pod") or labels.get("pod_name") or labels.get("podName")):
            missing.append("labels.pod")
            missing_label_keys.append("pod")
    if not investigation.evidence.k8s.pod_info:
        missing.append("k8s.pod_info")
    if investigation.evidence.logs.logs_status in ("unavailable", None):
        missing.append("logs")
    if investigation.evidence.metrics.cpu_metrics is None:
        missing.append("metrics.cpu")
    if investigation.evidence.metrics.restart_data is None:
        missing.append("metrics.restarts")

    contradiction_flags: List[str] = []
    if family == "crashloop":
        # CrashLoop contradiction: Ready=True and no restart signal
        if k8s.ready is True:
            rr = k8s.restart_rate_5m_max
            if rr is not None and rr <= 0:
                contradiction_flags.append("CRASHLOOP_CONTRADICTION_READY_NO_RESTARTS")
    if family == "cpu_throttling":
        # Throttling contradiction pattern: high throttling % but CPU usage far from limit.
        t = metrics.cpu_throttle_p95_pct
        ratio = metrics.cpu_throttle_top_container_usage_limit_ratio
        if ratio is None:
            # Fallback to overall ratio when we don't have inferred container ratio.
            try:
                if metrics.cpu_usage_p95_cores is not None and metrics.cpu_limit_cores and metrics.cpu_limit_cores > 0:
                    ratio = float(metrics.cpu_usage_p95_cores) / float(metrics.cpu_limit_cores)
            except Exception:
                ratio = None
        if t is not None and t > 25 and ratio is not None and ratio < 0.2:
            contradiction_flags.append("THROTTLING_HIGH_BUT_USAGE_LOW")

    # Impact signals availability (honesty about observability)
    logs_available = logs.status == "ok"
    http_metrics_available = (metrics.http_5xx_rate_p95 is not None) or (metrics.http_5xx_rate_max is not None)
    missing_impact_signals: List[str] = []
    if not logs_available:
        missing_impact_signals.append("logs")
    if not http_metrics_available:
        missing_impact_signals.append("http_metrics")
    impact_signals_available = bool(logs_available or http_metrics_available)

    # Alert age/duration (vs investigation window)
    age_hours = None
    starts = _parse_iso_datetime(investigation.alert.starts_at)
    if starts is not None:
        try:
            age_hours = max(0.0, (investigation.time_window.end_time - starts).total_seconds() / 3600.0)
        except Exception:
            age_hours = None
    # Long-running: firing for multiple days. This is commonly a signal of chronic/noisy alerting
    # rather than an acute incident.
    is_long = age_hours is not None and age_hours >= 72.0
    is_recent = age_hours is not None and age_hours <= 1.0

    quality = "high"
    if len(missing) >= 2:
        quality = "medium"
    if len(missing) >= 4:
        quality = "low"
    if missing_label_keys:
        quality = "low"

    return FeaturesQuality(
        evidence_quality=quality,
        missing_inputs=missing,
        contradiction_flags=contradiction_flags,
        impact_signals_available=impact_signals_available,
        missing_impact_signals=missing_impact_signals,
        alert_age_hours=age_hours,
        is_long_running=is_long if age_hours is not None else None,
        is_recently_started=is_recent if age_hours is not None else None,
    )


def compute_features(investigation: Investigation) -> DerivedFeatures:
    family = _detect_family(investigation)
    k8s = _k8s_features(investigation)

    # Fill restart rate from restart_data (metric)
    rr_max = None
    rd = investigation.evidence.metrics.restart_data or {}
    series = rd.get("restart_increase_5m") if isinstance(rd, dict) else []
    vals = _series_values(series or [], container=investigation.target.container)
    if vals:
        rr_max = max(vals)
    k8s.restart_rate_5m_max = rr_max

    metrics = _metrics_features(investigation)
    logs = _logs_features(investigation)
    changes = _changes_features(investigation)
    quality = _quality_features(investigation, family, k8s, metrics, logs)
    return DerivedFeatures(family=family, k8s=k8s, metrics=metrics, logs=logs, changes=changes, quality=quality)
