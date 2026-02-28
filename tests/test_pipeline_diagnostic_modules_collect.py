from __future__ import annotations

from datetime import datetime


def test_pipeline_can_collect_via_modules_and_skip_alertname_playbook(monkeypatch) -> None:
    """
    When a known family is detected, pipeline should collect evidence via diagnostic modules
    and not require alertname-based playbook routing.
    """
    import agent.pipeline.pipeline as pipeline_mod
    from agent.core.models import Investigation

    # Keep window deterministic
    now = datetime(2025, 1, 1, 0, 0, 0)

    def fake_parse_time_window(_tw: str):
        return now, now

    monkeypatch.setattr(pipeline_mod, "parse_time_window", fake_parse_time_window)

    # If alertname playbook routing is invoked, fail the test.
    monkeypatch.setattr(
        pipeline_mod,
        "get_playbook_for_alert",
        lambda _n: (_ for _ in ()).throw(AssertionError("playbook routing called")),
    )

    # Stub out the underlying playbook collector used by CapacityModule.collect so it doesn't do I/O.
    import agent.playbooks.cpu_throttling as cpu_pb

    def fake_cpu_throttling_playbook(inv: Investigation) -> None:
        inv.target.playbook = "cpu_throttling"
        inv.evidence.metrics.throttling_data = {"status": "ok"}

    monkeypatch.setattr(cpu_pb, "investigate_cpu_throttling_playbook", fake_cpu_throttling_playbook)

    # Avoid external I/O in later phases too.
    monkeypatch.setattr(pipeline_mod, "analyze_noise", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "enrich_investigation_with_signal_queries", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "analyze_changes", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "analyze_capacity", lambda _b: None)

    alert = {
        "fingerprint": "fp_mod",
        "labels": {"alertname": "CPUThrottlingHigh", "namespace": "ns1", "pod": "p1"},
        "annotations": {},
        "status": {"state": "firing"},
    }

    inv = pipeline_mod.run_investigation(alert=alert, time_window="1h")
    assert inv.target.playbook == "cpu_throttling"
    assert inv.evidence.metrics.throttling_data is not None
