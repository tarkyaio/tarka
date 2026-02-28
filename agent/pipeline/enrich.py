"""Family enrichment builder (deterministic, on-call-first).

This module is additive: it never replaces the base triage decision. It populates
`investigation.analysis.enrichment` when the alert family is known and we have enough
evidence to provide actionable, family-specific context.
"""

from __future__ import annotations

from typing import List, Optional

from agent.core.models import Decision, Investigation, K8sEventSummary


def build_family_enrichment(investigation: Investigation) -> Optional[Decision]:
    f = investigation.analysis.features
    if f is None:
        return None

    if f.family == "k8s_rollout_health":
        return _enrich_k8s_rollout_health(investigation)

    if f.family == "target_down":
        return _enrich_target_down(investigation)

    if f.family == "pod_not_healthy":
        return _enrich_pod_not_healthy(investigation)

    if f.family == "oom_killed":
        return _enrich_oom_killed(investigation)

    if f.family == "http_5xx":
        return _enrich_http_5xx(investigation)

    if f.family == "memory_pressure":
        return _enrich_memory_pressure(investigation)

    if f.family == "cpu_throttling":
        return _enrich_cpu_throttling(investigation)

    if f.family == "observability_pipeline":
        return _enrich_observability_pipeline(investigation)

    if f.family == "meta":
        return _enrich_meta(investigation)

    if f.family == "job_failed":
        return _enrich_job_failed(investigation)

    if f.family == "crashloop":
        return _enrich_crashloop(investigation)

    return None


def _enrich_k8s_rollout_health(investigation: Investigation) -> Decision:
    """
    Non-pod workload rollout/job health enrichment.

    This is intentionally concise and deterministic: do not dump large condition lists; pick 1-2 key signals.
    """
    rs = investigation.evidence.k8s.rollout_status or {}
    ns = investigation.target.namespace
    wk = investigation.target.workload_kind
    wn = investigation.target.workload_name

    why: List[str] = []
    nxt: List[str] = []

    if wk and wn:
        why.append(f"Workload: {wk}/{wn}")
    if isinstance(rs, dict) and rs.get("kind") and rs.get("name"):
        kind = rs.get("kind")
        name = rs.get("name")
        if kind == "Deployment":
            why.append(
                "Rollout: "
                f"Deployment/{name} "
                f"ready={rs.get('ready_replicas')}/{rs.get('replicas')} "
                f"updated={rs.get('updated_replicas')} "
                f"unavailable={rs.get('unavailable_replicas')}"
            )
        elif kind == "StatefulSet":
            why.append(
                "Rollout: "
                f"StatefulSet/{name} "
                f"ready={rs.get('ready_replicas')}/{rs.get('replicas')} "
                f"current={rs.get('current_replicas')} updated={rs.get('updated_replicas')}"
            )
        elif kind == "DaemonSet":
            why.append(
                "Rollout: "
                f"DaemonSet/{name} "
                f"ready={rs.get('number_ready')}/{rs.get('desired_number_scheduled')} "
                f"updated={rs.get('updated_number_scheduled')}"
            )
        elif kind == "Job":
            why.append(
                "Job status: " f"active={rs.get('active')} succeeded={rs.get('succeeded')} failed={rs.get('failed')}"
            )

        # Surface one “most relevant” condition if present.
        conds = rs.get("conditions")
        if isinstance(conds, list):
            for c in conds:
                if not isinstance(c, dict):
                    continue
                ctype = (c.get("type") or "").strip()
                status = (c.get("status") or "").strip()
                reason = (c.get("reason") or "").strip()
                msg = (c.get("message") or "").strip()
                if not ctype:
                    continue
                # Prefer negative / failing conditions.
                if status.lower() in ("false", "unknown") or reason.lower() in ("progressdeadlineexceeded", "failed"):
                    line = f"Condition: {ctype} status={status}"
                    if reason:
                        line += f" reason={reason}"
                    if msg:
                        line += f" message={msg}"
                    why.append(line)
                    break

    # ---- Choose label (deterministic)
    label = "unknown_needs_human"
    if isinstance(rs, dict):
        kind = rs.get("kind")
        if kind == "Job":
            failed = rs.get("failed")
            if isinstance(failed, int) and failed > 0:
                label = "suspected_job_failed"
        if kind == "Deployment":
            unavail = rs.get("unavailable_replicas")
            replicas = rs.get("replicas")
            ready = rs.get("ready_replicas")
            updated = rs.get("updated_replicas")
            if isinstance(unavail, int) and unavail > 0:
                label = "suspected_rollout_stuck"
            elif isinstance(replicas, int) and isinstance(ready, int) and replicas != ready:
                label = "suspected_replicas_mismatch"
            elif isinstance(replicas, int) and isinstance(updated, int) and updated < replicas:
                label = "suspected_replicas_mismatch"

        if kind in ("StatefulSet", "DaemonSet") and label == "unknown_needs_human":
            # Best-effort mismatch detection for other workloads
            replicas = rs.get("replicas")
            ready = rs.get("ready_replicas") if kind == "StatefulSet" else rs.get("number_ready")
            if isinstance(replicas, int) and isinstance(ready, int) and replicas != ready:
                label = "suspected_replicas_mismatch"

    # ---- Next steps (PromQL-first)
    if ns and wk and wn:
        if wk == "Deployment":
            nxt.extend(
                [
                    f'kube_deployment_status_replicas{{namespace="{ns}",deployment="{wn}"}}',
                    f'kube_deployment_status_replicas_available{{namespace="{ns}",deployment="{wn}"}}',
                    f'kube_deployment_status_replicas_unavailable{{namespace="{ns}",deployment="{wn}"}}',
                    f'kube_deployment_status_observed_generation{{namespace="{ns}",deployment="{wn}"}}',
                    f"kubectl -n {ns} rollout status deployment/{wn}",
                ]
            )
        elif wk == "StatefulSet":
            nxt.extend(
                [
                    f'kube_statefulset_status_replicas{{namespace="{ns}",statefulset="{wn}"}}',
                    f'kube_statefulset_status_replicas_ready{{namespace="{ns}",statefulset="{wn}"}}',
                    f'kube_statefulset_status_replicas_current{{namespace="{ns}",statefulset="{wn}"}}',
                    f"kubectl -n {ns} rollout status statefulset/{wn}",
                ]
            )
        elif wk == "DaemonSet":
            nxt.extend(
                [
                    f'kube_daemonset_status_desired_number_scheduled{{namespace="{ns}",daemonset="{wn}"}}',
                    f'kube_daemonset_status_number_ready{{namespace="{ns}",daemonset="{wn}"}}',
                    f'kube_daemonset_status_updated_number_scheduled{{namespace="{ns}",daemonset="{wn}"}}',
                    f"kubectl -n {ns} rollout status daemonset/{wn}",
                ]
            )
        elif wk == "Job":
            nxt.extend(
                [
                    f'kube_job_status_failed{{namespace="{ns}",job_name="{wn}"}}',
                    f'kube_job_status_active{{namespace="{ns}",job_name="{wn}"}}',
                    f'kube_job_status_succeeded{{namespace="{ns}",job_name="{wn}"}}',
                    f"kubectl -n {ns} describe job {wn}",
                ]
            )
    else:
        nxt.append("Workload identity missing; follow base triage Scenario A to discover workload/namespace first.")

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _prom_scalar(v: object) -> Optional[float]:
    """
    Extract a scalar-ish float from the common `query_prometheus_instant()` vector shape.

    Expected input:
      [{"metric": {...}, "value": [ts, "123.4"]}, ...]
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


def _enrich_target_down(investigation: Investigation) -> Decision:
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    ns = investigation.target.namespace or (
        labels.get("namespace") if isinstance(labels.get("namespace"), str) else None
    )
    job = investigation.target.job or (labels.get("job") if isinstance(labels.get("job"), str) else None)
    instance = investigation.target.instance or (
        labels.get("instance") if isinstance(labels.get("instance"), str) else None
    )
    service = investigation.target.service or (
        labels.get("service") if isinstance(labels.get("service"), str) else None
    )

    why: List[str] = []
    nxt: List[str] = []

    if job:
        why.append(f"Scrape target: job={job}" + (f" instance={instance}" if instance else ""))
    elif instance:
        why.append(f"Scrape target: instance={instance}")
    if ns and service:
        why.append(f"Service label: {ns}/{service}")

    # Scope hint (from noise)
    firing = None
    if investigation.analysis.noise and isinstance(investigation.analysis.noise.prometheus, dict):
        firing = investigation.analysis.noise.prometheus.get("firing_instances")
    if isinstance(firing, (int, float)):
        why.append(f"Targets reported down (best-effort): {int(firing)}")

    # Use baseline counts if present (nonpod baseline may populate this)
    prom_baseline = getattr(investigation.evidence.metrics, "prom_baseline", None)
    down = None
    total = None
    if isinstance(prom_baseline, dict):
        down = _prom_scalar(prom_baseline.get("up_job_down"))
        total = _prom_scalar(prom_baseline.get("up_job_total"))
        if down is not None and total is not None and total > 0:
            why.append(f"up==0 count (job): {int(down)}/{int(total)}")

    # ---- Choose label (deterministic)
    label = "unknown_needs_human"
    if down is not None and total is not None and total > 0:
        if down >= max(2.0, 0.5 * total):
            label = "suspected_job_wide_scrape_failure"
        elif instance:
            label = "suspected_single_endpoint_down"
        elif down >= 1:
            label = "suspected_job_wide_scrape_failure"
    else:
        if instance:
            label = "suspected_single_endpoint_down"

    # ---- Next steps (PromQL-first + one UI hint)
    if job and instance:
        nxt.extend(
            [
                f'up{{job="{job}",instance="{instance}"}}',
                f'avg_over_time(up{{job="{job}",instance="{instance}"}}[30m])',
            ]
        )
    if job:
        nxt.extend(
            [
                f'sum(up{{job="{job}"}} == 0)',
                f'count(up{{job="{job}"}})',
            ]
        )
    if ns and service:
        nxt.extend(
            [
                f'sum(up{{namespace="{ns}",service="{service}"}} == 0)',
                f'count(up{{namespace="{ns}",service="{service}"}})',
            ]
        )
    nxt.append(
        "Check Prometheus /targets for the affected job/instance and inspect the last scrape error (DNS/TLS/timeout/exporter crash)."
    )

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_observability_pipeline(investigation: Investigation) -> Decision:
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname = str(labels.get("alertname") or "")
    lname = alertname.lower()

    job = investigation.target.job or (labels.get("job") if isinstance(labels.get("job"), str) else None)
    instance = investigation.target.instance or (
        labels.get("instance") if isinstance(labels.get("instance"), str) else None
    )

    why: List[str] = []
    nxt: List[str] = []

    why.append(f"Alert: {alertname or 'Unknown'}")
    if job or instance:
        why.append(f"Component labels: job={job or 'n/a'} instance={instance or 'n/a'}")

    firing = None
    if investigation.analysis.noise and isinstance(investigation.analysis.noise.prometheus, dict):
        firing = investigation.analysis.noise.prometheus.get("firing_instances")
    if isinstance(firing, (int, float)):
        why.append(f"Instances firing (best-effort): {int(firing)}")

    # ---- Choose label (deterministic by alert name shape)
    label = "unknown_needs_human"
    if "alertingruleserror" in lname or "recordingrulesnodata" in lname:
        label = "suspected_alerting_rules_error"
    elif "rowsrejectedoningestion" in lname:
        label = "suspected_ingestion_failure"
    elif "toomanylogs" in lname:
        label = "suspected_ingestion_failure"
    else:
        label = "suspected_prometheus_or_vm_incident"

    # ---- Next steps (PromQL-first; keep generic but useful)
    if job and instance:
        nxt.append(f'up{{job="{job}",instance="{instance}"}}')
    elif job:
        nxt.append(f'sum(up{{job="{job}"}} == 0)')
        nxt.append(f'count(up{{job="{job}"}})')

    nxt.extend(
        [
            'topk(20, count by (alertname) (ALERTS{alertstate="firing"}))',
            "If many observability-related alerts are firing at once, treat as a platform incident and verify the metrics/logs pipeline health with the observability on-call.",
            "Check the affected component logs (vmalert/prometheus/agent) for rule evaluation or ingestion errors.",
        ]
    )

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_meta(investigation: Investigation) -> Decision:
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname = str(labels.get("alertname") or "")

    why: List[str] = []
    nxt: List[str] = []

    why.append("This is a meta/inhibitor alert intended to suppress other alerts (not a direct symptom).")
    if alertname:
        why.append(f"Alert: {alertname}")

    label = "expected_inhibitor" if alertname.lower() == "infoinhibitor" else "misrouted_meta_alert"

    nxt.extend(
        [
            "Confirm this alert is routed to a non-paging receiver (or to the platform team inbox), not to on-call paging.",
            "Verify Alertmanager inhibition rules and grouping are configured as expected.",
        ]
    )
    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_pod_not_healthy(investigation: Investigation) -> Decision:
    f = investigation.analysis.features
    assert f is not None

    ns = investigation.target.namespace
    pod = investigation.target.pod

    why: List[str] = []
    nxt: List[str] = []

    # ---- Evidence extracts (deterministic)
    phase = (f.k8s.pod_phase or "").strip()
    ready = f.k8s.ready
    status_reason = f.k8s.status_reason
    status_message = f.k8s.status_message

    if phase:
        bits = [f"phase={phase}"]
        if ready is not None:
            bits.append(f"ready={ready}")
        if status_reason:
            bits.append(f"reason={status_reason}")
        if status_message:
            bits.append(f"message={status_message}")
        why.append("Pod status: " + " | ".join(bits))

    # Waiting + last termination summaries (already ranked in features)
    for w in f.k8s.container_waiting_reasons_top[:3]:
        if w.container or w.reason or w.message:
            msg = f"Container waiting: {w.container}"
            if w.reason:
                msg += f" reason={w.reason}"
            if w.message:
                msg += f" message={w.message}"
            why.append(msg)

    for t in f.k8s.container_last_terminated_top[:3]:
        msg = f"Last terminated: {t.container}"
        if t.reason:
            msg += f" reason={t.reason}"
        if t.exit_code is not None:
            msg += f" exitCode={t.exit_code}"
        why.append(msg)

    # Restarts
    if f.k8s.restart_rate_5m_max is not None:
        why.append(f"Restart spike: restart_rate_5m_max={f.k8s.restart_rate_5m_max:.2f}")
    elif f.k8s.restart_count is not None:
        why.append(f"Restart count: {f.k8s.restart_count}")

    # Events (top recent, already compacted)
    if f.k8s.warning_events_count == 0:
        why.append("Warnings queried: 0")
    else:
        for ev in f.k8s.recent_event_reasons_top[:3]:
            why.append(_fmt_event(ev))

    # Owner/workload + rollout status (if present)
    wl_kind = None
    wl_name = None
    oc = investigation.evidence.k8s.owner_chain
    if isinstance(oc, dict):
        wl = oc.get("workload")
        if isinstance(wl, dict):
            wl_kind = wl.get("kind")
            wl_name = wl.get("name")
    if wl_kind and wl_name:
        why.append(f"Workload: {wl_kind}/{wl_name}")

    rs = investigation.evidence.k8s.rollout_status
    if isinstance(rs, dict) and rs.get("kind") and rs.get("name"):
        # Keep it short and deterministic (avoid dumping whole conditions list).
        if rs.get("kind") == "Deployment":
            why.append(
                "Rollout: "
                f"Deployment/{rs.get('name')} "
                f"ready={rs.get('ready_replicas')}/{rs.get('replicas')} "
                f"unavailable={rs.get('unavailable_replicas')}"
            )
        elif rs.get("kind") == "StatefulSet":
            why.append(
                "Rollout: " f"StatefulSet/{rs.get('name')} " f"ready={rs.get('ready_replicas')}/{rs.get('replicas')}"
            )
        elif rs.get("kind") == "DaemonSet":
            why.append(
                "Rollout: "
                f"DaemonSet/{rs.get('name')} "
                f"ready={rs.get('number_ready')}/{rs.get('desired_number_scheduled')}"
            )
        elif rs.get("kind") == "Job":
            why.append(
                "Job status: " f"active={rs.get('active')} succeeded={rs.get('succeeded')} failed={rs.get('failed')}"
            )

    # ---- Choose label
    # If K8s context is missing, we cannot deterministically classify root cause; avoid "unknown_needs_human"
    # and instead align with base triage blocker language.
    if f.quality and "k8s.pod_info" in (f.quality.missing_inputs or []):
        label = "blocked_no_k8s_context"
        why.insert(
            0,
            "K8s context unavailable to agent; cannot extract waiting reasons/events/status for deterministic classification.",
        )
    else:
        label = _podnh_label(investigation)

    # ---- Next steps (PromQL-first when we have pod identity)
    if ns and pod:
        nxt.extend(
            [
                f'max by (cluster, namespace, pod, phase) (kube_pod_status_phase{{namespace="{ns}",pod="{pod}"}})',
                f'max by (cluster, namespace, pod, condition) (kube_pod_status_ready{{namespace="{ns}",pod="{pod}"}})',
                f'increase(kube_pod_container_status_restarts_total{{namespace="{ns}",pod="{pod}"}}[30m])',
                f'max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{{namespace="{ns}",pod="{pod}"}})',
            ]
        )
        # One kubectl fallback (on-call often has it; when they don't, PromQL above still helps).
        nxt.append(f"kubectl -n {ns} describe pod {pod}")
    else:
        nxt.append("Target pod identity missing; follow base triage Scenario A to discover namespace/pod first.")

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_oom_killed(investigation: Investigation) -> Decision:
    f = investigation.analysis.features
    assert f is not None

    ns = investigation.target.namespace
    pod = investigation.target.pod
    c = investigation.target.container

    why: List[str] = []
    nxt: List[str] = []

    # Evidence: last termination reason for target container (already compacted)
    lt = None
    if c:
        for t in f.k8s.container_last_terminated_top:
            if (t.container or "") == c:
                lt = t
                break
    if lt is None and f.k8s.container_last_terminated_top:
        lt = f.k8s.container_last_terminated_top[0]

    if lt is not None:
        msg = f"Last terminated: {lt.container}"
        if lt.reason:
            msg += f" reason={lt.reason}"
        if lt.exit_code is not None:
            msg += f" exitCode={lt.exit_code}"
        why.append(msg)

    # Restarts trend
    if f.k8s.restart_rate_5m_max is not None:
        why.append(f"Restart spike: restart_rate_5m_max={f.k8s.restart_rate_5m_max:.2f}")
    elif f.k8s.restart_count is not None:
        why.append(f"Restart count: {f.k8s.restart_count}")

    # Memory evidence (if collected)
    if f.metrics.memory_usage_p95_bytes is not None:
        why.append(f"Memory p95 bytes: {int(f.metrics.memory_usage_p95_bytes)}")
    if f.metrics.memory_limit_bytes is not None:
        why.append(f"Memory limit bytes: {int(f.metrics.memory_limit_bytes)}")

    # Workload
    oc = investigation.evidence.k8s.owner_chain
    if isinstance(oc, dict):
        wl = oc.get("workload")
        if isinstance(wl, dict) and wl.get("kind") and wl.get("name"):
            why.append(f"Workload: {wl.get('kind')}/{wl.get('name')}")

    # Label selection (deterministic)
    label = "unknown_needs_human"
    # node pressure / eviction
    if f.k8s.evicted or (f.k8s.status_reason or "").strip().lower() == "evicted":
        label = "suspected_node_pressure"
    else:
        usage = f.metrics.memory_usage_p95_bytes
        limit = f.metrics.memory_limit_bytes
        if usage is not None and limit is not None and limit > 0:
            if usage >= 0.85 * limit:
                label = "suspected_oom_limit_too_low"
            else:
                label = "suspected_memory_leak_or_spike"
        elif f.k8s.oom_killed:
            label = "suspected_memory_leak_or_spike"

    # Next steps: PromQL-first
    if ns and pod:
        csel = c or "<container>"
        nxt.extend(
            [
                f'quantile_over_time(0.95, sum by (namespace, pod, container) (container_memory_working_set_bytes{{namespace="{ns}",pod="{pod}",container="{csel}",container!="POD",image!=""}})[30m])',
                f'max by (namespace, pod, container) (kube_pod_container_resource_limits{{namespace="{ns}",pod="{pod}",container="{csel}",resource="memory"}})',
                f'increase(kube_pod_container_status_restarts_total{{namespace="{ns}",pod="{pod}",container="{csel}"}}[30m])',
                f'max by (namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{{namespace="{ns}",pod="{pod}",container="{csel}"}})',
                f"kubectl -n {ns} describe pod {pod}",
            ]
        )
    else:
        nxt.append("Target identity missing; follow base triage Scenario A to discover namespace/pod/container first.")

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_http_5xx(investigation: Investigation) -> Decision:
    f = investigation.analysis.features
    assert f is not None

    why: List[str] = []
    nxt: List[str] = []

    h = investigation.evidence.metrics.http_5xx or {}
    query_used = h.get("query_used") if isinstance(h, dict) else None
    series = h.get("series") if isinstance(h, dict) else None

    # Observations
    if query_used:
        why.append(f"HTTP metric query used: {query_used}")
    if f.metrics.http_5xx_rate_p95 is not None or f.metrics.http_5xx_rate_max is not None:
        why.append(
            "HTTP 5xx rate: "
            f"p95={f.metrics.http_5xx_rate_p95 if f.metrics.http_5xx_rate_p95 is not None else 'n/a'} "
            f"max={f.metrics.http_5xx_rate_max if f.metrics.http_5xx_rate_max is not None else 'n/a'}"
        )
    if isinstance(series, list):
        why.append(f"Series returned: {len(series)}")

    # Change hint
    if f.changes.rollout_within_window is True:
        why.append("Recent change detected within window (possible rollout regression).")
    elif f.changes.rollout_within_window is False:
        why.append("No recent rollout change detected within window.")

    # Conservative label selection
    if f.changes.rollout_within_window is True:
        label = "suspected_rollout_regression"
    elif (f.logs.timeout_hits or 0) > 0:
        label = "suspected_infra_network"
    else:
        label = "unknown_needs_human"

    # On-call next steps (PromQL-first)
    if query_used:
        nxt.append(str(query_used))
    else:
        nxt.append("Confirm 5xx metric scope (service/namespace) and whether it is sustained.")

    # Generic fallbacks (best-effort; may need adapting per env)
    nxt.extend(
        [
            'topk(10, sum by (namespace, service) (rate(http_requests_total{status=~"5.."}[5m])))',
            'topk(10, sum by (namespace, service) (rate(http_server_requests_seconds_count{status=~"5.."}[5m])))',
        ]
    )

    # If pod identity exists, offer a kubectl fallback
    if investigation.target.namespace and investigation.target.pod and investigation.target.target_type == "pod":
        nxt.append(f"kubectl -n {investigation.target.namespace} describe pod {investigation.target.pod}")

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_memory_pressure(investigation: Investigation) -> Decision:
    f = investigation.analysis.features
    assert f is not None

    ns = investigation.target.namespace
    pod = investigation.target.pod
    c = investigation.target.container

    why: List[str] = []
    nxt: List[str] = []

    # Pod status / pressure hints
    phase = (f.k8s.pod_phase or "").strip()
    sr = (f.k8s.status_reason or "").strip()
    sm = (f.k8s.status_message or "").strip()
    if phase or sr or sm:
        bits = []
        if phase:
            bits.append(f"phase={phase}")
        if sr:
            bits.append(f"reason={sr}")
        if sm:
            bits.append(f"message={sm}")
        why.append("Pod status: " + " | ".join(bits))

    # Memory evidence
    if f.metrics.memory_usage_p95_bytes is not None:
        why.append(f"Memory p95 bytes: {int(f.metrics.memory_usage_p95_bytes)}")
    if f.metrics.memory_limit_bytes is not None:
        why.append(f"Memory limit bytes: {int(f.metrics.memory_limit_bytes)}")
    if f.metrics.memory_near_limit is True:
        why.append("Memory near limit: yes (p95 >= 90% of limit)")

    # Restarts trend
    if f.k8s.restart_rate_5m_max is not None:
        why.append(f"Restart spike: restart_rate_5m_max={f.k8s.restart_rate_5m_max:.2f}")
    elif f.k8s.restart_count is not None:
        why.append(f"Restart count: {f.k8s.restart_count}")

    # Events
    if f.k8s.warning_events_count == 0:
        why.append("Warnings queried: 0")
    else:
        for ev in f.k8s.recent_event_reasons_top[:2]:
            why.append(_fmt_event(ev))

    # Workload
    oc = investigation.evidence.k8s.owner_chain
    if isinstance(oc, dict):
        wl = oc.get("workload")
        if isinstance(wl, dict) and wl.get("kind") and wl.get("name"):
            why.append(f"Workload: {wl.get('kind')}/{wl.get('name')}")

    # Label selection (deterministic)
    sr_l = sr.lower()
    sm_l = sm.lower()
    label = "unknown_needs_human"
    if f.k8s.evicted or sr_l == "evicted" or any(x in sm_l for x in ("memorypressure", "diskpressure", "pidpressure")):
        label = "suspected_node_pressure_or_eviction"
    elif f.metrics.memory_near_limit is True:
        label = "suspected_container_near_limit"
    elif f.metrics.memory_usage_p95_bytes is not None:
        label = "suspected_memory_leak_or_spike"

    # On-call next steps (PromQL-first)
    if ns and pod:
        csel = c or "<container>"
        nxt.extend(
            [
                f'quantile_over_time(0.95, sum by (namespace, pod, container) (container_memory_working_set_bytes{{namespace="{ns}",pod="{pod}",container="{csel}",container!="POD",image!=""}})[30m])',
                f'max by (namespace, pod, container) (kube_pod_container_resource_limits{{namespace="{ns}",pod="{pod}",container="{csel}",resource="memory"}})',
                f'increase(kube_pod_container_status_restarts_total{{namespace="{ns}",pod="{pod}",container="{csel}"}}[30m])',
                f"kubectl -n {ns} describe pod {pod}",
            ]
        )
    else:
        nxt.append("Target identity missing; follow base triage Scenario A to discover namespace/pod/container first.")

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_cpu_throttling(investigation: Investigation) -> Decision:
    f = investigation.analysis.features
    assert f is not None

    ns = investigation.target.namespace
    pod = investigation.target.pod
    # Prefer explicit target container, else inferred top throttled container.
    c = investigation.target.container or f.metrics.cpu_throttle_top_container

    why: List[str] = []
    nxt: List[str] = []

    t = f.metrics.cpu_throttle_p95_pct
    if t is not None:
        why.append(f"CPU throttling p95: {t:.1f}%")

    if f.metrics.cpu_throttle_top_container:
        why.append(f"Top throttled container (inferred): {f.metrics.cpu_throttle_top_container}")
    if f.metrics.cpu_throttle_top_container_p95_pct is not None:
        why.append(f"Top container throttling p95: {f.metrics.cpu_throttle_top_container_p95_pct:.1f}%")

    if f.metrics.cpu_usage_p95_cores is not None:
        why.append(f"CPU usage p95 cores: {f.metrics.cpu_usage_p95_cores:.3f}")
    if f.metrics.cpu_limit_cores is not None:
        why.append(f"CPU limit cores: {f.metrics.cpu_limit_cores:.3f}")
    if f.metrics.cpu_near_limit is True:
        why.append("CPU near limit: yes (p95 >= 80% of limit)")
    elif f.metrics.cpu_near_limit is False:
        why.append("CPU near limit: no (p95 < 80% of limit)")

    # Label selection (conservative)
    label = "unknown_needs_human"
    # Missing limit is common and usually the most actionable fix.
    if f.metrics.cpu_limit_cores is None and f.metrics.cpu_usage_p95_cores is not None:
        label = "suspected_missing_cpu_limit"
    else:
        # Prefer container-specific ratio if available.
        ratio = f.metrics.cpu_throttle_top_container_usage_limit_ratio
        if (
            ratio is None
            and f.metrics.cpu_usage_p95_cores is not None
            and f.metrics.cpu_limit_cores
            and f.metrics.cpu_limit_cores > 0
        ):
            ratio = float(f.metrics.cpu_usage_p95_cores) / float(f.metrics.cpu_limit_cores)

        if t is not None and t >= 25:
            if ratio is not None and ratio >= 0.8:
                label = "suspected_cpu_limit_too_low"
            elif ratio is not None and ratio < 0.2:
                label = "suspected_cfs_throttle_but_usage_low"

    # On-call next (PromQL-first)
    if ns and pod:
        csel = c or "<container>"
        nxt.extend(
            [
                # Throttling % (alert-like)
                (
                    f'100 * sum by(container,pod,namespace) (increase(container_cpu_cfs_throttled_periods_total{{namespace="{ns}",pod="{pod}",container="{csel}",image!=""}}[5m])) '
                    f'/ clamp_min(sum by(container,pod,namespace) (increase(container_cpu_cfs_periods_total{{namespace="{ns}",pod="{pod}",container="{csel}",image!=""}}[5m])), 1)'
                ),
                # Usage and limit
                f'sum by(container,pod,namespace) (rate(container_cpu_usage_seconds_total{{namespace="{ns}",pod="{pod}",container="{csel}",image!=""}}[5m]))',
                f'max by(container,pod,namespace) (kube_pod_container_resource_limits{{namespace="{ns}",pod="{pod}",container="{csel}",resource="cpu"}})',
                f"kubectl -n {ns} describe pod {pod}",
            ]
        )
    else:
        nxt.append("Target identity missing; follow base triage Scenario A to discover namespace/pod/container first.")

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _enrich_job_failed(investigation: Investigation) -> Decision:
    """Job failure enrichment - completion status, exit details, IAM context."""
    f = investigation.analysis.features
    assert f is not None

    ns = investigation.target.namespace
    wn = investigation.target.workload_name  # Job name
    pod = investigation.target.pod

    why: List[str] = []
    nxt: List[str] = []

    # Job completion status (from rollout_status if available)
    rs = investigation.evidence.k8s.rollout_status or {}
    if isinstance(rs, dict) and rs.get("kind") == "Job":
        active = rs.get("active", 0)
        succeeded = rs.get("succeeded", 0)
        failed = rs.get("failed", 0)
        why.append(f"Job status: active={active} succeeded={succeeded} failed={failed}")

        # Conditions
        conds = rs.get("conditions", [])
        for c in conds:
            if isinstance(c, dict) and c.get("type") == "Failed" and c.get("status") == "True":
                reason = c.get("reason", "")
                message = c.get("message", "")
                if reason or message:
                    why.append(f"Job failure: reason={reason} message={message}")

    # Container exit details (from features)
    exit_code = None
    exit_reason = None
    if f.k8s.container_last_terminated_top:
        term = f.k8s.container_last_terminated_top[0]
        exit_code = term.exit_code
        exit_reason = term.reason
        msg = f"Container exit: exitCode={exit_code}"
        if exit_reason:
            msg += f" reason={exit_reason}"
        why.append(msg)

    # IAM context (if pod_info available)
    pod_info = investigation.evidence.k8s.pod_info
    if isinstance(pod_info, dict):
        sa_name = pod_info.get("service_account_name")
        if sa_name:
            why.append(f"Service account: {sa_name}")
            # Store for placeholder resolution later
            if not hasattr(investigation.evidence.k8s, "service_account_name"):
                investigation.evidence.k8s.service_account_name = sa_name

    # Error summary from logs
    if f.logs.error_hits is not None and f.logs.error_hits > 0:
        why.append(f"Error patterns in logs: {f.logs.error_hits} occurrences")

    # Choose label based on exit code
    label = "job_failed"
    if exit_code == 1:
        label = "job_failed_exit_1"
    elif exit_code and exit_code > 1:
        label = f"job_failed_exit_{exit_code}"

    # Next steps (PromQL-first, then kubectl)
    if ns and wn:
        nxt.extend(
            [
                f'kube_job_status_failed{{namespace="{ns}",job_name="{wn}"}}',
                f'kube_job_status_active{{namespace="{ns}",job_name="{wn}"}}',
                f'kube_job_status_succeeded{{namespace="{ns}",job_name="{wn}"}}',
                f'kube_job_complete{{namespace="{ns}",job_name="{wn}"}}',
                f"kubectl -n {ns} describe job {wn}",
                f"kubectl -n {ns} get pods -l job-name={wn}",
            ]
        )

    if ns and pod:
        nxt.append(f"kubectl -n {ns} logs {pod}")

    # Resolve placeholders in next steps
    from agent.utils.placeholder_resolver import PlaceholderResolver

    resolver = PlaceholderResolver(investigation)
    nxt_resolved = [resolver.resolve(cmd) for cmd in nxt]

    return Decision(label=label, why=why[:10], next=nxt_resolved[:10])


def _enrich_crashloop(investigation: Investigation) -> Decision:
    """CrashLoopBackOff enrichment - exit codes, probe failures, crash timing, log patterns."""
    f = investigation.analysis.features
    assert f is not None

    ns = investigation.target.namespace
    pod = investigation.target.pod
    c = investigation.target.container

    why: List[str] = []
    nxt: List[str] = []

    # Pod status (phase, ready, waiting_reason)
    phase = (f.k8s.pod_phase or "").strip()
    ready = f.k8s.ready
    waiting = (f.k8s.waiting_reason or "").strip()

    if phase or waiting:
        bits = []
        if phase:
            bits.append(f"phase={phase}")
        if ready is not None:
            bits.append(f"ready={ready}")
        if waiting:
            bits.append(f"waiting={waiting}")
        why.append("Pod status: " + " | ".join(bits))

    # Container waiting reasons (top 3)
    for w in f.k8s.container_waiting_reasons_top[:3]:
        if w.container or w.reason or w.message:
            msg = f"Container waiting: {w.container}"
            if w.reason:
                msg += f" reason={w.reason}"
            if w.message:
                msg += f" message={w.message}"
            why.append(msg)

    # Last termination details
    exit_code = None
    exit_reason = None
    for t in f.k8s.container_last_terminated_top[:3]:
        msg = f"Last terminated: {t.container}"
        if t.reason:
            msg += f" reason={t.reason}"
        if t.exit_code is not None:
            msg += f" exitCode={t.exit_code}"
        why.append(msg)
        if exit_code is None:
            exit_code = t.exit_code
            exit_reason = (t.reason or "").strip().lower()

    # Restart rate
    if f.k8s.restart_rate_5m_max is not None:
        why.append(f"Restart spike: restart_rate_5m_max={f.k8s.restart_rate_5m_max:.2f}")
    elif f.k8s.restart_count is not None:
        why.append(f"Restart count: {f.k8s.restart_count}")

    # Crash duration
    crash_duration = investigation.meta.get("crash_duration_seconds")
    if crash_duration is not None:
        if crash_duration < 10:
            why.append(f"Crash duration: {crash_duration}s (instant crash — likely config/dependency)")
        elif crash_duration > 60:
            why.append(f"Crash duration: {crash_duration}s (slow crash — likely memory leak/timeout)")
        else:
            why.append(f"Crash duration: {crash_duration}s")

    # Probe failure indication
    probe_type = investigation.meta.get("probe_failure_type")
    if probe_type:
        why.append(f"Probe failure detected: {probe_type}")

    # Warning events (top 3)
    if f.k8s.warning_events_count == 0:
        why.append("Warnings queried: 0")
    else:
        for ev in f.k8s.recent_event_reasons_top[:3]:
            why.append(_fmt_event(ev))

    # Workload identity + rollout status
    oc = investigation.evidence.k8s.owner_chain
    wl_kind = None
    wl_name = None
    if isinstance(oc, dict):
        wl = oc.get("workload")
        if isinstance(wl, dict):
            wl_kind = wl.get("kind")
            wl_name = wl.get("name")
    if wl_kind and wl_name:
        why.append(f"Workload: {wl_kind}/{wl_name}")

    rs = investigation.evidence.k8s.rollout_status
    if isinstance(rs, dict) and rs.get("kind") and rs.get("name"):
        if rs.get("kind") == "Deployment":
            why.append(
                "Rollout: "
                f"Deployment/{rs.get('name')} "
                f"ready={rs.get('ready_replicas')}/{rs.get('replicas')} "
                f"unavailable={rs.get('unavailable_replicas')}"
            )

    # ---- Choose label (deterministic)
    label = _crashloop_label(investigation, exit_code, exit_reason, probe_type, crash_duration)

    # ---- Next steps (PromQL-first)
    if ns and pod:
        csel = c or "<container>"
        nxt.extend(
            [
                f'increase(kube_pod_container_status_restarts_total{{namespace="{ns}",pod="{pod}"}}[30m])',
                f'max by (container, reason) (kube_pod_container_status_last_terminated_reason{{namespace="{ns}",pod="{pod}"}})',
                f'kube_pod_status_ready{{namespace="{ns}",pod="{pod}"}}',
                f"kubectl -n {ns} logs {pod} -c {csel} --previous --tail=200",
                f"kubectl -n {ns} describe pod {pod}",
            ]
        )
    else:
        nxt.append("Target pod identity missing; follow base triage Scenario A to discover namespace/pod first.")

    return Decision(label=label, why=why[:10], next=nxt[:10])


def _crashloop_label(
    investigation: Investigation,
    exit_code: object,
    exit_reason: object,
    probe_type: object,
    crash_duration: object,
) -> str:
    """Deterministic label selection for crashloop alerts."""
    exit_reason_str = str(exit_reason or "").strip().lower()

    # OOM crash
    if exit_code == 137 or exit_reason_str == "oomkilled":
        return "suspected_oom_crash"

    # Liveness probe failure (exit 0 + liveness events)
    if exit_code == 0 and probe_type == "liveness":
        return "suspected_liveness_probe_failure"

    # Check log patterns for dependency/config hints
    parsed_errors = None
    if investigation.evidence.logs and investigation.evidence.logs.parsed_errors:
        parsed_errors = investigation.evidence.logs.parsed_errors
    prev_parsed = investigation.meta.get("previous_logs_parsed_errors")

    combined_text = ""
    if parsed_errors:
        combined_text += " ".join(e.get("message", "") for e in parsed_errors)
    if prev_parsed:
        combined_text += " " + " ".join(e.get("message", "") for e in prev_parsed)
    combined_lower = combined_text.lower()

    # Check config/permission BEFORE dependency to avoid "enotfound" matching "FileNotFoundError"
    if any(
        kw in combined_lower
        for kw in [
            "permission denied",
            "eacces",
            "filenotfounderror",
            "no such file or directory",
            "missing required",
            "required key",
            "read-only file system",
        ]
    ):
        return "suspected_config_or_permission_error"

    if any(
        kw in combined_lower for kw in ["connection refused", "econnrefused", "no such host", "getaddrinfo enotfound"]
    ):
        return "suspected_dependency_unavailable"

    # Startup vs runtime failure based on crash timing
    if exit_code == 1 and isinstance(crash_duration, (int, float)):
        if crash_duration < 10:
            return "suspected_app_startup_failure"
        if crash_duration > 60:
            return "suspected_app_runtime_failure"

    return "unknown_needs_human"


def _fmt_event(ev: K8sEventSummary) -> str:
    parts = []
    if ev.type:
        parts.append(f"type={ev.type}")
    if ev.reason:
        parts.append(f"reason={ev.reason}")
    if ev.count is not None:
        parts.append(f"count={ev.count}")
    msg = ev.message or ""
    if msg:
        parts.append(f"msg={msg}")
    if not parts:
        return "Event: (unavailable)"
    return "Event: " + " | ".join(parts)


def _podnh_label(investigation: Investigation) -> str:
    f = investigation.analysis.features
    assert f is not None

    waiting_reasons = [str(w.reason or "") for w in f.k8s.container_waiting_reasons_top]
    waiting_reasons_l = [r.lower() for r in waiting_reasons if r]

    status_reason_l = (f.k8s.status_reason or "").strip().lower()
    status_msg_l = (f.k8s.status_message or "").strip().lower()

    # 1) image pull / registry
    if any(r in ("imagepullbackoff", "errimagepull", "registryunavailable") for r in waiting_reasons_l):
        return "suspected_image_pull_backoff"

    # 2) config
    if any(r in ("createcontainerconfigerror", "createcontainererror", "invalidimagename") for r in waiting_reasons_l):
        return "suspected_config_error"

    # 5) OOMKill (prioritize explicit OOM)
    if f.k8s.oom_killed:
        return "suspected_oomkill"

    # 3) scheduling/node pressure (evicted/pressure are strong proxies)
    if (
        f.k8s.evicted
        or status_reason_l == "evicted"
        or any(x in status_msg_l for x in ("diskpressure", "memorypressure", "pidpressure", "nodehadcondition"))
    ):
        return "suspected_scheduling_or_node_pressure"

    # Scheduling/unschedulable: Pending + explicit unschedulable signals.
    if (f.k8s.pod_phase or "").strip().lower() == "pending":
        if status_reason_l == "unschedulable" or "failedscheduling" in status_reason_l:
            return "suspected_scheduling_or_node_pressure"
        if "insufficient" in status_msg_l or "0/" in status_msg_l:
            return "suspected_scheduling_or_node_pressure"
        for ev in f.k8s.recent_event_reasons_top:
            if (ev.reason or "").strip().lower() == "failedscheduling":
                return "suspected_scheduling_or_node_pressure"

    # 6) job semantics (if workload=Job)
    oc = investigation.evidence.k8s.owner_chain
    wl_kind = None
    if isinstance(oc, dict):
        wl = oc.get("workload")
        if isinstance(wl, dict):
            wl_kind = wl.get("kind")
    if (wl_kind or "").lower() == "job":
        return "suspected_job_failed"

    # 4) crashloop/probe
    if any(r in ("crashloopbackoff",) for r in waiting_reasons_l):
        return "suspected_crashloop_or_probe_failure"
    rr = f.k8s.restart_rate_5m_max
    if rr is not None and rr >= 1:
        return "suspected_crashloop_or_probe_failure"
    for t in f.k8s.container_last_terminated_top:
        if (t.reason or "").lower() == "error" and (t.exit_code or 0) != 0:
            return "suspected_crashloop_or_probe_failure"

    # Fallback
    return "unknown_needs_human"
