from __future__ import annotations

from typing import List, Protocol

from agent.core.models import Hypothesis, Investigation


class DiagnosticModule(Protocol):
    """
    Universal diagnostic module contract.

    Modules are designed to be:
    - portable across orgs
    - deterministic and explainable
    - safe by default (collect is read-only; actions are proposals only)
    """

    module_id: str

    def applies(self, investigation: Investigation) -> bool:
        """Return True if this module should run for the given investigation."""

    def collect(self, investigation: Investigation) -> None:
        """
        Best-effort evidence gathering. Must not raise.

        NOTE: In early iterations, this may be a no-op if evidence was already gathered by playbooks.
        """

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        """Return ranked (or partially-ranked) hypotheses for this module."""
