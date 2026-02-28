from datetime import datetime, timedelta


def _tw():
    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    return start, end


def test_http_5xx_family_scores_actionable() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    start, end = _tw()
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "Http5xxRateHigh",
                "severity": "critical",
                "namespace": "ns1",
                "pod": "p1",
                "service": "svc1",
            },
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "playbook": "http_5xx"},
        evidence={
            "metrics": {
                "http_5xx": {
                    "series": [{"metric": {}, "values": [[0, "0.2"], [1, "0.3"], [2, "0.15"]]}],
                    "query_used": "q",
                }
            }
        },
    )

    f = compute_features(investigation)
    assert f.family == "http_5xx"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.primary_driver == "http_5xx"
    assert scores.impact_score >= 60


def test_oom_killed_family_scores_actionable() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    start, end = _tw()
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubernetesContainerOomKiller",
                "severity": "warning",
                "namespace": "ns1",
                "pod": "p1",
            },
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "playbook": "oom_killer"},
        evidence={"k8s": {"pod_events": [{"type": "Warning", "reason": "OOMKilling", "message": "OOMKilled"}]}},
    )
    f = compute_features(investigation)
    assert f.family == "oom_killed"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.primary_driver == "oom_killed"
    assert scores.impact_score >= 60


def test_memory_pressure_family_scores_actionable() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    start, end = _tw()
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "MemoryPressure", "severity": "warning", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "container": "app", "playbook": "memory_pressure"},
        evidence={
            "metrics": {
                "memory_metrics": {
                    "memory_usage_bytes": [{"metric": {"container": "app"}, "values": [[0, "900"], [1, "950"]]}],
                    "memory_limits_bytes": [{"metric": {"container": "app"}, "values": [[0, "1000"]]}],
                }
            }
        },
    )
    f = compute_features(investigation)
    assert f.family == "memory_pressure"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.primary_driver == "memory_pressure"
    assert scores.impact_score >= 60


def test_nonpod_target_does_not_crash_and_scores_generic() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    start, end = _tw()
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TargetDown", "severity": "critical", "instance": "1.2.3.4:9100"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "node", "instance": "1.2.3.4:9100", "playbook": "default"},
    )
    f = compute_features(investigation)
    assert f.family == "target_down"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.primary_driver in ("generic", "target_down")
    assert scores.impact_score >= 0
