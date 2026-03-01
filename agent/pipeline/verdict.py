"""Base triage decision builder (scenario-driven, deterministic).

This module builds `investigation.analysis.decision` from existing investigation state.
It is intentionally:
- deterministic
- PromQL-first
- honest about missing inputs
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from agent.core.models import Decision, Investigation


@dataclass(frozen=True)
class _ScopeInfo:
    label: str  # e.g. "Small", "Broad", "Widespread", "Scope=unknown"
    n: Optional[float]  # firing/active count used for label, if available
    source: Optional[str]  # "firing_instances" | "active_instances" | None
    prom_status: str  # "ok" | "unavailable" | "missing" | "skipped" | "unknown"
    selector: Optional[str]


_DISCRIMINATOR_ORDER = [
    # Most blocking first (on-call cannot even quantify scope)
    "blocked_prometheus_unavailable",
    # Cannot attribute incident target
    "blocked_no_target_identity",
    # Cannot attribute K8s state for known pod
    "blocked_no_k8s_context",
    # Job pods TTL-deleted
    "blocked_job_pods_not_found",
    # Missing logs for reasoning
    "logs_missing",
    # Combined/double-blocked
    "blocked_no_scope_no_identity",
]


def _safe_get(d: Any, key: str) -> Any:
    return d.get(key) if isinstance(d, dict) else None


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _scope_label(n: float) -> str:
    # Thresholds from docs/acceptance/base-contract.md
    if n == 1:
        return "Single-instance"
    if 2 <= n <= 5:
        return "Small"
    if 6 <= n <= 20:
        return "Multi-instance"
    if 21 <= n <= 49:
        return "Broad"
    if 50 <= n <= 100:
        return "Widespread"
    if n >= 101:
        return "Massive"
    # 0 or negative should not happen, but be safe.
    return "Scope=unknown"


def _parse_prom_scope(investigation: Investigation) -> Tuple[_ScopeInfo, bool]:
    """
    Return (scope_info, prom_unavailable_to_agent).
    """
    n = None
    source = None
    selector = None
    prom_status = "missing"

    noise = investigation.analysis.noise
    prom = None
    if noise is not None:
        prom = noise.prometheus

    if isinstance(prom, dict):
        prom_status = str(prom.get("status") or "unknown")
        selector = prom.get("selector")
        fi = _to_float(prom.get("firing_instances"))
        ai = _to_float(prom.get("active_instances"))
        if fi is not None:
            n = fi
            source = "firing_instances"
        elif ai is not None:
            n = ai
            source = "active_instances"
    else:
        prom_status = "missing"

    prom_unavailable = prom_status != "ok"
    if n is None:
        return (
            _ScopeInfo(label="Scope=unknown", n=None, source=None, prom_status=prom_status, selector=selector),
            prom_unavailable,
        )
    return (
        _ScopeInfo(label=_scope_label(n), n=n, source=source, prom_status=prom_status, selector=selector),
        prom_unavailable,
    )


def _target_identity_missing(investigation: Investigation) -> bool:
    t = investigation.target
    tt = t.target_type

    if tt == "unknown":
        return True
    if tt == "pod":
        return not (t.namespace and t.pod and t.namespace != "Unknown" and t.pod != "Unknown")
    if tt == "service":
        return not (t.namespace and t.service and t.namespace != "Unknown" and t.service != "Unknown")
    if tt == "node":
        return not (t.instance and t.instance != "Unknown")
    if tt == "cluster":
        return not (t.cluster and t.cluster != "Unknown")
    # Workload target_type exists in model but is rarely used as primary identity today.
    if tt == "workload":
        return not (t.workload_kind and t.workload_name)
    return True


def _k8s_context_missing(investigation: Investigation) -> bool:
    """
    Scenario B: pod identity exists, but no K8s context in investigation.
    """
    f = investigation.analysis.features
    if f is None:
        return False
    if investigation.target.target_type != "pod":
        return False
    if not (
        investigation.target.namespace
        and investigation.target.pod
        and investigation.target.namespace != "Unknown"
        and investigation.target.pod != "Unknown"
    ):
        return False
    return "k8s.pod_info" in (f.quality.missing_inputs or [])


def _logs_missing(investigation: Investigation) -> bool:
    """
    Logs are "missing" when we attempted to fetch logs but got empty/unavailable, or logs are configured
    but currently unavailable.

    IMPORTANT: Do NOT treat "never attempted" as missing; that would create misleading blockers
    (e.g., non-pod alerts where no logs query exists).
    """
    ev = investigation.evidence.logs
    attempted = bool(
        (ev.logs_status is not None)
        or (ev.logs_backend is not None)
        or (ev.logs_reason is not None)
        or (ev.logs_query is not None)
        or (ev.logs and len(ev.logs) > 0)
    )
    if not attempted:
        return False

    status = (ev.logs_status or "").strip().lower()
    # Treat None/"" as missing only if it looked attempted/configured.
    return status in ("", "empty", "unavailable")


def _missing_labels(investigation: Investigation) -> List[str]:
    noise = investigation.analysis.noise
    if noise is None or noise.missing_labels is None:
        return []
    return list(noise.missing_labels.missing or [])


def _alertname(investigation: Investigation) -> str:
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    return str(labels.get("alertname") or "Unknown")


def _cmd(q: str) -> str:
    return q.strip()


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for x in items:
        k = x.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def build_base_decision(investigation: Investigation) -> Decision:
    """
    Build the base triage Decision for the current investigation.

    Assumes `investigation.analysis.features` has been computed when possible, but degrades gracefully.
    """
    scope, prom_unavailable = _parse_prom_scope(investigation)
    identity_missing = _target_identity_missing(investigation)
    k8s_missing = _k8s_context_missing(investigation)
    logs_missing = _logs_missing(investigation)
    job_pods_not_found = investigation.meta.get("blocked_mode") == "job_pods_not_found"

    discriminators: List[str] = []
    if prom_unavailable:
        discriminators.append("blocked_prometheus_unavailable")
    if identity_missing:
        discriminators.append("blocked_no_target_identity")
    if k8s_missing:
        discriminators.append("blocked_no_k8s_context")
    if job_pods_not_found:
        discriminators.append("blocked_job_pods_not_found")
    if logs_missing:
        discriminators.append("logs_missing")
    # Double-blocked marker (A2/D2)
    if prom_unavailable and identity_missing:
        discriminators.append("blocked_no_scope_no_identity")

    # Deterministic ordering
    order = {d: i for i, d in enumerate(_DISCRIMINATOR_ORDER)}
    discriminators = sorted(_dedupe_keep_order(discriminators), key=lambda d: order.get(d, 999))

    impact_label = "unknown"  # Phase 1: keep conservative; scoring phase comes later.

    if not discriminators:
        disc_txt = "Discriminator=present"
    elif len(discriminators) == 1:
        disc_txt = f"Discriminator={discriminators[0]}"
    else:
        disc_txt = f"Discriminators={','.join(discriminators)}"

    label = f"{scope.label} • Impact={impact_label} • {disc_txt}"

    # why bullets
    why: List[str] = []
    if scope.n is not None and scope.source:
        why.append(
            f"Scope: {scope.source}={int(scope.n) if scope.n.is_integer() else scope.n} (label={scope.label}) "
            f"(selector={scope.selector or 'n/a'} | prom_status={scope.prom_status})"
        )
    else:
        why.append(f"Scope: unknown (Prometheus meta-signals unavailable to agent: prom_status={scope.prom_status})")

    t = investigation.target
    why.append(
        "Target: "
        f"type={t.target_type} cluster={t.cluster or 'unknown'} namespace={t.namespace or 'unknown'} "
        f"pod={t.pod or 'unknown'} service={t.service or 'unknown'} instance={t.instance or 'unknown'}"
    )

    f = investigation.analysis.features
    if f is not None and f.quality.missing_impact_signals:
        why.append(f"Impact: unknown (missing: {','.join(f.quality.missing_impact_signals)})")
    else:
        why.append("Impact: unknown")

    if discriminators:
        why.append(f"Primary discriminator: {discriminators[0]}")
    else:
        why.append("Primary discriminator: present")

    miss = _missing_labels(investigation)
    if miss:
        why.append(f"Missing labels: {','.join(miss)}")

    # Evidence status: derived from missing_inputs when present
    evidence_bits: List[str] = []
    if f is not None:
        missing_inputs = set(f.quality.missing_inputs or [])
        evidence_bits.append("prom_scope=" + ("ok" if scope.prom_status == "ok" else "unavailable"))
        evidence_bits.append("k8s=" + ("missing" if "k8s.pod_info" in missing_inputs else "ok"))
        logs_status = (f.logs.status or "").strip().lower() if f.logs is not None else ""
        evidence_bits.append("logs=" + ("ok" if logs_status == "ok" else "missing"))
        # metrics are too broad; best-effort indicator
        metrics_missing = any(x.startswith("metrics.") for x in missing_inputs)
        evidence_bits.append("metrics=" + ("missing" if metrics_missing else "ok"))
        # change analysis presence: for now, infer by fields
        evidence_bits.append("changes=" + ("yes" if f.changes.rollout_within_window is not None else "no"))
    else:
        evidence_bits = ["prom_scope=" + ("ok" if scope.prom_status == "ok" else "unavailable")]
    why.append("Evidence: " + ", ".join(evidence_bits))

    # Logs details (minimal) when missing
    if logs_missing:
        evl = investigation.evidence.logs
        why.append(
            "Logs: "
            f"status={evl.logs_status or 'missing'} backend={evl.logs_backend or 'unknown'} "
            f"reason={evl.logs_reason or 'unknown'} query={evl.logs_query or 'n/a'}"
        )

    # next steps (PromQL-first, capped)
    next_steps: List[str] = []
    an = _alertname(investigation)

    # Scenario D: prom unavailable to agent -> ask on-call to verify in Prom UI
    if prom_unavailable:
        next_steps.extend(
            [
                _cmd(f'count(ALERTS{{alertname="{an}",alertstate="firing"}})'),
                _cmd(f'count by (cluster, namespace) (ALERTS{{alertname="{an}",alertstate="firing"}})'),
                "If PromQL works for on-call, treat as agent network/auth/config; if not, treat as Prometheus/platform incident.",
            ]
        )

    # Scenario A: identity missing -> label discovery
    if identity_missing:
        next_steps.extend(
            [
                _cmd(f'ALERTS{{alertname="{an}",alertstate="firing"}}'),
                _cmd(f'count by (cluster, namespace) (ALERTS{{alertname="{an}",alertstate="firing"}})'),
                _cmd(f'topk(20, count by (cluster, namespace, pod) (ALERTS{{alertname="{an}",alertstate="firing"}}))'),
                _cmd(
                    f'topk(20, count by (cluster, namespace, service) (ALERTS{{alertname="{an}",alertstate="firing"}}))'
                ),
            ]
        )

    # Scenario B: pod identity known but k8s context missing
    if k8s_missing:
        ns = investigation.target.namespace or "<namespace>"
        pod = investigation.target.pod or "<pod>"
        next_steps.extend(
            [
                _cmd('up{job=~"kube-state-metrics.*"}'),
                _cmd('count({__name__="kube_pod_status_phase"})'),
                _cmd(f'max by (cluster, namespace, pod) (kube_pod_status_phase{{namespace="{ns}",pod="{pod}"}})'),
                _cmd(
                    f'max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_waiting_reason{{namespace="{ns}",pod="{pod}"}})'
                ),
                _cmd(
                    f'max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{{namespace="{ns}",pod="{pod}"}})'
                ),
            ]
        )

    # Scenario: Job pods TTL-deleted
    if job_pods_not_found:
        ns = investigation.target.namespace or "<namespace>"
        job_name = investigation.target.workload_name or "<job-name>"
        next_steps.extend(
            [
                "Increase Job TTL: spec.ttlSecondsAfterFinished (currently may be too short for investigation)",
                _cmd(f"kubectl -n {ns} describe job {job_name}"),
                _cmd(f'kube_job_status_failed{{namespace="{ns}",job_name="{job_name}"}}'),
                _cmd(f'kube_job_status_succeeded{{namespace="{ns}",job_name="{job_name}"}}'),
                _cmd(f'kube_job_complete{{namespace="{ns}",job_name="{job_name}"}}'),
                _cmd(f'max_over_time(kube_job_failed{{namespace="{ns}",job_name="{job_name}"}}[24h])'),
            ]
        )

    # Scenario C: logs missing -> proceed with non-log discriminators and verify logs backend
    if logs_missing:
        # Only include pod-specific discriminators when we actually know the pod identity.
        if investigation.target.target_type == "pod" and investigation.target.namespace and investigation.target.pod:
            ns = investigation.target.namespace
            pod = investigation.target.pod
            next_steps.extend(
                [
                    _cmd(f'max by (cluster, namespace, pod) (kube_pod_status_phase{{namespace="{ns}",pod="{pod}"}})'),
                    _cmd(
                        f'max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{{namespace="{ns}",pod="{pod}"}})'
                    ),
                    _cmd(f'increase(kube_pod_container_status_restarts_total{{namespace="{ns}",pod="{pod}"}}[30m])'),
                ]
            )
        else:
            # No pod identity: fall back to discovery (Scenario A style) rather than emitting invalid pod queries.
            next_steps.extend(
                [
                    _cmd(f'ALERTS{{alertname="{an}",alertstate="firing"}}'),
                    _cmd(f'count by (cluster, namespace) (ALERTS{{alertname="{an}",alertstate="firing"}})'),
                ]
            )

        # Logs dependency check is still useful regardless of target type.
        next_steps.append(_cmd('count(ALERTS{alertstate="firing",alertname=~".*(Loki|VictoriaLogs|Victoria).*"})'))

    # Only include the generic policy/escalation reminder when we are blocked/uncertain or the alert is meta/generic.
    # For strong-symptom, unblocked incidents (e.g. OOMKilled), this reads like hand-waving.
    fam = f.family if f is not None else None
    if discriminators or fam in ("meta", "generic"):
        next_steps.append(
            "Check runbook/owner policy for expected flaps vs incident; escalate with scope + discriminators + discovery output."
        )

    next_steps = _dedupe_keep_order(next_steps)[:7]

    return Decision(label=label, why=why[:10], next=next_steps)
