"""Shared Kubernetes read-only context gatherer for collectors/modules."""

from typing import Any, Dict, List, Optional

from agent.providers.k8s_provider import (
    get_pod_conditions,
    get_pod_events,
    get_pod_info,
    get_pod_owner_chain,
    get_workload_rollout_status,
)


def gather_pod_context(pod_name: str, namespace: str, events_limit: int = 20) -> Dict[str, Any]:
    """
    Gather Kubernetes read-only context for a Pod.

    Best-effort and never raises. Returns:
      {
        "pod_info": dict|None,
        "pod_conditions": list[dict],
        "pod_events": list[dict],
        "owner_chain": dict|None,
        "rollout_status": dict|None,
        "errors": list[str],
      }
    """
    errors: List[str] = []

    pod_info: Optional[Dict[str, Any]] = None
    pod_conditions: List[Dict[str, Any]] = []
    pod_events: List[Dict[str, Any]] = []
    owner_chain: Optional[Dict[str, Any]] = None
    rollout_status: Optional[Dict[str, Any]] = None

    try:
        pod_info = get_pod_info(pod_name, namespace)
    except Exception as e:
        errors.append(f"pod_info: {e}")

    try:
        pod_conditions = get_pod_conditions(pod_name, namespace)
    except Exception as e:
        errors.append(f"pod_conditions: {e}")

    try:
        pod_events = get_pod_events(pod_name, namespace, limit=events_limit)
    except Exception as e:
        errors.append(f"pod_events: {e}")

    try:
        owner_chain = get_pod_owner_chain(pod_name, namespace)
    except Exception as e:
        errors.append(f"owner_chain: {e}")

    try:
        wl = (owner_chain or {}).get("workload") if isinstance(owner_chain, dict) else None
        if isinstance(wl, dict) and wl.get("kind") and wl.get("name"):
            rollout_status = get_workload_rollout_status(namespace=namespace, kind=wl["kind"], name=wl["name"])
    except Exception as e:
        errors.append(f"rollout_status: {e}")

    return {
        "pod_info": pod_info,
        "pod_conditions": pod_conditions,
        "pod_events": pod_events,
        "owner_chain": owner_chain,
        "rollout_status": rollout_status,
        "errors": errors,
    }


__all__ = ["gather_pod_context"]
