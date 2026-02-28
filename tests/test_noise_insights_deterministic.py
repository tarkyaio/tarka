from datetime import datetime, timedelta


def test_noise_insights_flap_score_and_cardinality(monkeypatch) -> None:
    import agent.pipeline.noise as noise_mod
    from agent.core.models import AlertInstance, Investigation, TimeWindow

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubePodCrashLooping",
                "namespace": "ns1",
                "pod": "p1",
                "instance": "10.0.0.1:9100",
                "uid": "abc",
            },
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "default"},
    )

    # Mock Prometheus responses: active, firing, flaps
    def fake_query(q: str, at):
        if "count(ALERTS" in q and "alertstate" not in q:
            return [{"value": [0, "5"]}]
        if "count(ALERTS" in q and 'alertstate="firing"' in q:
            return [{"value": [0, "2"]}]
        if "resets(ALERTS_FOR_STATE" in q:
            return [{"value": [0, "3"]}]  # 3 flaps => score 60
        return []

    monkeypatch.setattr(noise_mod, "query_prometheus_instant", fake_query)
    noise_mod.analyze_noise(investigation)

    assert investigation.analysis.noise is not None
    assert investigation.analysis.noise.flap is not None
    assert investigation.analysis.noise.flap.flaps_estimate == 3.0
    assert investigation.analysis.noise.flap.flap_score_0_100 == 60
    assert investigation.analysis.noise.cardinality is not None
    assert "instance" in investigation.analysis.noise.cardinality.ephemeral_labels_present
    assert "uid" in investigation.analysis.noise.cardinality.ephemeral_labels_present


def test_features_quality_downgrades_when_labels_missing() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    # Pod-scoped family but missing namespace/pod labels
    investigation = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "KubePodCrashLooping"}, annotations={}),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "default"},
    )
    f = compute_features(investigation)
    assert f.family == "crashloop"
    assert f.quality.evidence_quality == "low"
    assert "labels.namespace" in f.quality.missing_inputs
    assert "labels.pod" in f.quality.missing_inputs
