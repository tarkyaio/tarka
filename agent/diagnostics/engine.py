from __future__ import annotations

from typing import List

from agent.actions.suggestions import attach_suggested_actions
from agent.core.models import Hypothesis, Investigation
from agent.diagnostics.memory_calibration import maybe_boost_hypotheses_from_memory
from agent.diagnostics.registry import get_default_registry


def run_diagnostics(investigation: Investigation, *, do_collect: bool = False) -> None:
    """
    Run applicable diagnostic modules (best-effort) and populate `analysis.hypotheses`.

    Determinism goals:
    - stable ordering for equal confidences
    - never raises
    """
    try:
        reg = get_default_registry()
        mods = reg.applicable(investigation)
    except Exception as e:
        investigation.errors.append(f"Diagnostics: registry error: {e}")
        return

    hyps: List[Hypothesis] = []

    for m in mods:
        mid = getattr(m, "module_id", "unknown")
        if do_collect:
            try:
                m.collect(investigation)
            except Exception as e:
                investigation.errors.append(f"Diagnostics({mid}): collect error: {e}")
        try:
            hyps.extend(list(m.diagnose(investigation) or []))
        except Exception as e:
            investigation.errors.append(f"Diagnostics({mid}): diagnose error: {e}")

    # Deterministic ranking: confidence desc, then hypothesis_id asc.
    try:
        hyps_sorted = sorted(hyps, key=lambda h: (-int(getattr(h, "confidence_0_100", 0)), str(h.hypothesis_id)))
    except Exception:
        hyps_sorted = hyps

    # Optional: memory-based calibration (best-effort, guarded by MEMORY_ENABLED).
    try:
        maybe_boost_hypotheses_from_memory(investigation, hyps_sorted)
        hyps_sorted = sorted(hyps_sorted, key=lambda h: (-int(getattr(h, "confidence_0_100", 0)), str(h.hypothesis_id)))
    except Exception:
        pass

    # Cap to keep UI/report concise
    investigation.analysis.hypotheses = hyps_sorted[:10]

    # Attach policy-gated action suggestions to the top hypotheses.
    try:
        attach_suggested_actions(investigation)
    except Exception:
        pass
