from datetime import datetime, timedelta


def test_noise_score_increases_with_flap_and_cardinality(monkeypatch) -> None:
    import agent.pipeline.noise as noise_mod
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubePodCrashLooping",
                "severity": "warning",
                "namespace": "ns1",
                "pod": "p1",
                "container": "app",
                "instance": "10.0.0.1:9100",
                "uid": "abc",
            },
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "default"},
        evidence={
            "k8s": {
                "pod_info": {
                    "phase": "Running",
                    "container_statuses": [
                        {"name": "app", "restart_count": 10, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}
                    ],
                },
                "pod_conditions": [{"type": "Ready", "status": "False"}],
            },
            "metrics": {
                "restart_data": {"restart_increase_5m": [{"metric": {"container": "app"}, "values": [[0, "4"]]}]}
            },
        },
    )

    def fake_query(q: str, at):
        if "resets(ALERTS_FOR_STATE" in q:
            return [{"value": [0, "3"]}]  # flap_score=60 => NOISE_FLAP_MED (+20)
        return [{"value": [0, "1"]}]

    monkeypatch.setattr(noise_mod, "query_prometheus_instant", fake_query)
    noise_mod.analyze_noise(investigation)

    f = compute_features(investigation)
    scores, _ = score_investigation(investigation, f)
    # Noise should be > 0 because flap + cardinality are present.
    assert scores.noise_score > 0
    assert "NOISE_CARDINALITY" in scores.reason_codes


def test_confidence_penalty_only_for_missing_namespace_pod_labels() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubePodCrashLooping", "severity": "warning"},
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "default"},
        evidence={
            "k8s": {
                "pod_info": {
                    "phase": "Running",
                    "container_statuses": [{"name": "app", "state": {"waiting": {"reason": "CrashLoopBackOff"}}}],
                },
                "pod_conditions": [{"type": "Ready", "status": "False"}],
            }
        },
    )

    f = compute_features(investigation)
    scores, _ = score_investigation(investigation, f)
    assert "MISSING_LABEL_NAMESPACE" in scores.reason_codes
    assert "MISSING_LABEL_POD" in scores.reason_codes
