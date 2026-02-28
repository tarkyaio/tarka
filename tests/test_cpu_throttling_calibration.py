from datetime import datetime, timedelta


def test_throttling_high_but_usage_low_sets_contradiction_flag() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "ContainerCpuThrottled", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "playbook": "cpu_throttling"},
        evidence={
            "metrics": {
                "throttling_data": {
                    "throttling_percentage": [
                        {"metric": {"container": "c1"}, "values": [[0, "35"], [1, "30"]]},
                    ]
                },
                "cpu_metrics": {
                    "cpu_usage": [{"metric": {"container": "c1"}, "values": [[0, "0.01"], [1, "0.02"]]}],
                    "cpu_limits": [{"metric": {"container": "c1"}, "values": [[0, "1.0"]]}],
                },
            }
        },
    )

    f = compute_features(investigation)
    assert "THROTTLING_HIGH_BUT_USAGE_LOW" in f.quality.contradiction_flags


def test_workload_known_removes_pod_cardinality_penalty_in_scoring(monkeypatch) -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "ContainerCpuThrottled", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={
            "namespace": "ns1",
            "pod": "p1",
            "workload_kind": "Deployment",
            "workload_name": "w1",
            "playbook": "cpu_throttling",
        },
        evidence={
            "metrics": {
                "throttling_data": {
                    "throttling_percentage": [{"metric": {"container": "c1"}, "values": [[0, "30"], [1, "30"]]}]
                },
                "cpu_metrics": {
                    "cpu_usage": [{"metric": {"container": "c1"}, "values": [[0, "0.5"], [1, "0.5"]]}],
                    "cpu_limits": [{"metric": {"container": "c1"}, "values": [[0, "1.0"]]}],
                },
            }
        },
        analysis={
            "noise": {
                "cardinality": {
                    "ephemeral_labels_present": ["pod"],
                    "recommended_group_by": ["alertname"],
                    "recommended_drop_labels": ["pod"],
                },
                "flap": {"lookback": "24h", "flaps_estimate": 0, "flap_score_0_100": 0, "notes": []},
                "missing_labels": {"missing": [], "inferred": [], "recommendation": []},
                "prometheus": {
                    "status": "ok",
                    "selector": "{}",
                    "active_instances": 1,
                    "firing_instances": 1,
                    "flap_resets_estimate": 0,
                    "lookback": "24h",
                },
                "label_shape": {},
                "notes": [],
            }
        },
    )

    f = compute_features(investigation)
    scores, _ = score_investigation(investigation, f)
    assert "NOISE_CARDINALITY" not in scores.reason_codes
