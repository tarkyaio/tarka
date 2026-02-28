from __future__ import annotations

from typing import List

import pytest


class _Sim:
    def __init__(self, resolution_category: str | None) -> None:
        self.resolution_category = resolution_category


def _mk_investigation_with_hypothesis(hypothesis_id: str, confidence: int = 50):
    from datetime import datetime, timedelta

    from agent.core.models import AlertInstance, Investigation, TargetRef, TimeWindow

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    inv = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "X"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=start, end_time=end),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1"),
    )
    # Minimal hypothesis list; calibration acts on this.
    from agent.core.models import Hypothesis

    inv.analysis.hypotheses = [Hypothesis(hypothesis_id=hypothesis_id, title="t", confidence_0_100=confidence)]
    return inv


def test_memory_calibration_skips_when_memory_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.diagnostics.memory_calibration import maybe_boost_hypotheses_from_memory

    inv = _mk_investigation_with_hypothesis("cpu_capacity_limit", confidence=50)
    maybe_boost_hypotheses_from_memory(inv, inv.analysis.hypotheses)
    assert inv.analysis.hypotheses[0].confidence_0_100 == 50


def test_memory_calibration_boosts_when_dominant_category(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force memory enabled without touching real env config.
    import agent.diagnostics.memory_calibration as mc

    monkeypatch.setattr(mc, "_env_memory_enabled", lambda: True)

    def fake_find_similar_runs(_inv, limit: int = 20):  # type: ignore[no-untyped-def]
        sims: List[object] = [_Sim("capacity"), _Sim("capacity"), _Sim("capacity"), _Sim("deploy")]
        return True, "ok", sims

    # The calibration module imports find_similar_runs from agent.memory.case_retrieval at runtime;
    # patch the module-level import target too.
    monkeypatch.setattr("agent.memory.case_retrieval.find_similar_runs", fake_find_similar_runs)

    inv = _mk_investigation_with_hypothesis("cpu_capacity_limit", confidence=50)
    mc.maybe_boost_hypotheses_from_memory(inv, inv.analysis.hypotheses)

    h = inv.analysis.hypotheses[0]
    assert h.confidence_0_100 >= 60  # +10 bump at least
    assert any("Memory:" in x for x in (h.why or []))
    assert "memory.similar_cases" in (h.supporting_refs or [])
