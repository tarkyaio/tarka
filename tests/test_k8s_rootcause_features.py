from datetime import datetime, timedelta


def test_k8s_rootcause_features_extracted_compactly() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, TimeWindow
    from agent.pipeline.features import compute_features

    end = datetime(2025, 1, 2, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    long_msg = "x" * 1000

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubernetesPodNotHealthy", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "p1"},
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "phase": "Pending",
                    "status_reason": "Unschedulable",
                    "status_message": long_msg,
                    "container_statuses": [
                        {"name": "app", "state": {"waiting": {"reason": "ImagePullBackOff", "message": "pull failed"}}},
                        {"name": "sidecar", "last_state": {"terminated": {"reason": "OOMKilled", "exit_code": 137}}},
                    ],
                },
                pod_conditions=[
                    {
                        "type": "Ready",
                        "status": "False",
                        "reason": "ContainersNotReady",
                        "message": "containers not ready",
                    },
                    {
                        "type": "PodScheduled",
                        "status": "False",
                        "reason": "Unschedulable",
                        "message": "Insufficient cpu",
                    },
                ],
                pod_events=[
                    {
                        "type": "Warning",
                        "reason": "FailedScheduling",
                        "message": "0/3 nodes are available: Insufficient cpu.",
                        "count": 12,
                        "last_timestamp": end.isoformat(),
                    }
                ],
            )
        ),
    )

    f = compute_features(investigation)
    assert f.k8s.status_reason == "Unschedulable"
    assert f.k8s.status_message is not None and len(f.k8s.status_message) <= 200
    assert any(c.type == "PodScheduled" and c.status == "False" for c in f.k8s.not_ready_conditions)
    assert any(w.container == "app" and w.reason == "ImagePullBackOff" for w in f.k8s.container_waiting_reasons_top)
    assert any(
        t.container == "sidecar" and t.reason == "OOMKilled" and t.exit_code == 137
        for t in f.k8s.container_last_terminated_top
    )
    assert any((e.reason or "") == "FailedScheduling" for e in f.k8s.recent_event_reasons_top)
    # OOMKilled should be inferred from container termination reason (not only from events).
    assert f.k8s.oom_killed is True


def test_pod_not_healthy_verdict_uses_failed_scheduling_event() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, MetricsEvidence, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 2, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubernetesPodNotHealthy", "severity": "info", "namespace": "ns1", "pod": "p1"},
            annotations={},
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "p1", "playbook": "pod_not_healthy"},
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={"phase": "Pending", "container_statuses": []},
                pod_conditions=[{"type": "Ready", "status": "False"}],
                pod_events=[
                    {
                        "type": "Warning",
                        "reason": "FailedScheduling",
                        "message": "0/3 nodes are available: Insufficient memory.",
                        "count": 5,
                        "last_timestamp": end.isoformat(),
                    }
                ],
            ),
            metrics=MetricsEvidence(
                pod_phase_signal={
                    "pod_phase_signal": [
                        {"metric": {"phase": "Pending"}, "values": [[0, "1"]]},
                    ]
                }
            ),
        ),
    )

    f = compute_features(investigation)
    scores, verdict = score_investigation(investigation, f)
    assert f.family == "pod_not_healthy"
    assert scores.impact_score > 0
    assert "FailedScheduling" in verdict.one_liner
    assert "Insufficient" in verdict.one_liner
    assert any("taints" in s or "affinity" in s or "quotas" in s for s in verdict.next_steps)
