from __future__ import annotations

from typing import Optional

from agent.core.models import Investigation


def set_canonical_family(investigation: Investigation, family: str, *, source: str) -> None:
    """
    Set the canonical family for an investigation.

    We store this in `investigation.meta` to avoid expanding the strict Pydantic models for now.
    Invariants:
    - Set early (before module selection / feature extraction).
    - Prefer stability over re-detection to prevent drift between collectors/modules/scoring.
    """
    if not isinstance(getattr(investigation, "meta", None), dict):
        investigation.meta = {}
    fam = (family or "").strip() or "generic"
    investigation.meta["family"] = fam
    investigation.meta["family_source"] = (source or "").strip() or "unknown"
    # Back-compat: some code paths still read family_hint.
    investigation.meta["family_hint"] = fam


def get_family(investigation: Investigation, *, default: str = "generic") -> str:
    """
    Return the current family for an investigation, using a stable precedence order.
    """
    # 1) Canonical meta field (set in pipeline).
    try:
        meta = investigation.meta if isinstance(investigation.meta, dict) else {}
        fam = meta.get("family")
        if fam is not None and str(fam).strip():
            return str(fam).strip()
        # Back-compat
        fam2 = meta.get("family_hint")
        if fam2 is not None and str(fam2).strip():
            return str(fam2).strip()
    except Exception:
        pass

    # 2) Derived features (when present).
    try:
        f = investigation.analysis.features
        if f is not None and getattr(f, "family", None):
            return str(f.family).strip() or default
    except Exception:
        pass

    return default


def get_family_source(investigation: Investigation) -> Optional[str]:
    try:
        meta = investigation.meta if isinstance(investigation.meta, dict) else {}
        v = meta.get("family_source")
        s = str(v).strip() if v is not None else ""
        return s or None
    except Exception:
        return None
