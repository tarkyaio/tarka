"""OOM killer evidence collector."""

from __future__ import annotations

from agent.collectors.pod_baseline import _require_pod_target, collect_pod_baseline
from agent.core.models import Investigation


def collect_oom_killer(investigation: Investigation) -> None:
    investigation.target.playbook = "oom_killer"
    target = _require_pod_target(investigation, "oom_killer")
    if target is None:
        return
    collect_pod_baseline(investigation, events_limit=50)

    labels = investigation.alert.labels or {}
    annotations = investigation.alert.annotations or {}
    investigation.evidence.k8s.oom_hint = {
        "container": (labels.get("container") if isinstance(labels, dict) else None)
        or (labels.get("Container") if isinstance(labels, dict) else None),
        "summary": (annotations.get("summary") if isinstance(annotations, dict) else None),
        "description": (annotations.get("description") if isinstance(annotations, dict) else None),
    }


__all__ = ["collect_oom_killer"]
