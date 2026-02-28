from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from agent.core.models import Hypothesis, Investigation


def _env_memory_enabled() -> bool:
    """
    Memory calibration is only enabled when the memory subsystem is enabled.
    """
    try:
        from agent.memory.config import load_memory_config

        cfg = load_memory_config()
        return bool(cfg.memory_enabled)
    except Exception:
        return False


def _hypothesis_to_resolution_category(hypothesis_id: str) -> Optional[str]:
    """
    Map hypothesis IDs (universal diagnostics) to coarse resolution categories.

    These categories align with the on-call UI resolution picker.
    """
    hid = (hypothesis_id or "").strip().lower()
    mapping = {
        # capacity-like
        "cpu_capacity_limit": "capacity",
        "memory_limit_oom": "capacity",
        "memory_pressure": "capacity",
        # k8s rollout
        "rollout_blocked_or_regression": "k8s_rollout",
        # config-ish
        "misconfig_or_missing_secret_configmap": "config",
        "image_pull_failure": "config",
        # meta/control plane
        "meta_alert": "unknown",
        "scrape_target_unreachable": "unknown",
        "upstream_or_regression": "unknown",
        "crashloop_app_failure": "unknown",
        "obs_pipeline_degraded": "unknown",
    }
    return mapping.get(hid)


def _resolution_stats(similar_items: List[object]) -> Tuple[int, Dict[str, int]]:
    """
    Count resolved similar cases by category.
    """
    total = 0
    counts: Dict[str, int] = {}
    for s in similar_items or []:
        cat = getattr(s, "resolution_category", None)
        if not cat:
            continue
        c = str(cat).strip().lower()
        if not c:
            continue
        total += 1
        counts[c] = counts.get(c, 0) + 1
    return total, counts


def maybe_boost_hypotheses_from_memory(investigation: Investigation, hyps: List[Hypothesis]) -> None:
    """
    Best-effort, non-blocking memory-based calibration.

    Semantics:
    - Only runs when MEMORY_ENABLED=1 (memory subsystem enabled).
    - Uses similar resolved cases to *soft boost* hypothesis confidence and add an explicit why-bullet.
    - Never decreases confidence (no negative learning without stronger supervision).
    """
    if not hyps:
        return
    if not _env_memory_enabled():
        return

    try:
        from agent.memory.case_retrieval import find_similar_runs
    except Exception:
        return

    try:
        ok, _msg, sims = find_similar_runs(investigation, limit=20)
        if not ok or not sims:
            return
        total, counts = _resolution_stats(sims)
        if total < 3:
            return
    except Exception:
        return

    # Boost only when a category is dominant among similar resolved cases.
    # Thresholds are intentionally conservative.
    for h in hyps:
        cat = _hypothesis_to_resolution_category(h.hypothesis_id)
        if not cat:
            continue
        n = counts.get(cat.lower(), 0)
        frac = (float(n) / float(total)) if total > 0 else 0.0
        if n >= 2 and frac >= 0.6:
            bump = 10 if frac < 0.8 else 20
            try:
                h.confidence_0_100 = max(0, min(100, int(h.confidence_0_100) + bump))
            except Exception:
                pass
            note = f"Memory: {n}/{total} similar resolved cases were categorized as `{cat}`."
            if note not in (h.why or []):
                h.why.append(note)
            if "memory.similar_cases" not in (h.supporting_refs or []):
                h.supporting_refs.append("memory.similar_cases")
