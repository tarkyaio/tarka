"""Alert noise insights (read-only, deterministic).

This focuses on:
- missing labels that prevent correlation (namespace/pod/container/workload/cluster)
- grouping/dedup suggestions from label set shape
- best-effort flapping/instance-count signals from Prometheus ALERTS metrics
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.core.models import (
    Investigation,
    NoiseCardinalityInsights,
    NoiseFlapInsights,
    NoiseInsights,
    NoiseMissingLabelsInsights,
)
from agent.providers.prom_provider import query_prometheus_instant

_EPHEMERAL_LABELS = {
    "pod",
    "pod_name",
    "instance",
    "endpoint",
    "container",
    "container_name",
    "uid",
    "node",
    "ip",
}

_IMPORTANT_CORRELATION_LABELS = [
    "cluster",
    "namespace",
    "service",
    "job",
    "app",
    "deployment",
    "statefulset",
    "daemonset",
]


def _safe_str(x: Any) -> str:
    return str(x) if x is not None else ""


def _prom_scalar(result: List[Dict[str, Any]]) -> Optional[float]:
    try:
        if not result:
            return None
        value = result[0].get("value")
        if not (isinstance(value, list) and len(value) >= 2):
            return None
        return float(value[1])
    except Exception:
        return None


def analyze_alert_labels(labels: Dict[str, Any]) -> Dict[str, Any]:
    missing = []
    for k in ("namespace", "pod"):
        if not labels.get(k) and not labels.get(k.capitalize()):
            missing.append(k)

    # Container is optional; but if present in alert rule it's useful for instance correlation.
    if not labels.get("container") and not labels.get("Container") and not labels.get("container_name"):
        missing.append("container")

    high_card = [k for k in labels.keys() if k in _EPHEMERAL_LABELS]

    # Suggested grouping keys: keep stable dims, drop ephemeral ones.
    suggested_group_by = ["alertname"]
    for k in _IMPORTANT_CORRELATION_LABELS:
        if labels.get(k):
            suggested_group_by.append(k)
    if labels.get("namespace") and "namespace" not in suggested_group_by:
        suggested_group_by.append("namespace")

    return {
        "missing_labels": missing,
        "ephemeral_labels_present": high_card,
        "suggested_group_by": suggested_group_by,
    }


def analyze_noise(investigation: Investigation, *, lookback: str = "24h") -> None:
    """
    Populate investigation.analysis.noise (never raises).
    """
    try:
        labels = investigation.alert.labels or {}
        if not isinstance(labels, dict):
            labels = {}

        shape = analyze_alert_labels(labels)

        alertname = _safe_str(labels.get("alertname"))
        namespace = _safe_str(labels.get("namespace") or labels.get("Namespace"))
        cluster = _safe_str(labels.get("cluster"))

        matcher_parts = [f'alertname="{alertname}"'] if alertname else []
        if namespace:
            matcher_parts.append(f'namespace="{namespace}"')
        if cluster:
            matcher_parts.append(f'cluster="{cluster}"')
        matchers = ",".join(matcher_parts)
        sel = f"{{{matchers}}}" if matchers else "{}"

        prom: Dict[str, Any] = {"status": "skipped"}
        try:
            active_instances = _prom_scalar(
                query_prometheus_instant(f"count(ALERTS{sel})", at=investigation.time_window.end_time)
            )
            firing_q = (
                f'count(ALERTS{{{matchers},alertstate="firing"}})' if matchers else 'count(ALERTS{alertstate="firing"})'
            )
            firing_instances = _prom_scalar(query_prometheus_instant(firing_q, at=investigation.time_window.end_time))
            resets_q = (
                f'max(resets(ALERTS_FOR_STATE{{{matchers},alertstate="firing"}}[{lookback}]))'
                if matchers
                else f'max(resets(ALERTS_FOR_STATE{{alertstate="firing"}}[{lookback}]))'
            )
            flaps = _prom_scalar(query_prometheus_instant(resets_q, at=investigation.time_window.end_time))
            prom = {
                "status": "ok",
                "selector": sel,
                "active_instances": active_instances,
                "firing_instances": firing_instances,
                "flap_resets_estimate": flaps,
                "lookback": lookback,
            }
        except Exception as e:
            prom = {"status": "unavailable", "error": str(e)}

        # A) Flap score (Prometheus best-effort)
        flaps_estimate = None
        if isinstance(prom, dict) and prom.get("status") == "ok":
            flaps_estimate = prom.get("flap_resets_estimate")
        flap_score = 0
        if isinstance(flaps_estimate, (int, float)) and flaps_estimate > 0:
            # Deterministic mapping: 1 flap => 20 points, clamp 0..100.
            flap_score = max(0, min(100, int(round(float(flaps_estimate) * 20))))
        flap = NoiseFlapInsights(
            lookback=lookback,
            flaps_estimate=flaps_estimate,
            flap_score_0_100=flap_score,
            notes=[
                "Best-effort estimate from ALERTS_FOR_STATE resets; resolved periods may not be represented as samples.",
            ],
        )

        # B) Cardinality hints
        cardinality = NoiseCardinalityInsights(
            ephemeral_labels_present=list(shape.get("ephemeral_labels_present") or []),
            recommended_group_by=list(shape.get("suggested_group_by") or []),
            recommended_drop_labels=list(shape.get("ephemeral_labels_present") or []),
        )

        # C) Missing labels
        missing = list(shape.get("missing_labels") or [])
        recs: List[str] = []
        if missing:
            recs.append(
                "Add missing labels (namespace/pod/container) in the alert rule labels/annotations or via relabeling so investigations can correlate evidence."
            )
            if "container" in missing:
                recs.append(
                    "For this rule, include `container` in the rule label set and aggregation (e.g., `sum by(container,pod,namespace)`), "
                    "and ensure Alertmanager routing/grouping includes `container` so investigations can pinpoint the right container."
                )
        missing_labels = NoiseMissingLabelsInsights(
            missing=missing,
            inferred=[],
            recommendation=recs,
        )

        investigation.analysis.noise = NoiseInsights(
            label_shape=shape,
            prometheus=prom,
            flap=flap,
            cardinality=cardinality,
            missing_labels=missing_labels,
            notes=[],
        )
    except Exception as e:
        investigation.errors.append(f"Noise: {e}")
        return


def postprocess_noise(investigation: Investigation) -> None:
    """
    Postprocess noise insights using derived features (inference-aware).

    Example: if alert is missing `container` label but we inferred top throttled container from metrics,
    mark it as inferred so the agent doesn't sound blocked.
    """
    try:
        n = investigation.analysis.noise
        f = investigation.analysis.features
        if n is None or n.missing_labels is None or f is None:
            return

        missing = list(n.missing_labels.missing or [])
        inferred = set(n.missing_labels.inferred or [])
        rec = list(n.missing_labels.recommendation or [])

        if "container" in missing and f.metrics.cpu_throttle_top_container:
            inferred.add("container")
            # Keep the rule-level guidance but lead with the fact we inferred the container.
            lead = f"Container label is missing on the alert; the agent inferred it as `{f.metrics.cpu_throttle_top_container}` from metrics."
            tail = [
                "Include `container` in the rule label set and aggregation (e.g., `sum by(container,pod,namespace)`), and ensure Alertmanager routing/grouping includes `container`."
            ]
            rec = [lead] + tail

        n.missing_labels.inferred = sorted(inferred)
        n.missing_labels.recommendation = rec
    except Exception:
        return
