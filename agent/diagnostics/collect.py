from __future__ import annotations

from agent.core.models import Investigation
from agent.diagnostics.registry import get_default_registry


def collect_evidence_via_modules(investigation: Investigation) -> bool:
    """
    Best-effort evidence collection via applicable diagnostic modules.

    Returns:
        True if at least one module successfully collected evidence.
        False if no modules applied OR all modules failed to collect.

    Resilience:
        - If all diagnostic modules fail, returns False so playbook fallback can run
        - Catches and logs all exceptions, never crashes the pipeline
        - Allows graceful degradation to playbook-based evidence collection

    Never raises.
    """
    try:
        reg = get_default_registry()
        mods = reg.applicable(investigation)
    except Exception as e:
        investigation.errors.append(f"Diagnostics: registry error: {e}")
        return False

    if not mods:
        return False

    # Track if at least one module successfully collected
    success_count = 0
    for m in mods:
        mid = getattr(m, "module_id", "unknown")
        try:
            m.collect(investigation)
            success_count += 1
        except Exception as e:
            investigation.errors.append(f"Diagnostics({mid}): collect error: {e}")

    # Return True only if at least one module succeeded
    # If all failed, return False so playbook fallback can run
    return success_count > 0
