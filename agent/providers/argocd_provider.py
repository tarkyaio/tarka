"""Argo CD provider (placeholder)."""

from __future__ import annotations

from typing import Any, Dict, Optional


def get_application_health(app: str, *, server: Optional[str] = None) -> Dict[str, Any]:
    """Placeholder for future ArgoCD integration."""
    raise NotImplementedError("ArgoCD provider not implemented")
