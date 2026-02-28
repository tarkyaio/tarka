from datetime import datetime


def test_pipeline_returns_investigation_for_minimal_alert(monkeypatch) -> None:
    import agent.pipeline.pipeline as pipeline_mod
    from agent.core.models import Investigation

    monkeypatch.setattr(pipeline_mod, "collect_evidence_via_modules", lambda _b: False)

    # Stub time window parsing to deterministic values
    now = datetime(2025, 1, 1, 0, 0, 0)

    def fake_parse_time_window(_tw: str):
        return now, now

    monkeypatch.setattr(pipeline_mod, "parse_time_window", fake_parse_time_window)

    # Stub playbook resolution + execution
    def fake_playbook(investigation: Investigation) -> None:
        # Investigation-first playbooks mutate in place.
        investigation.target.playbook = "default"
        investigation.evidence.logs.logs = []
        investigation.evidence.logs.logs_status = "unavailable"

    monkeypatch.setattr(pipeline_mod, "get_playbook_for_alert", lambda _name: fake_playbook)
    monkeypatch.setattr(pipeline_mod, "default_playbook", fake_playbook)

    alert = {
        "fingerprint": "fp1",
        "labels": {"alertname": "X", "namespace": "ns1", "pod": "p1"},
        "annotations": {},
        "status": {"state": "firing"},
    }

    investigation = pipeline_mod.run_investigation(alert=alert, time_window="1h")
    assert isinstance(investigation, Investigation)
    assert investigation.alert.fingerprint == "fp1"
    assert investigation.target.namespace == "ns1"
    assert investigation.target.pod == "p1"


def test_pipeline_promotes_team_from_alert_labels(monkeypatch) -> None:
    """
    If alert labels include org routing metadata (team/environment), ensure we promote them
    to investigation.target.* without requiring K8s owner-chain lookups. This keeps list views
    consistent (team comes from analysis_json.target.team).
    """
    import agent.pipeline.pipeline as pipeline_mod
    from agent.core.models import Investigation

    monkeypatch.setattr(pipeline_mod, "collect_evidence_via_modules", lambda _b: False)

    now = datetime(2025, 1, 1, 0, 0, 0)

    def fake_parse_time_window(_tw: str):
        return now, now

    monkeypatch.setattr(pipeline_mod, "parse_time_window", fake_parse_time_window)

    def fake_playbook(investigation: Investigation) -> None:
        investigation.target.playbook = "default"

    monkeypatch.setattr(pipeline_mod, "get_playbook_for_alert", lambda _name: fake_playbook)
    monkeypatch.setattr(pipeline_mod, "default_playbook", fake_playbook)

    alert = {
        "fingerprint": "fp_team",
        "labels": {
            "alertname": "X",
            # include pod labels to ensure we don't attempt K8s lookups when team/env is present
            "namespace": "control",
            "pod": "some-pod",
            "team": "Observability",
            "environment": "prod",
        },
        "annotations": {},
        "status": {"state": "firing"},
    }

    investigation = pipeline_mod.run_investigation(alert=alert, time_window="1h")
    assert investigation.target.team == "Observability"
    assert investigation.target.environment == "prod"


def test_pipeline_pod_target_does_not_copy_scrape_job_service_instance(monkeypatch) -> None:
    import agent.pipeline.pipeline as pipeline_mod
    from agent.core.models import DerivedFeatures, DeterministicScores, DeterministicVerdict, Investigation

    monkeypatch.setattr(pipeline_mod, "collect_evidence_via_modules", lambda _b: False)

    now = datetime(2025, 1, 1, 0, 0, 0)

    def fake_parse_time_window(_tw: str):
        return now, now

    monkeypatch.setattr(pipeline_mod, "parse_time_window", fake_parse_time_window)

    # Avoid external I/O and keep the test focused on target parsing.
    def fake_playbook(investigation: Investigation) -> None:
        investigation.target.playbook = "pod_not_healthy"

    monkeypatch.setattr(pipeline_mod, "get_playbook_for_alert", lambda _name: fake_playbook)
    monkeypatch.setattr(pipeline_mod, "default_playbook", fake_playbook)
    monkeypatch.setattr(pipeline_mod, "analyze_noise", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "enrich_investigation_with_signal_queries", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "analyze_changes", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "analyze_capacity", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "compute_features", lambda _b: DerivedFeatures(family="pod_not_healthy"))
    monkeypatch.setattr(
        pipeline_mod,
        "score_investigation",
        lambda _b, _f: (
            DeterministicScores(impact_score=0, confidence_score=0, noise_score=0),
            DeterministicVerdict(classification="informational", primary_driver="test", one_liner="x"),
        ),
    )

    alert = {
        "fingerprint": "fp2",
        "labels": {
            "alertname": "KubernetesPodNotHealthy",
            "namespace": "test",
            "pod": "room-management-api-7cf5d76f57-x5kv9",
            # scrape metadata from kube-state-metrics:
            "job": "kube-state-metrics",
            "service": "victoria-metrics-kube-state-metrics",
            "instance": "172.29.67.184:8080",
            "container": "kube-state-metrics",
        },
        "annotations": {},
        "status": {"state": "active"},
    }

    investigation = pipeline_mod.run_investigation(alert=alert, time_window="1h")
    assert investigation.target.target_type == "pod"
    assert investigation.target.namespace == "test"
    assert investigation.target.pod == "room-management-api-7cf5d76f57-x5kv9"

    # Scrape metadata should not be treated as target identity for a pod incident.
    assert investigation.target.job is None
    assert investigation.target.service is None
    assert investigation.target.instance is None
    # And the container label from KSM should not be treated as incident container.
    assert investigation.target.container is None


def test_pipeline_rollout_health_does_not_treat_kube_state_metrics_pod_as_target(monkeypatch) -> None:
    """
    Rollout-health alerts often include the kube-state-metrics pod label; that is scrape metadata,
    not the affected workload. Ensure pipeline does not classify these as pod-target incidents.
    """
    import agent.pipeline.pipeline as pipeline_mod
    from agent.core.models import DerivedFeatures, DeterministicScores, DeterministicVerdict, Investigation

    monkeypatch.setattr(pipeline_mod, "collect_evidence_via_modules", lambda _b: False)

    now = datetime(2025, 1, 1, 0, 0, 0)

    def fake_parse_time_window(_tw: str):
        return now, now

    monkeypatch.setattr(pipeline_mod, "parse_time_window", fake_parse_time_window)

    def fake_playbook(investigation: Investigation) -> None:
        investigation.target.playbook = "default"

    monkeypatch.setattr(pipeline_mod, "get_playbook_for_alert", lambda _name: fake_playbook)
    monkeypatch.setattr(pipeline_mod, "default_playbook", fake_playbook)
    monkeypatch.setattr(pipeline_mod, "analyze_noise", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "enrich_investigation_with_signal_queries", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "analyze_changes", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "analyze_capacity", lambda _b: None)
    monkeypatch.setattr(pipeline_mod, "compute_features", lambda _b: DerivedFeatures(family="k8s_rollout_health"))
    monkeypatch.setattr(
        pipeline_mod,
        "score_investigation",
        lambda _b, _f: (
            DeterministicScores(impact_score=0, confidence_score=0, noise_score=0),
            DeterministicVerdict(classification="informational", primary_driver="test", one_liner="x"),
        ),
    )

    alert = {
        "fingerprint": "fp3",
        "labels": {
            "alertname": "KubeDeploymentRolloutStuck",
            "namespace": "test",
            # scrape metadata from kube-state-metrics:
            "pod": "victoria-metrics-kube-state-metrics-xxxx",
            "job": "kube-state-metrics",
            "service": "victoria-metrics-kube-state-metrics",
            "container": "kube-state-metrics",
            # affected workload identity:
            "deployment": "victoria-metrics-kube-state-metrics",
        },
        "annotations": {},
        "status": {"state": "active"},
    }

    investigation = pipeline_mod.run_investigation(alert=alert, time_window="1h")
    assert investigation.analysis.features is not None
    assert investigation.analysis.features.family == "k8s_rollout_health"
    assert investigation.target.target_type != "pod"
    assert investigation.target.pod is None
