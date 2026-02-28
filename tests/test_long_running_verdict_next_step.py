from datetime import datetime, timedelta


def test_long_running_informational_adds_alert_quality_next_step() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 10, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    # Unknown family -> informational fallback. Make it long-running via starts_at.
    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "SomeUnknownAlert", "severity": "info"},
            annotations={},
            starts_at=(end - timedelta(hours=132)).isoformat() + "Z",
        ),
        time_window=tw,
    )
    f = compute_features(investigation)
    assert f.quality.is_long_running is True

    _scores, verdict = score_investigation(investigation, f)
    assert verdict.classification == "informational"
    assert any("Alert is long-running" in s for s in verdict.next_steps)
