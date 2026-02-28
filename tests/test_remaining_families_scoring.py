from datetime import datetime, timedelta


def _tw():
    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    return start, end


def test_target_down_family_scores_actionable_when_many_firing() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    start, end = _tw()
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "TargetDown",
                "severity": "critical",
                "instance": "1.2.3.4:9100",
                "job": "node-exporter",
            },
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "node", "instance": "1.2.3.4:9100", "job": "node-exporter", "playbook": "default"},
        analysis={
            "noise": {
                "prometheus": {"status": "ok", "firing_instances": 10, "active_instances": 10, "selector": "{}"},
                "flap": {"lookback": "24h", "flaps_estimate": 0, "flap_score_0_100": 0, "notes": []},
                "cardinality": {
                    "ephemeral_labels_present": ["instance"],
                    "recommended_group_by": ["alertname", "job"],
                    "recommended_drop_labels": ["instance"],
                },
                "missing_labels": {"missing": [], "inferred": [], "recommendation": []},
                "notes": [],
            }
        },
    )

    f = compute_features(investigation)
    assert f.family == "target_down"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.primary_driver == "target_down"
    assert scores.impact_score >= 60


def test_k8s_rollout_health_family_scores_actionable() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    start, end = _tw()
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubeDaemonSetRolloutStuck",
                "severity": "warning",
                "namespace": "ns1",
                "daemonset": "ds1",
            },
            annotations={},
        ),
        time_window=tw,
        target={
            "target_type": "workload",
            "namespace": "ns1",
            "workload_kind": "DaemonSet",
            "workload_name": "ds1",
            "playbook": "default",
        },
    )
    f = compute_features(investigation)
    assert f.family == "k8s_rollout_health"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.primary_driver == "k8s_rollout_health"
    assert scores.impact_score >= 60


def test_observability_pipeline_family_scores_actionable() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    start, end = _tw()
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "RowsRejectedOnIngestion", "severity": "warning", "namespace": "control"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "cluster", "cluster": "c1", "playbook": "default"},
    )
    f = compute_features(investigation)
    assert f.family == "observability_pipeline"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.primary_driver == "observability_pipeline"
    assert scores.impact_score >= 50
