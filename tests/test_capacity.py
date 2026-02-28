from datetime import datetime


def test_analyze_capacity_populates_investigation(monkeypatch) -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.capacity import analyze_capacity

    end = datetime(2025, 1, 1, 0, 0, 0)
    tw = TimeWindow(window="1h", start_time=end, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp", labels={"alertname": "A", "namespace": "ns1", "pod": "p1"}, annotations={}
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1"},
        evidence={"k8s": {"rollout_status": {"kind": "Deployment", "name": "demo-api"}}},
    )

    # Return empty vectors for all instant queries.
    monkeypatch.setattr("agent.pipeline.capacity.query_prometheus_instant", lambda *a, **k: [])

    analyze_capacity(investigation)

    assert investigation.analysis.capacity is not None
    assert investigation.analysis.capacity.status in ("ok", "unavailable")
