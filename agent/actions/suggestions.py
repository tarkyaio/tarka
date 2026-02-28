from __future__ import annotations

from typing import List

from agent.authz.policy import load_action_policy
from agent.core.models import ActionProposal, Hypothesis, Investigation


def _scope_allowed(inv: Investigation) -> bool:
    p = load_action_policy()
    if not p.enabled:
        return False
    ns = (inv.target.namespace or "").strip() or None
    cl = (inv.target.cluster or "").strip() or None
    if p.namespace_allowlist is not None and ns and ns not in p.namespace_allowlist:
        return False
    if p.cluster_allowlist is not None and cl and cl not in p.cluster_allowlist:
        return False
    return True


def _append_unique(actions: List[ActionProposal], a: ActionProposal) -> None:
    key = (a.action_type or "", a.title or "")
    for x in actions:
        if (x.action_type or "", x.title or "") == key:
            return
    actions.append(a)


def attach_suggested_actions(investigation: Investigation) -> None:
    """
    Attach *suggested* action proposals to hypotheses (policy-gated visibility).

    Important:
    - These are suggestions only; they are NOT executed by the agent.
    - Approval/execution workflow is handled via separate case actions API + audit trail.
    """
    if not investigation.analysis.hypotheses:
        return
    if not _scope_allowed(investigation):
        return

    tgt = investigation.target
    ns = tgt.namespace or ""
    pod = tgt.pod or ""
    wk_kind = tgt.workload_kind or ""
    wk_name = tgt.workload_name or ""

    for h in investigation.analysis.hypotheses:
        if not isinstance(h, Hypothesis):
            continue
        actions = h.proposed_actions or []

        hid = (h.hypothesis_id or "").strip().lower()

        # Crashloops / pod lifecycle
        if hid in {"crashloop_app_failure", "misconfig_or_missing_secret_configmap", "image_pull_failure"}:
            if ns and pod:
                _append_unique(
                    actions,
                    ActionProposal(
                        action_type="restart_pod",
                        title="Restart the pod (delete pod to force recreate)",
                        risk="medium",
                        preconditions=[
                            "Confirm this is safe (stateless or has proper state handling).",
                            "Capture `kubectl logs --previous` / `describe pod` before restart.",
                        ],
                        execution_payload={"kind": "Pod", "namespace": ns, "pod": pod},
                    ),
                )

        # Rollout health / regression
        if hid in {"rollout_blocked_or_regression"}:
            if ns and wk_kind and wk_name:
                _append_unique(
                    actions,
                    ActionProposal(
                        action_type="rollout_restart",
                        title=f"Rollout restart {wk_kind}/{wk_name}",
                        risk="medium",
                        preconditions=[
                            "Confirm there is an incident impact and restart is an accepted mitigation.",
                            "If this is a regression, prefer rollback after confirmation.",
                        ],
                        execution_payload={"kind": wk_kind, "namespace": ns, "name": wk_name, "op": "rollout_restart"},
                    ),
                )
                _append_unique(
                    actions,
                    ActionProposal(
                        action_type="rollback_workload",
                        title=f"Rollback {wk_kind}/{wk_name} to last known good",
                        risk="high",
                        preconditions=[
                            "Confirm regression timing aligns with rollout/change correlation.",
                            "Ensure rollback procedure/runbook exists for this workload.",
                        ],
                        execution_payload={"kind": wk_kind, "namespace": ns, "name": wk_name, "op": "rollback"},
                    ),
                )

        # Capacity
        if hid in {"cpu_capacity_limit", "memory_limit_oom", "memory_pressure"}:
            if ns and wk_kind and wk_name:
                _append_unique(
                    actions,
                    ActionProposal(
                        action_type="scale_workload",
                        title=f"Scale {wk_kind}/{wk_name} (if horizontally scalable)",
                        risk="medium",
                        preconditions=[
                            "Confirm HPA/replicas scaling is safe and won't overload dependencies.",
                            "Confirm the bottleneck is capacity and not a downstream outage/regression.",
                        ],
                        execution_payload={"kind": wk_kind, "namespace": ns, "name": wk_name, "op": "scale"},
                    ),
                )

        h.proposed_actions = actions
