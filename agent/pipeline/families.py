"""Alert family registry (calibrate per family, not globally)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class FamilySpec:
    family: str
    match_substrings: List[str]
    match_playbooks: List[str] = None


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


FAMILIES: List[FamilySpec] = [
    # NOTE: do NOT key off the default playbook; many alerts route through it.
    FamilySpec(family="crashloop", match_substrings=["crashloop"], match_playbooks=["crashloop"]),
    FamilySpec(family="cpu_throttling", match_substrings=["throttl", "cputhrottl"], match_playbooks=["cpu_throttling"]),
    FamilySpec(
        family="pod_not_healthy",
        match_substrings=["podnothealthy", "kubernetespodnothealthy"],
        match_playbooks=["pod_not_healthy"],
    ),
    FamilySpec(family="http_5xx", match_substrings=["5xx", "http5xx"], match_playbooks=["http_5xx"]),
    FamilySpec(family="oom_killed", match_substrings=["oom", "oomkiller", "oomkilled"], match_playbooks=["oom_killer"]),
    FamilySpec(
        family="memory_pressure",
        match_substrings=["memorypressure", "memory_pressure"],
        match_playbooks=["memory_pressure"],
    ),
    # Non-pod families (scoring can be added later)
    FamilySpec(family="target_down", match_substrings=["targetdown"]),
    FamilySpec(
        family="job_failed",
        match_substrings=["jobfailed", "kubejobfailed"],
        match_playbooks=["job_failure"],
    ),
    FamilySpec(
        family="k8s_rollout_health",
        match_substrings=["replicasmismatch", "rolloutstuck", "deploymentreplicas"],
    ),
    FamilySpec(
        family="observability_pipeline",
        match_substrings=["alertingruleserror", "recordingrulesnodata", "rowsrejectedoningestion", "toomanylogs"],
    ),
    FamilySpec(family="meta", match_substrings=["infoinhibitor"]),
]


def detect_family(labels: Dict[str, object], playbook: Optional[str]) -> str:
    alertname = _norm(str(labels.get("alertname") or "")) if isinstance(labels, dict) else ""
    pb = _norm(playbook)
    hay = f"{alertname} {pb}"

    for spec in FAMILIES:
        if spec.match_playbooks:
            if pb and any(_norm(x) == pb for x in (spec.match_playbooks or [])):
                return spec.family
        if any(sub in hay for sub in (spec.match_substrings or [])):
            return spec.family
    return "generic"


def derive_target_type(labels: Dict[str, object], *, pod: Optional[str], namespace: Optional[str]) -> str:
    # Prefer explicit pod mapping
    if pod and namespace and pod != "Unknown" and namespace != "Unknown":
        return "pod"
    # Otherwise infer best-effort from common labels
    if labels.get("service") or labels.get("kubernetes_service_name"):
        return "service"
    if labels.get("instance"):
        return "node"
    if labels.get("cluster"):
        return "cluster"
    return "unknown"
