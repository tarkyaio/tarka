from __future__ import annotations

from datetime import datetime, timedelta


def _base_investigation(*, family: str):
    from agent.core.models import AlertInstance, DerivedFeatures, Investigation, TargetRef, TimeWindow

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    inv = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "X"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=start, end_time=end),
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1"),
    )
    inv.analysis.features = DerivedFeatures(family=family)
    return inv


def test_run_diagnostics_emits_capacity_hypothesis_for_cpu_throttling() -> None:
    from agent.diagnostics.engine import run_diagnostics

    inv = _base_investigation(family="cpu_throttling")
    inv.analysis.features.metrics.cpu_throttle_p95_pct = 28.21
    inv.analysis.features.metrics.cpu_near_limit = False

    run_diagnostics(inv)
    assert inv.analysis.hypotheses
    assert any(h.hypothesis_id == "cpu_capacity_limit" for h in inv.analysis.hypotheses)


def test_run_diagnostics_emits_meta_hypothesis_for_meta_family() -> None:
    from agent.diagnostics.engine import run_diagnostics

    inv = _base_investigation(family="meta")
    inv.alert.labels["alertname"] = "InfoInhibitor"

    run_diagnostics(inv)
    assert inv.analysis.hypotheses
    top = inv.analysis.hypotheses[0]
    assert top.hypothesis_id == "meta_alert"
    assert top.confidence_0_100 >= 80
