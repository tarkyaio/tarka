"""JSON dump helpers (CLI-friendly, testable).

We keep CLI printing logic out of core modules; this returns plain dicts.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Tuple

from agent.core.models import Investigation

DumpMode = Literal["analysis", "investigation"]


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _alert_label_views(investigation: Investigation) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Split raw alert labels into:
    - core_labels: the affected target identity + minimal alert identity
    - source_labels: scrape/metric source metadata (job/service/instance/endpoint/etc.)

    Raw labels are always preserved under `alert.labels`.
    """
    labels = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}

    core: Dict[str, Any] = {
        "alertname": labels.get("alertname"),
        "severity": labels.get("severity"),
        "cluster": investigation.target.cluster or labels.get("cluster"),
        "target_type": investigation.target.target_type,
    }

    # Prefer the parsed target identity (investigation.target) over alert labels.
    if investigation.target.target_type == "pod":
        core.update(
            {
                "namespace": investigation.target.namespace or labels.get("namespace") or labels.get("Namespace"),
                "pod": investigation.target.pod or labels.get("pod") or labels.get("pod_name") or labels.get("podName"),
                "container": investigation.target.container,
                "workload_kind": investigation.target.workload_kind,
                "workload_name": investigation.target.workload_name,
            }
        )
    elif investigation.target.target_type == "service":
        core.update(
            {
                "namespace": investigation.target.namespace or labels.get("namespace") or labels.get("Namespace"),
                "service": investigation.target.service or labels.get("service"),
            }
        )
    elif investigation.target.target_type == "node":
        core.update({"instance": investigation.target.instance or labels.get("instance")})
    elif investigation.target.target_type == "cluster":
        core.update({"cluster": investigation.target.cluster or labels.get("cluster")})

    # Source labels are only attached for pod targets, where scrape metadata is commonly confused with
    # the affected workload identity (e.g., kube-state-metrics).
    source: Dict[str, Any] = {}
    if investigation.target.target_type == "pod":
        for k in ("job", "service", "instance", "endpoint", "prometheus"):
            if labels.get(k) is not None:
                source[k] = labels.get(k)

        # Make it explicit when the alert's `container` label is scrape metadata (common with kube-state-metrics),
        # rather than the affected container.
        raw_container = labels.get("container") or labels.get("Container") or labels.get("container_name")
        if raw_container and (not core.get("container") or raw_container != core.get("container")):
            source["scrape_container"] = raw_container

    return _clean(core), _clean(source)


def investigation_to_json_dict(investigation: Investigation, *, mode: DumpMode = "analysis") -> Dict[str, Any]:
    if mode == "investigation":
        # Pydantic v2: mode="json" produces JSON-serializable types.
        return investigation.model_dump(mode="json")

    core_labels, source_labels = _alert_label_views(investigation)

    # Keep `alert.labels` compact in analysis mode to avoid confusing duplication.
    # Full raw labels are still available in `--dump-json investigation`.
    labels_raw = investigation.alert.labels if isinstance(investigation.alert.labels, dict) else {}
    labels_compact: Dict[str, Any] = dict(labels_raw)
    if investigation.target.target_type == "pod" and source_labels:
        for k in ("job", "service", "instance", "endpoint", "prometheus"):
            labels_compact.pop(k, None)
        # If we promoted the alert's container label to `scrape_container`, drop it from `labels`.
        if source_labels.get("scrape_container") is not None:
            labels_compact.pop("container", None)
            labels_compact.pop("Container", None)
            labels_compact.pop("container_name", None)

    # analysis mode (small, stable, explainable)
    return {
        "alert": {
            "fingerprint": investigation.alert.fingerprint,
            "labels": labels_compact,
            "core_labels": core_labels,
            "source_labels": source_labels,
            "annotations": investigation.alert.annotations,
            "starts_at": investigation.alert.starts_at,
            "ends_at": investigation.alert.ends_at,
            "state": investigation.alert.state,
            "normalized_state": investigation.alert.normalized_state,
            "ends_at_kind": investigation.alert.ends_at_kind,
        },
        "target": investigation.target.model_dump(mode="json"),
        "time_window": investigation.time_window.model_dump(mode="json"),
        "evidence": {
            # Include logs metadata and parsed_errors for RCA context (critical for reducing tool loops)
            "logs": (
                {
                    "status": investigation.evidence.logs.logs_status,
                    "reason": investigation.evidence.logs.logs_reason,
                    "count": len(investigation.evidence.logs.logs) if investigation.evidence.logs.logs else 0,
                    "parsed_errors": investigation.evidence.logs.parsed_errors or [],
                }
                if investigation.evidence and investigation.evidence.logs
                else None
            ),
            # Include lightweight GitHub metadata so chat tools can discover the repo
            "github": (
                {
                    "repo": investigation.evidence.github.repo,
                    "repo_discovery_method": investigation.evidence.github.repo_discovery_method,
                    "is_third_party": investigation.evidence.github.is_third_party,
                }
                if investigation.evidence and investigation.evidence.github and investigation.evidence.github.repo
                else None
            ),
        },
        "analysis": {
            "features": (
                investigation.analysis.features.model_dump(mode="json") if investigation.analysis.features else None
            ),
            "scores": investigation.analysis.scores.model_dump(mode="json") if investigation.analysis.scores else None,
            "verdict": (
                investigation.analysis.verdict.model_dump(mode="json") if investigation.analysis.verdict else None
            ),
            "change": investigation.analysis.change.model_dump(mode="json") if investigation.analysis.change else None,
            "noise": investigation.analysis.noise.model_dump(mode="json") if investigation.analysis.noise else None,
            "decision": (
                investigation.analysis.decision.model_dump(mode="json") if investigation.analysis.decision else None
            ),
            "enrichment": (
                investigation.analysis.enrichment.model_dump(mode="json") if investigation.analysis.enrichment else None
            ),
            "hypotheses": [h.model_dump(mode="json") for h in (investigation.analysis.hypotheses or [])],
            # Avoid noisy defaults for capacity (only show recommendations/rightsizing when present).
            "capacity": (
                investigation.analysis.capacity.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                if investigation.analysis.capacity
                else None
            ),
            "rca": (
                investigation.analysis.rca.model_dump(mode="json")
                if getattr(investigation.analysis, "rca", None)
                else None
            ),
            "llm": investigation.analysis.llm.model_dump(mode="json") if investigation.analysis.llm else None,
            "debug": investigation.analysis.debug.model_dump(mode="json") if investigation.analysis.debug else None,
        },
        "errors": list(investigation.errors or []),
    }
