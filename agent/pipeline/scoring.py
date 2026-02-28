"""Deterministic scoring (features → scores → verdict).

This module is intentionally explainable:
- start from 0
- add/subtract fixed deltas based on computed features
- clamp 0..100
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from agent.core.models import (
    DerivedFeatures,
    DeterministicScores,
    DeterministicVerdict,
    Investigation,
    ScoreBreakdownItem,
)
from agent.image_pull import classify_pull_error, parse_image_ref
from agent.logs_select import select_best_line


def _clamp_0_100(x: int) -> int:
    return max(0, min(100, x))


def _add(
    breakdown: List[ScoreBreakdownItem],
    reason_codes: List[str],
    *,
    code: str,
    delta: int,
    feature_ref: Optional[str] = None,
    why: Optional[str] = None,
) -> int:
    if delta == 0:
        return 0
    breakdown.append(ScoreBreakdownItem(code=code, delta=delta, feature_ref=feature_ref, why=why))
    if code not in reason_codes:
        reason_codes.append(code)
    return delta


def _prom_scalar(v: object) -> Optional[float]:
    """
    Best-effort scalar extraction from `query_prometheus_instant()`-like vector results.

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


def score_crashloop(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []

    impact = 0
    confidence = 0
    noise = 0

    # Impact (symptom severity only)
    if (f.k8s.waiting_reason or "").lower() == "crashloopbackoff":
        impact += _add(
            breakdown,
            reasons,
            code="CRASHLOOPBACKOFF",
            delta=60,
            feature_ref="k8s.waiting_reason",
            why="CrashLoopBackOff",
        )
    if (f.k8s.restart_rate_5m_max or 0) >= 3:
        # In practice, restart spikes are the primary on-call pain for crashloop alerts.
        impact += _add(
            breakdown,
            reasons,
            code="RESTART_RATE_HIGH",
            delta=35,
            feature_ref="k8s.restart_rate_5m_max",
            why=f"max={f.k8s.restart_rate_5m_max}",
        )
    if f.k8s.ready is False:
        impact += _add(breakdown, reasons, code="POD_NOT_READY", delta=20, feature_ref="k8s.ready", why="Ready=False")
    if (f.k8s.warning_events_count or 0) >= 1:
        impact += _add(
            breakdown,
            reasons,
            code="WARNING_EVENTS",
            delta=10,
            feature_ref="k8s.warning_events_count",
            why=f"count={f.k8s.warning_events_count}",
        )
    # Widespread scope bumps impact (how bad if real)
    try:
        if investigation.analysis.noise and isinstance(investigation.analysis.noise.prometheus, dict):
            fi = investigation.analysis.noise.prometheus.get("firing_instances")
            fi = float(fi) if fi is not None else None
            if fi is not None:
                if fi >= 20:
                    impact += _add(
                        breakdown,
                        reasons,
                        code="SCOPE_WIDESPREAD",
                        delta=20,
                        feature_ref="noise.prometheus.firing_instances",
                        why=f"firing_instances={int(fi)}",
                    )
                elif fi >= 5:
                    impact += _add(
                        breakdown,
                        reasons,
                        code="SCOPE_MULTI_INSTANCE",
                        delta=10,
                        feature_ref="noise.prometheus.firing_instances",
                        why=f"firing_instances={int(fi)}",
                    )
    except Exception:
        pass

    # Confidence (evidence quality + agreement + contradictions)
    if (f.k8s.waiting_reason or "").lower() == "crashloopbackoff":
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_K8S_WAITING_REASON",
            delta=35,
            feature_ref="k8s.waiting_reason",
            why="waiting_reason present",
        )
    if (f.k8s.restart_rate_5m_max is not None) and (f.k8s.restart_rate_5m_max > 0):
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_RESTART_METRIC",
            delta=35,
            feature_ref="k8s.restart_rate_5m_max",
            why="restart metric corroborates",
        )
    if (f.k8s.warning_events_count or 0) >= 1:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_WARNING_EVENTS",
            delta=10,
            feature_ref="k8s.warning_events_count",
            why="warning events corroborate",
        )
    # Strong crashloop corroboration: BackOff/Unhealthy events usually mean real impact (not just a stale alert)
    try:
        ev_reasons = [(ev.reason or "").lower() for ev in (f.k8s.recent_event_reasons_top or [])]
        if any(r in ("backoff", "unhealthy", "killing") for r in ev_reasons):
            confidence += _add(
                breakdown,
                reasons,
                code="EVID_K8S_EVENTS_CRASHLOOP",
                delta=20,
                feature_ref="k8s.recent_event_reasons_top",
                why="BackOff/Unhealthy/Killing events present",
            )
    except Exception:
        pass
    if "logs" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LOGS",
            delta=-15,
            feature_ref="quality.missing_inputs",
            why="logs unavailable",
        )
    # Confidence penalty only for missing namespace/pod labels (attribution risk)
    if "labels.namespace" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_NAMESPACE",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="namespace label missing",
        )
    if "labels.pod" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_POD",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="pod label missing",
        )
    for cf in f.quality.contradiction_flags:
        confidence += _add(
            breakdown,
            reasons,
            code=cf,
            delta=-40,
            feature_ref="quality.contradiction_flags",
            why="contradiction detected",
        )

    # Noise (start at 0)
    # Add noise signals
    if (investigation.alert.labels or {}).get("alertname") == "InfoInhibitor":
        noise += _add(
            breakdown, reasons, code="META_ALERT", delta=60, feature_ref="alert.alertname", why="InfoInhibitor is meta"
        )
    # Add flap/cardinality noise (deterministic)
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            # +10 per ephemeral label, capped at +30
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )
    # Subtract strong actionable signals
    if (f.k8s.waiting_reason or "").lower() == "crashloopbackoff":
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_CRASHLOOP",
            delta=-30,
            feature_ref="k8s.waiting_reason",
            why="strong symptom reduces noise",
        )
    if (f.k8s.restart_rate_5m_max or 0) >= 3:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_RESTARTS",
            delta=-10,
            feature_ref="k8s.restart_rate_5m_max",
            why="restart spike reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    classification = "informational"
    if confidence < 40:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    # More actionable on-call wording: cite concrete evidence instead of generic guidance.
    bits: List[str] = []
    if f.k8s.restart_rate_5m_max is not None:
        bits.append(f"restart_rate_5m_max={f.k8s.restart_rate_5m_max:.2f}")
    # Probe failures (common in crashlooping workloads)
    probe_bits: List[str] = []
    status_codes: List[str] = []
    try:
        for ev in (f.k8s.recent_event_reasons_top or [])[:10]:
            msg = (ev.message or "").lower()
            if "readiness probe failed" in msg:
                probe_bits.append("readiness")
            if "liveness probe failed" in msg:
                probe_bits.append("liveness")
            m = re.search(r"statuscode\\s*:\\s*(\\d+)", msg)
            if m:
                status_codes.append(m.group(1))
    except Exception:
        probe_bits = []
        status_codes = []

    def _dedupe_keep_order(xs: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    probe_bits = _dedupe_keep_order([p for p in probe_bits if p])
    status_codes = _dedupe_keep_order([c for c in status_codes if c])
    if probe_bits:
        tail = ""
        if status_codes:
            tail = " (HTTP " + ",".join(status_codes[:3]) + ")"
        bits.append("probe_failures=" + "/".join(probe_bits) + tail)

    # Last termination
    try:
        t = None
        for x in f.k8s.container_last_terminated_top or []:
            if investigation.target.container and x.container == investigation.target.container:
                t = x
                break
        if t is None and f.k8s.container_last_terminated_top:
            t = f.k8s.container_last_terminated_top[0]
        if t is not None:
            t_bits = []
            if t.reason:
                t_bits.append(str(t.reason))
            if t.exit_code is not None:
                t_bits.append(f"exit={t.exit_code}")
            if t_bits:
                bits.append(f"last_terminated={t.container}(" + ",".join(t_bits) + ")")
    except Exception:
        pass

    # A single “best” log line if available (shared heuristics; avoids banner/config false positives)
    top_log: Optional[str] = select_best_line(investigation.evidence.logs.logs or [])

    prefix = "CrashLoopBackOff" if (f.k8s.waiting_reason or "").lower() == "crashloopbackoff" else "Crashloop symptoms"
    one_liner = prefix
    if bits:
        one_liner += ": " + "; ".join(bits[:4])
    if top_log:
        one_liner += f"; top_log={top_log}"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="crashloop",
        one_liner=one_liner,
        next_steps=[
            "Check the **Top events** and **last termination** in the Appendix (probe failures vs BackOff vs explicit errors).",
            "Use the Appendix **Logs** snippet (prioritized errors); if it’s only startup noise, expand the time window and re-run.",
            "If probes are failing (e.g., HTTP 503), validate the dependency/readiness endpoint and consider rollback if there was a recent change.",
        ],
    )

    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_pod_not_healthy(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    phase = (f.k8s.pod_phase or "").lower().strip()
    ready = f.k8s.ready
    waiting = (f.k8s.waiting_reason or "").strip() if f.k8s.waiting_reason else None
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname_raw = str(labels.get("alertname") or "")
    lname = alertname_raw.lower()
    severity = str(labels.get("severity") or "").strip().lower()

    # Scope: best-effort number of firing instances (derived from ALERTS by noise module)
    firing_instances = None
    if investigation.analysis.noise and isinstance(investigation.analysis.noise.prometheus, dict):
        firing_instances = investigation.analysis.noise.prometheus.get("firing_instances")
    try:
        if firing_instances is not None:
            firing_instances = float(firing_instances)
    except Exception:
        firing_instances = None

    # -----------------------
    # Impact (severity if true)
    # -----------------------
    # Base unhealthy signal from metrics (kube_pod_status_phase / similar).
    if f.metrics.pod_unhealthy_phase_observed:
        impact += _add(
            breakdown,
            reasons,
            code="POD_UNHEALTHY_SIGNAL",
            delta=50,  # lowered from 70: phase signal alone doesn't tell "how bad"
            feature_ref="metrics.pod_unhealthy_phase_observed",
            why="phase signal > 0",
        )

    # Scope/severity should influence impact for pod health alerts (on-call priority).
    if severity == "critical" or "critical" in lname:
        impact += _add(
            breakdown,
            reasons,
            code="SEVERITY_CRITICAL",
            delta=20,
            feature_ref="labels.severity",
            why=f"severity={severity or 'n/a'}",
        )
    if isinstance(firing_instances, (int, float)):
        if firing_instances >= 50:
            impact += _add(
                breakdown,
                reasons,
                code="IMPACT_WIDESPREAD",
                delta=25,
                feature_ref="noise.prometheus.firing_instances",
                why=f"firing_instances={int(firing_instances)}",
            )
        elif firing_instances >= 20:
            impact += _add(
                breakdown,
                reasons,
                code="IMPACT_BROAD",
                delta=15,
                feature_ref="noise.prometheus.firing_instances",
                why=f"firing_instances={int(firing_instances)}",
            )
        elif firing_instances >= 5:
            impact += _add(
                breakdown,
                reasons,
                code="IMPACT_MULTI",
                delta=5,
                feature_ref="noise.prometheus.firing_instances",
                why=f"firing_instances={int(firing_instances)}",
            )

    # Phase-specific impact
    if phase == "failed":
        impact += _add(
            breakdown, reasons, code="POD_PHASE_FAILED", delta=40, feature_ref="k8s.pod_phase", why="phase=Failed"
        )
    elif phase == "pending":
        impact += _add(
            breakdown, reasons, code="POD_PHASE_PENDING", delta=35, feature_ref="k8s.pod_phase", why="phase=Pending"
        )
    elif phase == "unknown":
        impact += _add(
            breakdown, reasons, code="POD_PHASE_UNKNOWN", delta=30, feature_ref="k8s.pod_phase", why="phase=Unknown"
        )

    # Ready signal adds impact only when explicitly false (ready=None means you don't know)
    if ready is False:
        impact += _add(breakdown, reasons, code="POD_NOT_READY", delta=25, feature_ref="k8s.ready", why="ready=False")

    # Restart spike / crashloop-like patterns (if available)
    rr = f.k8s.restart_rate_5m_max
    if rr is not None and rr >= 3:
        impact += _add(
            breakdown,
            reasons,
            code="RESTART_RATE_HIGH",
            delta=30,
            feature_ref="k8s.restart_rate_5m_max",
            why=f"restart_rate_5m_max={rr:.2f}",
        )
    elif rr is not None and rr >= 1:
        impact += _add(
            breakdown,
            reasons,
            code="RESTART_RATE_ELEVATED",
            delta=15,
            feature_ref="k8s.restart_rate_5m_max",
            why=f"restart_rate_5m_max={rr:.2f}",
        )

    # Waiting reason can indicate specific high-impact states (if you populate it)
    if waiting:
        if waiting in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"):
            impact += _add(
                breakdown,
                reasons,
                code="WAITING_REASON_CRITICAL",
                delta=35,
                feature_ref="k8s.waiting_reason",
                why=f"reason={waiting}",
            )
        elif waiting in ("ContainerCreating", "PodInitializing"):
            impact += _add(
                breakdown,
                reasons,
                code="WAITING_REASON_PROGRESSING",
                delta=10,
                feature_ref="k8s.waiting_reason",
                why=f"reason={waiting}",
            )

    # -----------------------
    # Confidence (is it real + correctly attributed?)
    # -----------------------
    if f.metrics.pod_unhealthy_phase_observed:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_PHASE_METRIC",
            delta=35,
            feature_ref="metrics.pod_unhealthy_phase_observed",
            why="kube-state-metrics corroborates",
        )

    # Explicit target labels (namespace+pod) are strong attribution evidence for pod health alerts.
    if (labels.get("namespace") or labels.get("Namespace")) and (
        labels.get("pod") or labels.get("pod_name") or labels.get("podName")
    ):
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_TARGET_LABELS",
            delta=20,
            feature_ref="labels.namespace,labels.pod",
            why="namespace+pod present",
        )

    # K8s API corroboration: phase / ready / restarts / events
    if phase in ("pending", "failed", "unknown"):
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_K8S_PHASE",
            delta=25,
            feature_ref="k8s.pod_phase",
            why=f"phase={f.k8s.pod_phase}",
        )

    if ready is not None:
        confidence += _add(
            breakdown, reasons, code="EVID_K8S_READY_FIELD", delta=10, feature_ref="k8s.ready", why=f"ready={ready}"
        )

    if f.k8s.restart_count is not None or f.k8s.restart_rate_5m_max is not None:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_RESTART_SIGNAL",
            delta=10,
            feature_ref="k8s.restart_count,k8s.restart_rate_5m_max",
            why="restart signal present",
        )

    # Events (if you collect them). warning_events_count=0 is evidence too, but only if field exists.
    if f.k8s.warning_events_count is not None:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_EVENTS_QUERIED",
            delta=5,
            feature_ref="k8s.warning_events_count",
            why=f"warnings={f.k8s.warning_events_count}",
        )

    # Missing logs should not reduce confidence for pod health (logs are often irrelevant for scheduling/mount issues)
    # Keep a note as a reason code if you want, but don't penalize confidence by default.
    if "logs" in f.quality.missing_inputs:
        _add(
            breakdown,
            reasons,
            code="LOGS_UNAVAILABLE",
            delta=0,
            feature_ref="quality.missing_inputs",
            why="logs unavailable",
        )

    # K8s context missing: do not treat as "false", but be explicit. KSM metrics can still be valid.
    if "k8s.pod_info" in f.quality.missing_inputs:
        _add(
            breakdown,
            reasons,
            code="K8S_CONTEXT_MISSING",
            delta=0,
            feature_ref="quality.missing_inputs",
            why="k8s pod_info missing",
        )

    # Missing key labels hurts confidence
    if "labels.namespace" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_NAMESPACE",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="namespace label missing",
        )
    if "labels.pod" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_POD",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="pod label missing",
        )

    # If we don't have any root-cause signals, reduce confidence a bit.
    # With enriched K8s features, we can be more precise here.
    has_rootcause = bool(
        waiting
        or (f.k8s.not_ready_conditions)
        or (f.k8s.container_waiting_reasons_top)
        or (f.k8s.container_last_terminated_top)
        or (f.k8s.recent_event_reasons_top)
        or (f.k8s.status_reason)
        or (f.k8s.status_message)
    )
    lacks_rootcause = (
        (ready is None)
        and (f.k8s.restart_count is None)
        and (f.k8s.restart_rate_5m_max is None)
        and (f.k8s.warning_events_count in (None, 0))
        and (not has_rootcause)
    )
    if lacks_rootcause:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_ROOTCAUSE_SIGNALS",
            delta=-15,
            feature_ref="k8s.ready,k8s.restart_count,k8s.warning_events_count,k8s.waiting_reason",
            why="ready/restarts/events/waiting_reason not available",
        )

    # -----------------------
    # Noise
    # -----------------------
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )

        # IMPORTANT: For kube-state-metrics-backed alerts, these labels are scraper metadata, not noise.
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        eph = [e for e in eph if e not in ("job", "instance", "endpoint", "service", "container")]
        # If workload is known, pod-level cardinality is less of an issue for investigations.
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]

        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

        # NOTE: do NOT treat many instances as "noise" for pod health alerts; wide scope usually increases impact.

    # Strong symptom reduces noise a bit (but don't overdo it)
    if f.metrics.pod_unhealthy_phase_observed:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_POD_UNHEALTHY",
            delta=-10,
            feature_ref="metrics.pod_unhealthy_phase_observed",
            why="strong symptom reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    # -----------------------
    # Classification
    # -----------------------
    classification = "informational"

    # "artifact" should mean we can't attribute/reproduce, not "logs missing"
    if confidence < 30:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    # -----------------------
    # Verdict templating (deterministic, root-cause oriented)
    # -----------------------
    phase_txt = f.k8s.pod_phase or "Unknown"

    def _s(x: Optional[str]) -> str:
        return str(x).strip() if x is not None else ""

    # Root-cause selection (priority order)
    events = list(f.k8s.recent_event_reasons_top or [])
    waitings = list(f.k8s.container_waiting_reasons_top or [])
    last_terms = list(f.k8s.container_last_terminated_top or [])
    conds = list(f.k8s.not_ready_conditions or [])

    def _find_event(reason_set: set[str]) -> Optional[Any]:
        for e in events:
            r = _s(getattr(e, "reason", None))
            if r in reason_set:
                return e
        return None

    def _find_waiting(reason_set: set[str]) -> Optional[Any]:
        for w2 in waitings:
            r = _s(getattr(w2, "reason", None))
            if r in reason_set:
                return w2
        return None

    def _find_last_term(reason_set: set[str]) -> Optional[Any]:
        for t2 in last_terms:
            r = _s(getattr(t2, "reason", None))
            if r in reason_set:
                return t2
        return None

    ev_sched = _find_event({"FailedScheduling"})
    ev_vol = _find_event({"FailedMount", "FailedAttachVolume", "FailedUnMount", "FailedMapVolume"})
    w_img = _find_waiting({"ImagePullBackOff", "ErrImagePull"})
    w_cfg = _find_waiting({"CreateContainerConfigError", "CreateContainerError"})
    w_crash = _find_waiting({"CrashLoopBackOff"})
    lt_oom = _find_last_term({"OOMKilled"})
    lt_err = _find_last_term({"Error"})

    # Default fallback
    # If K8s context is missing, avoid the misleading "phase Unknown" one-liner and be explicit about blockers.
    if "k8s.pod_info" in f.quality.missing_inputs and f.metrics.pod_unhealthy_phase_observed:
        scope_txt = (
            f"{int(firing_instances)} instances" if isinstance(firing_instances, (int, float)) else "multiple instances"
        )
        one_liner = f"Pod health alert fired for ~{scope_txt}; kube-state-metrics indicates unhealthy phase, but agent could not fetch pod status/events (K8s context missing)."
    else:
        one_liner = f"Pod phase is `{phase_txt}` in this window."
    if ready is False:
        one_liner += " Ready=`False`."
    elif ready is True:
        one_liner += " Ready=`True`."

    next_steps: List[str] = []
    if lacks_rootcause:
        next_steps.append(
            "Collect pod status details (conditions + containerStatuses waiting/terminated) and recent Pod Events; current evidence lacks root-cause signals."
        )

    if ev_sched is not None:
        msg = _s(getattr(ev_sched, "message", None))
        one_liner = f"Pod {phase_txt}: FailedScheduling" + (f" — {msg}" if msg else "")
        next_steps.extend(
            [
                "Check the event message for the exact constraint (resources/taints/affinity/quotas).",
                "Validate CPU/memory requests vs available node capacity and namespace quotas.",
                "Inspect node selectors/taints/tolerations and affinity rules for mismatches.",
            ]
        )
    elif ev_vol is not None:
        r = _s(getattr(ev_vol, "reason", None))
        msg = _s(getattr(ev_vol, "message", None))
        one_liner = f"Pod {phase_txt}: {r}" + (f" — {msg}" if msg else "")
        next_steps.extend(
            [
                "Check PVC/PV status and whether volumes are bound and accessible.",
                "Inspect CSI driver/controller health and node-level storage connectivity.",
                "Review recent storage-related changes (storage class, IAM, nodes).",
            ]
        )
    elif w_img is not None:
        c = _s(getattr(w_img, "container", None)) or "container"
        r = _s(getattr(w_img, "reason", None)) or "ImagePull"
        msg = _s(getattr(w_img, "message", None))
        one_liner = f"Container `{c}`: {r}" + (f" — {msg}" if msg else "")
        # Evidence-driven next steps (prefer concrete findings over generic advice)
        diag = getattr(investigation.evidence.k8s, "image_pull_diagnostics", None)
        diag = diag if isinstance(diag, dict) else {}

        img = None
        try:
            img = str(diag.get("image") or "").strip() or None
        except Exception:
            img = None
        img_ref = parse_image_ref(img or "")

        # Bucket from message (fallback to diag bucket if set by playbook)
        bucket = None
        ev = None
        try:
            bucket = str(diag.get("error_bucket") or "").strip() or None
            ev = str(diag.get("error_evidence") or "").strip() or None
        except Exception:
            bucket, ev = None, None
        if not bucket:
            bucket, ev = classify_pull_error(msg or "")

        # Optional ECR verification result
        ecr_check = diag.get("ecr_check") if isinstance(diag.get("ecr_check"), dict) else None

        # ServiceAccount wiring (SA-only; do not read secrets)
        sa_name = diag.get("service_account_name")
        sa_pull = diag.get("service_account_image_pull_secrets")
        if isinstance(sa_name, str) and sa_name.strip() and isinstance(sa_pull, list) and len(sa_pull) == 0:
            next_steps.append(f"ServiceAccount `{sa_name}` has **no** `imagePullSecrets` configured.")

        if bucket == "not_found":
            if img_ref.raw:
                if img_ref.tag:
                    next_steps.append(
                        f"Registry reported **NotFound**; image tag likely missing: `{img_ref.repository}:{img_ref.tag}`"
                    )
                elif img_ref.digest:
                    next_steps.append(
                        f"Registry reported **NotFound**; image digest likely missing: `{img_ref.repository}@{img_ref.digest}`"
                    )
                else:
                    next_steps.append(
                        f"Registry reported **NotFound** for image `{img_ref.raw}` (repo/tag/digest may be wrong)."
                    )
            else:
                next_steps.append("Registry reported **NotFound**; image reference (repo/tag/digest) is likely wrong.")
        elif bucket == "auth":
            next_steps.append(
                "Registry reported **unauthorized/denied**; this is likely an auth/permissions issue (imagePullSecrets/IAM)."
            )
        elif bucket == "tls":
            next_steps.append(
                "Registry pull failed due to **TLS/certificate** errors; validate trust chain / proxy / registry certs on nodes."
            )
        elif bucket == "network":
            next_steps.append(
                "Registry pull failed due to **network/DNS/timeouts**; validate node egress + DNS to the registry endpoint."
            )
        else:
            next_steps.append(
                "Image pull failed; use the exact kubelet error to distinguish NotFound vs auth vs network/TLS."
            )

        # If ECR verification ran, surface it explicitly.
        if img_ref.is_ecr and isinstance(ecr_check, dict):
            st = str(ecr_check.get("status") or "")
            detail = str(ecr_check.get("detail") or "")
            if st == "missing":
                next_steps.append("ECR verification: **image not found** (tag/digest missing).")
            elif st == "exists":
                next_steps.append("ECR verification: image **exists**; focus on auth wiring or node reachability.")
            elif st.startswith("skipped"):
                next_steps.append("ECR verification: not run (" + detail + ").")
            else:
                next_steps.append("ECR verification: error (" + detail + ").")

        # Copy/paste follow-ups (small set)
        if img_ref.is_ecr and img_ref.ecr_region and img_ref.repository:
            if img_ref.tag:
                next_steps.append(
                    f'AWS CLI: `aws ecr describe-images --region {img_ref.ecr_region} --repository-name "{img_ref.repository}" --image-ids imageTag={img_ref.tag}`'
                )
            elif img_ref.digest:
                next_steps.append(
                    f'AWS CLI: `aws ecr describe-images --region {img_ref.ecr_region} --repository-name "{img_ref.repository}" --image-ids imageDigest={img_ref.digest}`'
                )
        if ev:
            next_steps.append(f"Error excerpt: `{ev}`")
    elif w_cfg is not None:
        c = _s(getattr(w_cfg, "container", None)) or "container"
        r = _s(getattr(w_cfg, "reason", None)) or "CreateContainerConfigError"
        msg = _s(getattr(w_cfg, "message", None))
        one_liner = f"Container `{c}`: {r}" + (f" — {msg}" if msg else "")
        next_steps.extend(
            [
                "Check referenced ConfigMaps/Secrets and env var valueFrom fields for missing keys.",
                "Review container spec (command/args/volumes) for invalid references.",
                "Use Events/describe output to identify the exact missing resource.",
            ]
        )
    elif w_crash is not None:
        c = _s(getattr(w_crash, "container", None)) or "container"
        # Try to attach last termination for this container if present.
        last_for_container = None
        for t2 in last_terms:
            if _s(getattr(t2, "container", None)) == c:
                last_for_container = t2
                break
        if last_for_container is None:
            last_for_container = lt_oom or lt_err
        tail = ""
        if last_for_container is not None:
            lr = _s(getattr(last_for_container, "reason", None))
            ec = getattr(last_for_container, "exit_code", None)
            if lr or ec is not None:
                tail = f" (last={lr or 'terminated'}, exitCode={ec})"
        one_liner = f"Container `{c}`: CrashLoopBackOff{tail}"
        next_steps.extend(
            [
                "Inspect previous container logs (`kubectl logs --previous`) and current startup logs.",
                "Check recent config/deploy changes and dependency connectivity (DB, cache, etc.).",
                "If exitCode=137/143, treat as termination/resource pressure (OOM/signal).",
            ]
        )
    elif lt_oom is not None:
        c = _s(getattr(lt_oom, "container", None)) or "container"
        ec = getattr(lt_oom, "exit_code", None)
        one_liner = f"Container `{c}`: last terminated OOMKilled" + (f" (exitCode={ec})" if ec is not None else "")
        next_steps.extend(
            [
                "Review memory requests/limits and recent memory usage; increase limit if justified.",
                "Look for allocation spikes and GC/heap growth in app metrics/logs.",
                "Check node memory pressure / eviction signals.",
            ]
        )
    elif lt_err is not None:
        c = _s(getattr(lt_err, "container", None)) or "container"
        ec = getattr(lt_err, "exit_code", None)
        one_liner = f"Container `{c}`: last terminated Error" + (f" (exitCode={ec})" if ec is not None else "")
        next_steps.extend(
            [
                "Inspect application logs around the termination time and the error path.",
                "Validate configuration/env vars and dependency health (DB, cache, network).",
                "Correlate with recent rollout/config changes.",
            ]
        )
    elif conds:
        c0 = conds[0]
        one_liner = f"Pod {phase_txt}: condition `{c0.type}` is `{c0.status}`" + (
            f" (reason={c0.reason})" if c0.reason else ""
        )
        next_steps.extend(
            [
                "Use `kubectl describe pod` to inspect condition reason/message and related Events.",
                "If PodScheduled=False: treat as scheduling constraint (taints/affinity/resources).",
                "If ContainersReady/Ready=False: inspect container states (waiting/terminated) and logs.",
            ]
        )
    elif f.k8s.status_reason or f.k8s.status_message:
        one_liner = f"Pod {phase_txt}: {_s(f.k8s.status_reason) or 'unhealthy'}" + (
            f" — {_s(f.k8s.status_message)}" if f.k8s.status_message else ""
        )
        next_steps.extend(
            [
                "Check pod status reason/message and correlate with conditions + Events.",
                "Inspect containerStatuses (waiting/terminated/lastState) for the immediate cause.",
                "Correlate with recent workload rollout/config changes.",
            ]
        )
    else:
        # Phase-based fallback if we couldn't extract a specific root cause
        if phase == "pending":
            next_steps.extend(
                [
                    "Check Events for scheduling and volume mount issues: FailedScheduling / FailedMount / FailedAttachVolume.",
                    "Check node capacity/taints/tolerations and whether required PVCs exist and are bound.",
                ]
            )
        elif phase == "failed":
            next_steps.extend(
                [
                    "Inspect container last termination reason (exitCode, OOMKilled, Error) and pod status reason/message.",
                    "Confirm whether this is a Job/one-shot pod vs a long-running Deployment replica.",
                ]
            )
        elif phase == "unknown":
            next_steps.extend(
                [
                    "Check node readiness and kubelet connectivity for the node running this pod.",
                    "Review cluster/network issues that could prevent status updates (API/kubelet).",
                ]
            )
        else:
            next_steps.append("Review pod Events and conditions to determine why it is marked unhealthy.")

    # Change correlation still useful
    next_steps.append("Correlate with recent workload rollout changes if any occurred near the alert window.")

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="pod_not_healthy",
        one_liner=one_liner,
        next_steps=next_steps[:6],
    )

    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_cpu_throttling(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    t = f.metrics.cpu_throttle_p95_pct
    near = f.metrics.cpu_near_limit is True

    # Compute usage_vs_limit deterministically.
    # Prefer inferred top-throttled-container ratio when container label is missing.
    usage_vs_limit = None
    try:
        if (
            investigation.target.container is None
            and f.metrics.cpu_throttle_top_container_usage_limit_ratio is not None
        ):
            usage_vs_limit = float(f.metrics.cpu_throttle_top_container_usage_limit_ratio)
        else:
            u = f.metrics.cpu_usage_p95_cores
            lim = f.metrics.cpu_limit_cores
            if u is not None and lim is not None and lim > 0:
                usage_vs_limit = float(u) / float(lim)
    except Exception:
        usage_vs_limit = None

    # -----------------------
    # Impact (severity if true)
    # -----------------------
    if t is not None and t > 25:
        # Scale impact by near-limit (CPU-bound) as chosen.
        delta = 60 if near else 30
        impact += _add(
            breakdown,
            reasons,
            code="THROTTLING_P95_HIGH",
            delta=delta,
            feature_ref="metrics.cpu_throttle_p95_pct",
            why=f"p95={t:.2f}% (near_limit={near})",
        )

    # -----------------------
    # Confidence (is it real + correctly attributed?)
    # -----------------------
    if t is not None:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_THROTTLING_METRIC",
            delta=40,
            feature_ref="metrics.cpu_throttle_p95_pct",
            why="throttling series present",
        )

    # If we can compute usage/limit at all, that's evidence quality (even if not near limit)
    if usage_vs_limit is not None:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_USAGE_LIMIT_COMPUTED",
            delta=20,
            feature_ref="metrics.cpu_usage_p95_cores,metrics.cpu_limit_cores",
            why=f"p95 usage/limit={usage_vs_limit:.2f}",
        )

    # Additional confidence terms (cap each at ~40, avoid conflating with impactfulness)
    if f.k8s.ready is True:
        confidence += _add(
            breakdown, reasons, code="EVID_K8S_READY", delta=10, feature_ref="k8s.ready", why="pod Ready=True"
        )
    if investigation.target.namespace and investigation.target.pod:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_TARGET_LABELS",
            delta=10,
            feature_ref="target.namespace,target.pod",
            why="namespace+pod present",
        )

    # Contradiction: alert fired but we can't reproduce meaningful throttling in window
    if t is not None and t <= 1.0:
        confidence += _add(
            breakdown,
            reasons,
            code="THROTTLING_NOT_REPRODUCED",
            delta=-40,
            feature_ref="metrics.cpu_throttle_p95_pct",
            why=f"p95={t:.2f}%",
        )
        noise += _add(
            breakdown,
            reasons,
            code="NOISE_RECOVERED_OR_MISMATCH",
            delta=20,
            feature_ref="metrics.cpu_throttle_p95_pct",
            why="alert may have recovered or query/label mismatch",
        )

    # Key contradiction: throttling high but CPU usage far from limit
    # This is often low-impact, bursty, or a metrics/labeling artifact.
    if t is not None and t > 25 and usage_vs_limit is not None and usage_vs_limit < 0.2:
        confidence += _add(
            breakdown,
            reasons,
            code="THROTTLING_HIGH_BUT_USAGE_LOW",
            delta=-10,
            feature_ref="metrics.cpu_usage_p95_cores,metrics.cpu_limit_cores",
            why=f"usage/limit={usage_vs_limit:.2f} < 0.20",
        )
        noise += _add(
            breakdown,
            reasons,
            code="NOISE_POSSIBLE_ARTIFACT",
            delta=15,
            feature_ref="metrics.cpu_throttle_p95_pct",
            why="high throttling with low CPU usage",
        )

    # Missing labels reduces confidence (note: your missing_inputs currently has "logs"; these checks remain harmless)
    if "labels.namespace" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_NAMESPACE",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="namespace label missing",
        )
    if "labels.pod" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_POD",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="pod label missing",
        )

    # -----------------------
    # Noise
    # -----------------------
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )

        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        # If we already know the owning workload, pod-level cardinality is expected; don't penalize 'pod'.
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

    # Strong symptom reduces noise slightly, but only when not contradicted by "usage low"
    if t is not None and t > 25 and not (usage_vs_limit is not None and usage_vs_limit < 0.2):
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_THROTTLING",
            delta=-10,
            feature_ref="metrics.cpu_throttle_p95_pct",
            why="strong symptom reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    # -----------------------
    # Classification
    # -----------------------
    classification = "informational"

    # Reserve "artifact" for genuinely low-confidence attribution/repro
    if confidence < 30 and (
        (t is None)
        or (t <= 1.0)
        or ("labels.namespace" in f.quality.missing_inputs)
        or ("labels.pod" in f.quality.missing_inputs)
    ):
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    # For throttling, require near-limit for "actionable" (otherwise it is usually informational)
    elif impact >= 60 and confidence >= 60 and noise <= 60 and near:
        classification = "actionable"

    # -----------------------
    # Verdict templating (deterministic)
    # -----------------------
    # Build one_liner + next_steps based on the same gates that drive scoring.
    if confidence < 30:
        one_liner = (
            "Insufficient or inconsistent evidence to confirm CPU throttling for this pod in the selected window."
        )
        next_steps = [
            'Verify alert labels (namespace/pod/container) and ensure PromQL filters exclude infra/empty containers (container!=POD, container!="", image!="").',
            "Re-run investigation using the alert start window or widen the time range.",
        ]
    elif t is not None and t > 25 and near:
        one_liner = (
            "CPU throttling is high and CPU usage is near the configured limit; this is likely capacity-related."
        )
        next_steps = [
            "Increase CPU limit or scale replicas and re-check throttling p95.",
            "Correlate with latency/errors during the same window (if available) to confirm user impact.",
        ]
    elif t is not None and t > 25 and not near:
        if usage_vs_limit is not None and usage_vs_limit < 0.2:
            ratio_txt = f"{usage_vs_limit:.2f} ({usage_vs_limit*100:.0f}%)" if usage_vs_limit is not None else "unknown"
            one_liner = (
                "CPU throttling p95 is high, but CPU usage is far from the configured limit; "
                f"usage/limit p95 is ~{ratio_txt}, so raising limits is unlikely to help."
            )
            if f.metrics.cpu_throttle_top_container:
                next_steps = [
                    f"Inferred top throttled container (from metrics): `{f.metrics.cpu_throttle_top_container}` (p95 throttling ~{(f.metrics.cpu_throttle_top_container_p95_pct or 0):.2f}%).",
                    "Prometheus: per-container throttling (top 3) (see debug promql in JSON / Appendix).",
                    "If no logs/app metrics are available for this target, consider enabling Loki for this namespace or exposing RED metrics to assess impact.",
                ]
            else:
                next_steps = [
                    "Prometheus: per-container throttling (top 3) (see debug promql in JSON / Appendix).",
                    "If no logs/app metrics are available for this target, consider enabling Loki for this namespace or exposing RED metrics to assess impact.",
                ]
            # Store long query in structured debug instead of next steps.
            try:
                from agent.core.models import DebugInfo

                if investigation.analysis.debug is None:
                    investigation.analysis.debug = DebugInfo()
                ns = investigation.target.namespace or ""
                pod = investigation.target.pod or ""
                investigation.analysis.debug.promql["cpu_throttling_top_containers"] = (
                    "topk(3, max by(container) (100 * sum by(container) (increase("
                    f'container_cpu_cfs_throttled_periods_total{{namespace="{ns}",pod="{pod}",image!="",container!="",container!="POD"}}[5m])) '
                    "/ clamp_min(sum by(container) (increase("
                    f'container_cpu_cfs_periods_total{{namespace="{ns}",pod="{pod}",image!="",container!="",container!="POD"}}[5m])), 1)))'
                )
            except Exception:
                pass
        else:
            one_liner = "CPU throttling p95 is high, but CPU usage is not near the limit; validate whether this is impacting the service before taking action."
            next_steps = [
                "Correlate with latency/errors or timeout logs during the same window.",
                "If impact exists, consider scaling replicas or increasing CPU limit; otherwise treat as informational.",
            ]
    else:
        one_liner = "CPU throttling is not elevated in the selected window; the alert may have recovered."
        next_steps = [
            "Re-run using the alert start time window or widen the time range.",
            "If the alert keeps flapping, review the alert rule threshold/window and label filters.",
        ]

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="cpu_throttling",
        one_liner=one_liner,
        next_steps=next_steps,
    )

    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_http_5xx(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    p95 = f.metrics.http_5xx_rate_p95
    mx = f.metrics.http_5xx_rate_max

    # Impact: symptom severity only (5xx rate)
    if p95 is not None and p95 >= 1.0:
        impact += _add(
            breakdown,
            reasons,
            code="HTTP5XX_P95_HIGH",
            delta=80,
            feature_ref="metrics.http_5xx_rate_p95",
            why=f"p95={p95:.3f}/s",
        )
    elif p95 is not None and p95 >= 0.1:
        impact += _add(
            breakdown,
            reasons,
            code="HTTP5XX_P95_ELEVATED",
            delta=60,
            feature_ref="metrics.http_5xx_rate_p95",
            why=f"p95={p95:.3f}/s",
        )
    elif mx is not None and mx >= 0.1:
        impact += _add(
            breakdown,
            reasons,
            code="HTTP5XX_SPIKES",
            delta=30,
            feature_ref="metrics.http_5xx_rate_max",
            why=f"max={mx:.3f}/s",
        )

    # Confidence: metric presence + contradictions
    if p95 is not None or mx is not None:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_HTTP5XX_METRIC",
            delta=50,
            feature_ref="metrics.http_5xx_rate_p95",
            why="http_5xx series present",
        )
    else:
        confidence += _add(
            breakdown,
            reasons,
            code="NO_HTTP5XX_METRIC",
            delta=-40,
            feature_ref="metrics.http_5xx_rate_p95",
            why="no http_5xx series",
        )

    if p95 is not None and p95 <= 0.001 and mx is not None and mx <= 0.001:
        confidence += _add(
            breakdown,
            reasons,
            code="HTTP5XX_CONTRADICTION_NEAR_ZERO",
            delta=-40,
            feature_ref="metrics.http_5xx_rate_p95",
            why="series near zero",
        )
        noise += _add(
            breakdown,
            reasons,
            code="NOISE_HTTP5XX_CONTRADICTION",
            delta=20,
            feature_ref="metrics.http_5xx_rate_p95",
            why="contradiction increases noise",
        )

    # Confidence penalty only for missing namespace/pod labels (attribution risk)
    if "labels.namespace" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_NAMESPACE",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="namespace label missing",
        )
    if "labels.pod" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_POD",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="pod label missing",
        )

    # Noise: include flap/cardinality from existing shared logic
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

    # Strong symptom reduces noise
    if p95 is not None and p95 >= 0.1:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_HTTP5XX",
            delta=-20,
            feature_ref="metrics.http_5xx_rate_p95",
            why="strong symptom reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    classification = "informational"
    if confidence < 40:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="http_5xx",
        one_liner="HTTP 5xx errors are elevated in this window; investigate upstream dependencies and recent changes.",
        next_steps=[
            "Confirm 5xx metric scope (service/namespace) and whether it is sustained.",
            "Check recent deploys/rollouts and upstream timeouts in logs/traces if available.",
            "Correlate with latency spikes and error logs for the same window.",
        ],
    )
    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_oom_killed(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    # Baseline: the alert firing is weak evidence of an OOM condition (it is derived from metrics),
    # but it is not as strong as direct K8s corroboration for a specific container/pod.
    impact += _add(
        breakdown, reasons, code="OOM_ALERT_FIRING", delta=50, feature_ref="alert.alertname", why="oom alert fired"
    )

    if f.k8s.oom_killed:
        impact += _add(
            breakdown,
            reasons,
            code="OOMKILLED",
            delta=40,
            feature_ref="k8s.oom_killed",
            why="OOMKilled evidence present",
        )
    if (f.k8s.oom_killed_events or 0) >= 2:
        impact += _add(
            breakdown,
            reasons,
            code="OOMKILLED_REPEAT",
            delta=20,
            feature_ref="k8s.oom_killed_events",
            why=f"count={f.k8s.oom_killed_events}",
        )

    if f.k8s.oom_killed:
        confidence += _add(
            breakdown, reasons, code="EVID_OOM_K8S", delta=70, feature_ref="k8s.oom_killed", why="k8s evidence"
        )
    else:
        # We treat this as "missing corroboration", not "no evidence".
        confidence += _add(
            breakdown,
            reasons,
            code="OOM_CORROBORATION_MISSING",
            delta=-15,
            feature_ref="k8s.oom_killed",
            why="could not corroborate OOM via K8s context",
        )

    if "labels.namespace" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_NAMESPACE",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="namespace label missing",
        )
    if "labels.pod" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_POD",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="pod label missing",
        )

    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

    if f.k8s.oom_killed:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_OOM",
            delta=-30,
            feature_ref="k8s.oom_killed",
            why="strong symptom reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    classification = "informational"
    if confidence < 40:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="oom_killed",
        one_liner="Container appears to have been OOMKilled; investigate memory usage/limits and recent changes.",
        next_steps=[
            "Check container memory usage vs limits/requests and consider raising limits.",
            "Look for allocation spikes or leaks around the window (logs/traces if available).",
            "Correlate with deploy/rollout changes.",
        ],
    )
    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_memory_pressure(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    if f.metrics.memory_near_limit is True:
        impact += _add(
            breakdown,
            reasons,
            code="MEM_NEAR_LIMIT",
            delta=70,
            feature_ref="metrics.memory_near_limit",
            why="p95 usage near limit",
        )
    if f.k8s.evicted:
        impact += _add(
            breakdown, reasons, code="POD_EVICTED", delta=60, feature_ref="k8s.evicted", why="eviction evidence"
        )

    if f.metrics.memory_usage_p95_bytes is not None:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_MEM_USAGE",
            delta=40,
            feature_ref="metrics.memory_usage_p95_bytes",
            why="memory usage series present",
        )
    else:
        confidence += _add(
            breakdown,
            reasons,
            code="NO_MEM_USAGE_SERIES",
            delta=-40,
            feature_ref="metrics.memory_usage_p95_bytes",
            why="no memory usage series",
        )
    if f.metrics.memory_limit_bytes is not None:
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_MEM_LIMIT",
            delta=20,
            feature_ref="metrics.memory_limit_bytes",
            why="memory limit present",
        )

    # Contradiction: usage is tiny but alert implies pressure
    if f.metrics.memory_usage_p95_bytes is not None and f.metrics.memory_limit_bytes is not None:
        if f.metrics.memory_usage_p95_bytes < 0.1 * f.metrics.memory_limit_bytes:
            confidence += _add(
                breakdown,
                reasons,
                code="MEM_PRESSURE_CONTRADICTION_LOW_USAGE",
                delta=-30,
                feature_ref="metrics.memory_usage_p95_bytes",
                why="p95 usage < 10% of limit",
            )
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_MEM_CONTRADICTION",
                delta=10,
                feature_ref="metrics.memory_usage_p95_bytes",
                why="contradiction increases noise",
            )

    if "labels.namespace" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_NAMESPACE",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="namespace label missing",
        )
    if "labels.pod" in f.quality.missing_inputs:
        confidence += _add(
            breakdown,
            reasons,
            code="MISSING_LABEL_POD",
            delta=-30,
            feature_ref="quality.missing_inputs",
            why="pod label missing",
        )

    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

    if f.metrics.memory_near_limit is True or f.k8s.evicted:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_MEMORY",
            delta=-20,
            feature_ref="metrics.memory_near_limit",
            why="strong symptom reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    classification = "informational"
    if confidence < 40:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="memory_pressure",
        one_liner="Memory pressure signals detected; investigate memory usage vs limits and potential evictions.",
        next_steps=[
            "Compare memory usage p95 against limits/requests and adjust if needed.",
            "Look for eviction/oom events and correlate with workload changes.",
        ],
    )
    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_meta(investigation: Investigation, f: DerivedFeatures) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    alertname = (
        (investigation.alert.labels or {}).get("alertname") if isinstance(investigation.alert.labels, dict) else None
    )
    if alertname == "InfoInhibitor":
        noise += _add(
            breakdown,
            reasons,
            code="META_ALERT",
            delta=90,
            feature_ref="alert.alertname",
            why="InfoInhibitor is a meta/inhibitor alert",
        )
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_META_ALERTNAME",
            delta=70,
            feature_ref="alert.alertname",
            why="alertname matches InfoInhibitor",
        )
        impact += _add(
            breakdown,
            reasons,
            code="IMPACT_LOW_META",
            delta=0,
            feature_ref="alert.alertname",
            why="meta alert has no direct symptom impact",
        )
    else:
        noise += _add(
            breakdown, reasons, code="META_FAMILY", delta=60, feature_ref="features.family", why=f"family={f.family}"
        )
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_META_FAMILY",
            delta=40,
            feature_ref="features.family",
            why="classified as meta",
        )

    # Include flap/cardinality noise (base noise contributions)
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    verdict = DeterministicVerdict(
        classification="noisy" if noise >= 70 else "informational",
        primary_driver="meta",
        one_liner="This is a meta/inhibitor alert intended to suppress other alerts; treat it as operational noise, not a direct incident symptom.",
        next_steps=[
            "Confirm Alertmanager inhibition rules and routing/grouping are configured as expected.",
            "If this alert pages humans, adjust routing to reduce noise (e.g., route to null receiver).",
        ],
    )
    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_target_down(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname = labels.get("alertname")

    # Impact: based on how many targets appear affected (best-effort via ALERTS counts)
    firing = None
    if investigation.analysis.noise and isinstance(investigation.analysis.noise.prometheus, dict):
        firing = investigation.analysis.noise.prometheus.get("firing_instances")
    if isinstance(firing, (int, float)) and firing >= 1:
        impact += _add(
            breakdown,
            reasons,
            code="TARGETS_DOWN",
            delta=70,
            feature_ref="noise.prometheus.firing_instances",
            why=f"firing={firing}",
        )
        if firing >= 5:
            impact += _add(
                breakdown,
                reasons,
                code="TARGETS_DOWN_MANY",
                delta=20,
                feature_ref="noise.prometheus.firing_instances",
                why=f"firing={firing}",
            )
        if firing >= 20:
            impact += _add(
                breakdown,
                reasons,
                code="TARGETS_DOWN_MASS",
                delta=10,
                feature_ref="noise.prometheus.firing_instances",
                why=f"firing={firing}",
            )
    else:
        # Still an impact signal even without counts (we saw a TargetDown alert instance).
        impact += _add(
            breakdown, reasons, code="TARGET_DOWN_ALERT", delta=50, feature_ref="alert.alertname", why=str(alertname)
        )

    # Confidence: is this really TargetDown and do we have identity labels?
    if str(alertname or "").lower() == "targetdown":
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_TARGETDOWN_NAME",
            delta=60,
            feature_ref="alert.alertname",
            why="alertname=TargetDown",
        )
    if labels.get("instance"):
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_INSTANCE_LABEL",
            delta=20,
            feature_ref="labels.instance",
            why=str(labels.get("instance")),
        )
    if labels.get("job"):
        confidence += _add(
            breakdown, reasons, code="EVID_JOB_LABEL", delta=10, feature_ref="labels.job", why=str(labels.get("job"))
        )

    # Contradiction detector: TargetDown present but no firing instances reported (best-effort)
    if isinstance(firing, (int, float)) and firing == 0:
        confidence += _add(
            breakdown,
            reasons,
            code="TARGETDOWN_CONTRADICTION_NO_FIRING",
            delta=-40,
            feature_ref="noise.prometheus.firing_instances",
            why="firing_instances=0",
        )
        noise += _add(
            breakdown,
            reasons,
            code="NOISE_TARGETDOWN_CONTRADICTION",
            delta=20,
            feature_ref="noise.prometheus.firing_instances",
            why="contradiction increases noise",
        )

    # Contradiction detector (weak corroboration): label-derived up() suggests 0 down, but TargetDown is firing.
    # This usually indicates either:
    # - stale/false-positive alert
    # - label mismatch between alert and scrape labels
    # We treat this as a confidence penalty and noise increase (not a hard proof).
    prom_baseline = getattr(investigation.evidence.metrics, "prom_baseline", None)
    if isinstance(prom_baseline, dict) and isinstance(firing, (int, float)) and firing >= 1:
        down = _prom_scalar(prom_baseline.get("up_job_down"))
        total = _prom_scalar(prom_baseline.get("up_job_total"))
        # Only use this signal when the baseline query clearly matched a non-zero population.
        if down is not None and total is not None and total >= 1 and down <= 0:
            confidence += _add(
                breakdown,
                reasons,
                code="TARGETDOWN_CONTRADICTION_UP_NONE",
                delta=-30,
                feature_ref="evidence.metrics.prom_baseline.up_job_down,up_job_total",
                why=f"up_job_down={down}, up_job_total={total}",
            )
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_TARGETDOWN_CONTRADICTION_UP_NONE",
                delta=15,
                feature_ref="evidence.metrics.prom_baseline.up_job_down,up_job_total",
                why="TargetDown firing but label-derived up() shows 0 down",
            )

    # Noise: flap/cardinality (but instance/endpoint are expected dimensions for TargetDown)
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )

        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        eph_eff = [e for e in eph if e not in ("instance", "endpoint")]
        if eph_eff:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph_eff)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph_eff[:6]),
            )

    # Strong symptom reduces noise: if many targets down, it is likely actionable
    if isinstance(firing, (int, float)) and firing >= 1:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_TARGETDOWN",
            delta=-20,
            feature_ref="noise.prometheus.firing_instances",
            why="targets down reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    classification = "informational"
    if confidence < 40:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="target_down",
        one_liner=(
            "TargetDown alert is firing, but label-derived up() checks suggest 0 targets down; verify in Prometheus /targets "
            "(possible label mismatch or stale signal)."
            if "TARGETDOWN_CONTRADICTION_UP_NONE" in reasons
            else "One or more scrape targets appear down; validate reachability and exporter/endpoint health."
        ),
        next_steps=[
            "Check if the target is reachable (DNS/network/TLS) and if the exporter process is running.",
            "Inspect Prometheus scrape errors for the affected job/instance and any recent config changes.",
            "If many targets are down, treat as a potential infrastructure/network incident.",
        ],
    )
    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_k8s_rollout_health(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname = str(labels.get("alertname") or "")
    lname = alertname.lower()

    # Impact: per alert kind
    if "daemonsetrolloutstuck" in lname or "rolloutstuck" in lname:
        impact += _add(breakdown, reasons, code="ROLLOUT_STUCK", delta=80, feature_ref="alert.alertname", why=alertname)
    if "replicasmismatch" in lname or "deploymentreplicas" in lname:
        impact += _add(
            breakdown, reasons, code="REPLICAS_MISMATCH", delta=60, feature_ref="alert.alertname", why=alertname
        )
    if "jobfailed" in lname:
        impact += _add(breakdown, reasons, code="JOB_FAILED", delta=70, feature_ref="alert.alertname", why=alertname)

    # Confidence: name match + workload labels if present
    if impact > 0:
        confidence += _add(
            breakdown, reasons, code="EVID_ROLLOUT_ALERTNAME", delta=60, feature_ref="alert.alertname", why=alertname
        )
    for k in ("deployment", "daemonset", "statefulset", "job"):
        if labels.get(k):
            confidence += _add(
                breakdown,
                reasons,
                code="EVID_WORKLOAD_LABEL",
                delta=10,
                feature_ref=f"labels.{k}",
                why=f"{k}={labels.get(k)}",
            )
            break

    # Contradiction: no firing instances reported (best-effort)
    firing = None
    if investigation.analysis.noise and isinstance(investigation.analysis.noise.prometheus, dict):
        firing = investigation.analysis.noise.prometheus.get("firing_instances")
    if isinstance(firing, (int, float)) and firing == 0:
        confidence += _add(
            breakdown,
            reasons,
            code="ROLLOUT_CONTRADICTION_NO_FIRING",
            delta=-40,
            feature_ref="noise.prometheus.firing_instances",
            why="firing_instances=0",
        )
        noise += _add(
            breakdown,
            reasons,
            code="NOISE_ROLLOUT_CONTRADICTION",
            delta=15,
            feature_ref="noise.prometheus.firing_instances",
            why="contradiction increases noise",
        )

    # Contradiction: rollout status indicates healthy, but "rollout stuck / replicas mismatch" alert is firing.
    # This suggests stale signal, label mismatch, or transient that already recovered.
    rs = investigation.evidence.k8s.rollout_status or {}
    if isinstance(rs, dict) and rs.get("kind") and rs.get("name") and impact >= 60:
        kind = str(rs.get("kind") or "")
        healthy = False
        why_bits: List[str] = []
        try:
            if kind == "Deployment":
                replicas = rs.get("replicas")
                ready = rs.get("ready_replicas")
                updated = rs.get("updated_replicas")
                unavail = rs.get("unavailable_replicas")
                if isinstance(unavail, int) and unavail == 0:
                    if isinstance(replicas, int) and isinstance(ready, int) and ready >= replicas:
                        healthy = True
                    if isinstance(replicas, int) and isinstance(updated, int) and updated >= replicas:
                        healthy = True if (replicas is None or updated >= replicas) and healthy else healthy
                why_bits = [f"ready={ready}/{replicas}", f"updated={updated}", f"unavailable={unavail}"]
            elif kind == "DaemonSet":
                desired = rs.get("desired_number_scheduled")
                ready = rs.get("number_ready")
                updated = rs.get("updated_number_scheduled")
                if isinstance(desired, int) and isinstance(ready, int) and isinstance(updated, int):
                    if desired > 0 and ready == desired and updated == desired:
                        healthy = True
                why_bits = [f"ready={ready}/{desired}", f"updated={updated}"]
            elif kind == "StatefulSet":
                replicas = rs.get("replicas")
                ready = rs.get("ready_replicas")
                updated = rs.get("updated_replicas")
                if isinstance(replicas, int) and isinstance(ready, int) and isinstance(updated, int):
                    if replicas > 0 and ready >= replicas and updated >= replicas:
                        healthy = True
                why_bits = [f"ready={ready}/{replicas}", f"updated={updated}"]
            elif kind == "Job":
                failed = rs.get("failed")
                active = rs.get("active")
                succeeded = rs.get("succeeded")
                if isinstance(failed, int) and failed == 0:
                    # Treat "not failing" as healthy for the purpose of contradiction; JobFailed alerts should have failed>0.
                    healthy = True
                why_bits = [f"active={active}", f"succeeded={succeeded}", f"failed={failed}"]
        except Exception:
            healthy = False

        if healthy:
            confidence += _add(
                breakdown,
                reasons,
                code="ROLLOUT_CONTRADICTION_HEALTHY_STATUS",
                delta=-50,
                feature_ref="evidence.k8s.rollout_status",
                why=f"{kind} appears healthy ({', '.join([b for b in why_bits if b])})",
            )
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_ROLLOUT_CONTRADICTION_HEALTHY_STATUS",
                delta=15,
                feature_ref="evidence.k8s.rollout_status",
                why="rollout status contradicts alert",
            )

    # Noise: flap/cardinality baseline
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

    if impact >= 60:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_ROLLOUT",
            delta=-20,
            feature_ref="alert.alertname",
            why="strong rollout symptom reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    classification = "informational"
    if confidence < 40:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="k8s_rollout_health",
        one_liner=(
            "Rollout health alert is firing, but current rollout status appears healthy; verify alert rule scope/labels and check if the issue already recovered."
            if "ROLLOUT_CONTRADICTION_HEALTHY_STATUS" in reasons
            else "Kubernetes rollout/workload health alert fired; validate rollout status and controller conditions."
        ),
        next_steps=[
            "Check the controller status (Deployment/DaemonSet/Job) and events for progress deadline, unavailable replicas, or stuck pods.",
            "Identify the workload label (deployment/daemonset/job) from the alert and correlate with recent changes.",
        ],
    )
    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_observability_pipeline(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    breakdown: List[ScoreBreakdownItem] = []
    reasons: List[str] = []
    impact = 0
    confidence = 0
    noise = 0

    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    alertname = str(labels.get("alertname") or "")
    lname = alertname.lower()

    # Impact: per alert class
    if "alertingruleserror" in lname:
        impact += _add(
            breakdown, reasons, code="ALERTING_RULES_ERROR", delta=80, feature_ref="alert.alertname", why=alertname
        )
    if "recordingrulesnodata" in lname:
        impact += _add(
            breakdown, reasons, code="RECORDING_RULES_NO_DATA", delta=60, feature_ref="alert.alertname", why=alertname
        )
    if "rowsrejectedoningestion" in lname:
        impact += _add(
            breakdown, reasons, code="INGESTION_REJECTS", delta=70, feature_ref="alert.alertname", why=alertname
        )
    if "toomanylogs" in lname:
        impact += _add(breakdown, reasons, code="TOO_MANY_LOGS", delta=50, feature_ref="alert.alertname", why=alertname)

    if impact > 0:
        confidence += _add(
            breakdown, reasons, code="EVID_OBS_ALERTNAME", delta=60, feature_ref="alert.alertname", why=alertname
        )
    if labels.get("namespace"):
        confidence += _add(
            breakdown,
            reasons,
            code="EVID_NAMESPACE",
            delta=10,
            feature_ref="labels.namespace",
            why=str(labels.get("namespace")),
        )

    # Contradiction: no firing instances reported (best-effort)
    firing = None
    if investigation.analysis.noise and isinstance(investigation.analysis.noise.prometheus, dict):
        firing = investigation.analysis.noise.prometheus.get("firing_instances")
    if isinstance(firing, (int, float)) and firing == 0:
        confidence += _add(
            breakdown,
            reasons,
            code="OBS_CONTRADICTION_NO_FIRING",
            delta=-40,
            feature_ref="noise.prometheus.firing_instances",
            why="firing_instances=0",
        )
        noise += _add(
            breakdown,
            reasons,
            code="NOISE_OBS_CONTRADICTION",
            delta=15,
            feature_ref="noise.prometheus.firing_instances",
            why="contradiction increases noise",
        )

    # Noise: flap/cardinality baseline
    ni = investigation.analysis.noise
    if ni is not None:
        flap_score = ni.flap.flap_score_0_100 if ni.flap is not None else 0
        if flap_score >= 80:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_HIGH",
                delta=40,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        elif flap_score >= 40:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_FLAP_MED",
                delta=20,
                feature_ref="noise.flap.flap_score_0_100",
                why=f"flap_score={flap_score}",
            )
        eph = ni.cardinality.ephemeral_labels_present if ni.cardinality is not None else []
        if investigation.target.workload_kind and investigation.target.workload_name:
            eph = [e for e in eph if e not in ("pod", "pod_name")]
        if eph:
            noise += _add(
                breakdown,
                reasons,
                code="NOISE_CARDINALITY",
                delta=min(30, 10 * len(eph)),
                feature_ref="noise.cardinality.ephemeral_labels_present",
                why=",".join(eph[:6]),
            )

    if impact >= 60:
        noise += _add(
            breakdown,
            reasons,
            code="STRONG_SYMPTOM_OBS",
            delta=-10,
            feature_ref="alert.alertname",
            why="strong observability symptom reduces noise",
        )

    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    classification = "informational"
    if confidence < 40:
        classification = "artifact"
    elif noise >= 70:
        classification = "noisy"
    elif impact >= 60 and confidence >= 60 and noise <= 60:
        classification = "actionable"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="observability_pipeline",
        one_liner="Monitoring/logging pipeline health alert fired; investigate rule evaluation and ingestion/backpressure.",
        next_steps=[
            "Check the relevant component (vmalert/vminsert/logs backend) for errors and saturation.",
            "Inspect recent config/rule changes and upstream write error rates/rejections.",
        ],
    )
    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_job_failed(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    """
    Score Kubernetes Job failure alerts (KubeJobFailed).

    Impact drivers:
    - Job failed (baseline): +40
    - FATAL in logs: +30
    - Exceptions in logs: +20
    - ERROR patterns: +15
    - Multiple restarts: +15
    - Repeated failures: +10

    Confidence drivers:
    - Alert confirms failure: +60
    - Logs with parsed errors: +25
    - Logs available: +15
    - K8s context: +10
    - Job status: +5

    Noise factors:
    - Test/canary job name: +20
    - Historical mode: +10
    - Empty logs: +15
    """
    impact = 0.0
    confidence = 0.0
    noise = 0.0
    reasons: List[str] = []
    breakdown: List[ScoreBreakdownItem] = []

    # Impact: Job failure baseline
    impact += _add(
        breakdown,
        reasons,
        code="JOB_FAILED_BASELINE",
        delta=40,
        feature_ref="alert.alertname",
        why="Job failed",
    )

    # Impact: Log parsing results
    logs_ev = investigation.evidence.logs
    if logs_ev.parsed_errors:
        parsing_meta = logs_ev.parsing_metadata or {}
        fatal_count = parsing_meta.get("fatal_count", 0)
        exception_count = parsing_meta.get("exception_count", 0)
        error_count = parsing_meta.get("error_count", 0)

        if fatal_count > 0:
            impact += _add(
                breakdown,
                reasons,
                code="JOB_FATAL_IN_LOGS",
                delta=30,
                feature_ref="evidence.logs.parsing_metadata.fatal_count",
                why=f"{fatal_count} FATAL patterns",
            )

        if exception_count > 0:
            impact += _add(
                breakdown,
                reasons,
                code="JOB_EXCEPTION_IN_LOGS",
                delta=20,
                feature_ref="evidence.logs.parsing_metadata.exception_count",
                why=f"{exception_count} Exception patterns",
            )

        if error_count > 0:
            impact += _add(
                breakdown,
                reasons,
                code="JOB_ERROR_IN_LOGS",
                delta=15,
                feature_ref="evidence.logs.parsing_metadata.error_count",
                why=f"{error_count} ERROR patterns",
            )

    # Impact: Multiple restarts
    restart_data = investigation.evidence.metrics.restart_data
    if restart_data:
        restart_count = restart_data.get("restart_count", 0)
        if restart_count > 1:
            impact += _add(
                breakdown,
                reasons,
                code="JOB_MULTIPLE_RESTARTS",
                delta=15,
                feature_ref="evidence.metrics.restart_data.restart_count",
                why=f"{restart_count} restarts",
            )

    # Impact: Repeated failures (from rollout status)
    rollout_status = investigation.evidence.k8s.rollout_status
    if rollout_status:
        failed_count = rollout_status.get("failed", 0)
        if failed_count > 1:
            impact += _add(
                breakdown,
                reasons,
                code="JOB_REPEATED_FAILURES",
                delta=10,
                feature_ref="evidence.k8s.rollout_status.failed",
                why=f"{failed_count} failed attempts",
            )

    # Confidence: Alert confirms failure
    confidence += _add(
        breakdown,
        reasons,
        code="JOB_ALERT_CONFIRMS_FAILURE",
        delta=60,
        feature_ref="alert.alertname",
        why="KubeJobFailed alert",
    )

    # Confidence: Logs with parsed errors
    if logs_ev.parsed_errors:
        confidence += _add(
            breakdown,
            reasons,
            code="JOB_LOGS_PARSED",
            delta=25,
            feature_ref="evidence.logs.parsed_errors",
            why=f"{len(logs_ev.parsed_errors)} error patterns",
        )
    elif logs_ev.logs_status == "ok":
        # Logs available but no errors parsed
        confidence += _add(
            breakdown,
            reasons,
            code="JOB_LOGS_AVAILABLE",
            delta=15,
            feature_ref="evidence.logs.logs_status",
            why="logs available",
        )

    # Confidence: K8s context
    if investigation.evidence.k8s.pod_info:
        confidence += _add(
            breakdown,
            reasons,
            code="JOB_K8S_CONTEXT",
            delta=10,
            feature_ref="evidence.k8s.pod_info",
            why="K8s context available",
        )

    # Confidence: Job status
    if rollout_status:
        confidence += _add(
            breakdown,
            reasons,
            code="JOB_ROLLOUT_STATUS",
            delta=5,
            feature_ref="evidence.k8s.rollout_status",
            why="Job status available",
        )

    # Noise: Test/canary job name
    job_name = investigation.target.workload_name or ""
    if re.search(r"\b(test|canary|sample|demo|example)\b", job_name, re.IGNORECASE):
        noise += _add(
            breakdown,
            reasons,
            code="JOB_TEST_NAME",
            delta=20,
            feature_ref="target.workload_name",
            why="test/canary job",
        )

    # Noise: Historical mode
    if investigation.meta.get("historical_mode"):
        noise += _add(
            breakdown,
            reasons,
            code="JOB_HISTORICAL_MODE",
            delta=10,
            feature_ref="meta.historical_mode",
            why="TTL-deleted pod",
        )

    # Noise: Empty logs
    if logs_ev.logs_status == "empty":
        noise += _add(
            breakdown,
            reasons,
            code="JOB_EMPTY_LOGS",
            delta=15,
            feature_ref="evidence.logs.logs_status",
            why="no logs available",
        )

    # Clamp scores
    impact = _clamp_0_100(int(impact))
    confidence = _clamp_0_100(int(confidence))
    noise = _clamp_0_100(int(noise))

    # Verdict classification
    classification = "informational"
    if impact + confidence >= 140 and confidence >= 75:
        classification = "actionable"
    elif impact + confidence >= 100 and confidence >= 60:
        classification = "actionable"

    # Build next steps
    next_steps = []

    # Add hypothesis next_tests (remediation first, then diagnostics)
    # The hypothesis.next_tests now contains remediation steps first, then diagnostic steps
    for hyp in investigation.analysis.hypotheses:
        if hyp.confidence_0_100 >= 70 and hyp.next_tests:
            # Add all steps from high-confidence hypothesis
            # (pattern-based hypotheses put remediation first, diagnostics second)
            next_steps.extend(hyp.next_tests)
            break  # Only add from top hypothesis to avoid redundancy

    # Add generic next steps
    if logs_ev.parsed_errors and not next_steps:
        # Only add generic "review logs" if no specific hypothesis tests were added
        next_steps.append("Review parsed error patterns in logs to identify root cause")
    if investigation.target.workload_name:
        next_steps.append(
            f"kubectl describe job {investigation.target.workload_name} -n {investigation.target.namespace}"
        )
        next_steps.append(
            f"kubectl get job {investigation.target.workload_name} -n {investigation.target.namespace} -o yaml"
        )
    if investigation.target.pod:
        next_steps.append(f"kubectl logs {investigation.target.pod} -n {investigation.target.namespace}")
    if rollout_status and rollout_status.get("failed", 0) > 1:
        next_steps.append("Check for systemic issues causing repeated Job failures")

    # Build one-liner: incorporate hypothesis if high-confidence root cause identified
    job_name = investigation.target.workload_name or "unknown"
    one_liner = f"Kubernetes Job {job_name} failed"

    # If we have a high-confidence hypothesis, include it in the one-liner
    if investigation.analysis.hypotheses:
        top_hyp = investigation.analysis.hypotheses[0]
        if top_hyp.confidence_0_100 >= 70:
            # Extract key insight from hypothesis title
            one_liner = f"Job {job_name} failed due to {top_hyp.title.lower()}"

    verdict = DeterministicVerdict(
        classification=classification,
        primary_driver="job_failed",
        one_liner=one_liner,
        next_steps=next_steps or ["Investigate Job failure cause"],
    )

    return (
        DeterministicScores(
            impact_score=impact,
            confidence_score=confidence,
            noise_score=noise,
            reason_codes=reasons,
            breakdown=breakdown,
        ),
        verdict,
    )


def score_investigation(
    investigation: Investigation, f: DerivedFeatures
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    family = f.family
    if family == "crashloop":
        scores, verdict = score_crashloop(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "pod_not_healthy":
        scores, verdict = score_pod_not_healthy(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "cpu_throttling":
        scores, verdict = score_cpu_throttling(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "http_5xx":
        scores, verdict = score_http_5xx(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "oom_killed":
        scores, verdict = score_oom_killed(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "memory_pressure":
        scores, verdict = score_memory_pressure(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "meta":
        scores, verdict = score_meta(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "target_down":
        scores, verdict = score_target_down(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "k8s_rollout_health":
        scores, verdict = score_k8s_rollout_health(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "observability_pipeline":
        scores, verdict = score_observability_pipeline(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    if family == "job_failed":
        scores, verdict = score_job_failed(investigation, f)
        return _postprocess_verdict(investigation, f, scores, verdict)
    # Generic fallback
    scores = DeterministicScores(
        impact_score=0, confidence_score=0, noise_score=0, reason_codes=["UNSUPPORTED_FAMILY"], breakdown=[]
    )
    verdict = DeterministicVerdict(
        classification="informational",
        primary_driver="generic",
        one_liner="No deterministic scoring profile exists for this alert family yet.",
        next_steps=["Add a scoring profile for this alert family."],
    )
    return _postprocess_verdict(investigation, f, scores, verdict)


def _postprocess_verdict(
    investigation: Investigation,
    f: DerivedFeatures,
    scores: DeterministicScores,
    verdict: DeterministicVerdict,
) -> Tuple[DeterministicScores, DeterministicVerdict]:
    """
    Cross-cutting deterministic tweaks that should reflect in both JSON and markdown.
    """
    # ---- Artifact split (recovered vs low-confidence) must be explicit for on-call.
    if verdict.classification == "artifact":

        def _add_reason(code: str) -> None:
            if code not in scores.reason_codes:
                scores.reason_codes.append(code)

        # Heuristic: only mark as "recovered" when we have a clear contradiction pattern that implies
        # the symptom is not currently present (vs "low confidence" which includes missing evidence).
        recovered_hints = {
            "CRASHLOOP_CONTRADICTION_READY_NO_RESTARTS",
            "TARGETDOWN_CONTRADICTION_NO_FIRING",
            "ROLLOUT_CONTRADICTION_NO_FIRING",
            "ROLLOUT_CONTRADICTION_HEALTHY_STATUS",
        }
        is_recovered = any(c in recovered_hints for c in (scores.reason_codes or []))

        # Special-case: prevent over-claiming when the scorer could not corroborate OOM.
        if "OOM_CORROBORATION_MISSING" in (scores.reason_codes or []):
            base = (
                "OOM alert fired (derived from metrics), but the agent could not retrieve corroborating K8s evidence "
                "for the container/pod in this window (missing K8s context or stale window)."
            )
            verdict.one_liner = base
            _add_reason("ARTIFACT_LOW_CONFIDENCE")
            return scores, verdict

        if is_recovered:
            _add_reason("ARTIFACT_RECOVERED")
            if not verdict.one_liner.lower().startswith("recovered"):
                verdict.one_liner = f"Recovered/stale signal: {verdict.one_liner}"
        else:
            _add_reason("ARTIFACT_LOW_CONFIDENCE")
            if not verdict.one_liner.lower().startswith("low-confidence"):
                verdict.one_liner = f"Low-confidence attribution: {verdict.one_liner}"

    # If an alert is long-running and still informational, explicitly suggest alert-quality improvements.
    if f.quality.is_long_running and verdict.classification == "informational":
        tip = (
            "Alert is long-running and informational; consider adjusting threshold/window or adding an impact condition "
            "(e.g., require CPU near limit or correlate with errors/latency) to reduce chronic noise."
        )
        if tip not in verdict.next_steps:
            verdict.next_steps.append(tip)

    # Derived severity (critical|warning|info) used for Inbox urgency.
    # Score-aware mapping:
    # - critical only when classification=actionable AND impact>=85 AND confidence>=70 AND noise<=40
    # - warning when classification=actionable but not critical
    # - info otherwise
    # Guardrail: if confidence<60 OR noise>60, never emit critical.
    try:
        impact = int(scores.impact_score)
        confidence = int(scores.confidence_score)
        noise = int(scores.noise_score)
    except Exception:
        impact, confidence, noise = 0, 0, 100

    sev: str = "info"
    if verdict.classification == "actionable":
        sev = "warning"
        if confidence >= 60 and noise <= 60 and impact >= 85 and confidence >= 70 and noise <= 40:
            sev = "critical"
        if confidence < 60 or noise > 60:
            sev = "warning"
    verdict.severity = sev  # type: ignore[assignment]
    return scores, verdict
