from datetime import datetime


def test_analyze_noise_populates_investigation(monkeypatch) -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.noise import analyze_noise

    end = datetime(2025, 1, 1, 0, 0, 0)
    tw = TimeWindow(window="1h", start_time=end, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "A", "namespace": "ns1", "cluster": "c1"},
            annotations={},
        ),
        time_window=tw,
    )

    # Fake Prometheus instant responses: count(ALERTS)=2, count(firing)=1, resets=0
    def fake_query(_q: str, at: datetime):
        if "count(ALERTS" in _q and "alertstate" not in _q:
            return [{"metric": {}, "value": [at.timestamp(), "2"]}]
        if "count(ALERTS" in _q and "alertstate" in _q:
            return [{"metric": {}, "value": [at.timestamp(), "1"]}]
        return [{"metric": {}, "value": [at.timestamp(), "0"]}]

    monkeypatch.setattr("agent.pipeline.noise.query_prometheus_instant", fake_query)

    analyze_noise(investigation)

    assert investigation.analysis.noise is not None
    assert investigation.analysis.noise.prometheus["status"] == "ok"
    assert investigation.analysis.noise.prometheus["active_instances"] == 2.0
    assert investigation.analysis.noise.prometheus["firing_instances"] == 1.0


def test_noise_missing_container_includes_rule_snippet() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.noise import analyze_noise

    end = datetime(2025, 1, 1, 0, 0, 0)
    tw = TimeWindow(window="1h", start_time=end, end_time=end)
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp", labels={"alertname": "A", "namespace": "ns1", "pod": "p1"}, annotations={}
        ),
        time_window=tw,
    )
    analyze_noise(investigation)
    assert investigation.analysis.noise is not None
    ml = investigation.analysis.noise.missing_labels
    assert ml is not None
    assert "container" in (ml.missing or [])
    assert any("sum by(container,pod,namespace)" in r for r in (ml.recommendation or []))
