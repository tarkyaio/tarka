from datetime import datetime, timedelta


def test_infoinhibitor_is_classified_noisy() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "InfoInhibitor", "severity": "none"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "unknown", "playbook": "default"},
    )

    f = compute_features(investigation)
    assert f.family == "meta"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.classification == "noisy"
    assert scores.noise_score >= 70
    assert "META_ALERT" in scores.reason_codes
