from datetime import datetime, timedelta


def test_alert_lifecycle_normalized_state_and_ends_kind(monkeypatch) -> None:
    # Avoid running real playbooks/noise/signals.
    import agent.pipeline.pipeline as pipe

    monkeypatch.setattr(pipe, "collect_evidence_via_modules", lambda _b: False)

    monkeypatch.setattr(pipe, "get_playbook_for_alert", lambda _n: (lambda _b: None))
    monkeypatch.setattr(pipe, "analyze_noise", lambda _b: None)
    monkeypatch.setattr(pipe, "enrich_investigation_with_signal_queries", lambda _b: None)
    monkeypatch.setattr(pipe, "analyze_changes", lambda _b: None)
    monkeypatch.setattr(pipe, "analyze_capacity", lambda _b: None)

    now = datetime(2025, 1, 1, 0, 0, 0)
    alert = {
        "fingerprint": "fp",
        "labels": {"alertname": "TargetDown", "severity": "critical", "instance": "1.2.3.4:9100"},
        "annotations": {},
        "starts_at": (now - timedelta(minutes=10)).isoformat() + "Z",
        "ends_at": (now + timedelta(minutes=10)).isoformat() + "Z",
        "status": {"state": "active"},
    }

    investigation = pipe.run_investigation(alert=alert, time_window="1h")
    assert investigation.alert.state == "active"
    assert investigation.alert.normalized_state == "firing"
    assert investigation.alert.ends_at_kind == "expires_at"
