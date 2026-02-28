from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_pod_not_healthy_imagepullbackoff_next_steps_use_evidence_bucket_and_ecr() -> None:
    from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, TargetRef, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=1)

    # Minimal pod info containing ImagePullBackOff waiting message.
    k8s = K8sEvidence(
        pod_info={
            "phase": "Pending",
            "service_account_name": "default",
            "container_statuses": [
                {
                    "name": "app",
                    "state": {
                        "waiting": {
                            "reason": "ImagePullBackOff",
                            "message": 'Back-off pulling image "123456789012.dkr.ecr.us-east-1.amazonaws.com/example-org/example-app:badtag": ErrImagePull: rpc error: code = NotFound desc = ...',
                        }
                    },
                }
            ],
        },
        pod_events=[
            {"type": "Warning", "reason": "Failed", "message": "Error: ImagePullBackOff", "count": 3},
        ],
    )
    # Simulate playbook-attached diagnostics (deterministic).
    k8s.image_pull_diagnostics = {
        "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/example-org/example-app:badtag",
        "error_bucket": "not_found",
        "error_evidence": "rpc error: code = NotFound",
        "service_account_name": "default",
        "service_account_image_pull_secrets": [],
        "ecr_check": {"status": "missing", "detail": "ImageNotFoundException"},
    }

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={
                "alertname": "KubernetesPodNotHealthyCritical",
                "severity": "critical",
                "namespace": "accept-0",
                "pod": "p1",
            },
            annotations={},
            starts_at=end.isoformat(),
            normalized_state="firing",
        ),
        time_window=TimeWindow(window="1h", start_time=start, end_time=end),
        target=TargetRef(target_type="pod", namespace="accept-0", pod="p1"),
        evidence=Evidence(k8s=k8s),
    )

    f = compute_features(investigation)
    scores, verdict = score_investigation(investigation, f)

    assert verdict.primary_driver == "pod_not_healthy"
    txt = "\n".join(verdict.next_steps or [])
    assert "ServiceAccount `default` has **no** `imagePullSecrets`" in txt
    assert "Registry reported **NotFound**" in txt
    assert "ECR verification: **image not found**" in txt
