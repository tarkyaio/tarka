from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from agent.core.models import Investigation
from agent.diagnostics.base import DiagnosticModule


@dataclass
class DiagnosticRegistry:
    modules: List[DiagnosticModule] = field(default_factory=list)

    def register(self, module: DiagnosticModule) -> None:
        self.modules.append(module)

    def applicable(self, investigation: Investigation) -> List[DiagnosticModule]:
        out: List[DiagnosticModule] = []
        for m in self.modules:
            try:
                if m.applies(investigation):
                    out.append(m)
            except Exception as e:
                investigation.errors.append(f"Diagnostics({getattr(m, 'module_id', 'unknown')}): applies error: {e}")
        return out


_DEFAULT_REGISTRY: DiagnosticRegistry | None = None


def get_default_registry() -> DiagnosticRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is not None:
        return _DEFAULT_REGISTRY
    reg = DiagnosticRegistry()
    # Explicit composition (single source of truth lives in `agent.diagnostics.universal`).
    from agent.diagnostics.universal import DEFAULT_MODULE_CLASSES  # noqa: WPS433

    for cls in DEFAULT_MODULE_CLASSES:
        try:
            reg.register(cls())
        except Exception as e:
            # This should never happen; keep best-effort for production robustness.
            # Registry uses investigation.errors in applicable(), but we don't have an Investigation here.
            raise RuntimeError(f"Failed to register diagnostic module {cls}: {e}") from e

    _DEFAULT_REGISTRY = reg
    return reg
