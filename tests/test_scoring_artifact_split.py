from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_oom_artifact_is_low_confidence_and_does_not_overclaim() -> None:
    """
    If the scorer has OOM_CORROBORATION_MISSING, the verdict must not claim OOMKilled and must
    be explicit that this is low-confidence.
    """
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubernetesContainerOomKiller", "namespace": "ns1", "pod": "p1"},
            annotations={},
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
            starts_at=end.isoformat(),
        ),
        time_window=tw,
        target={"target_type": "pod", "namespace": "ns1", "pod": "p1", "playbook": "oom_killer"},
        evidence={},  # no k8s corroboration
    )
    f = compute_features(investigation)
    assert f.family == "oom_killed"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.classification == "artifact"
    assert "OOM_CORROBORATION_MISSING" in scores.reason_codes
    assert "ARTIFACT_LOW_CONFIDENCE" in scores.reason_codes
    assert "OOM alert fired" in verdict.one_liner
    assert "appears to have been OOMKilled" not in verdict.one_liner


def test_crashloop_artifact_is_marked_recovered_when_contradiction_present() -> None:
    """
    Crashloop contradiction (Ready=True + no restarts) should be treated as recovered/stale when it
    leads to artifact classification.
    """
    from agent.core.models import (
        AlertInstance,
        Evidence,
        Investigation,
        K8sEvidence,
        MetricsEvidence,
        TargetRef,
        TimeWindow,
    )
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "KubePodCrashLooping", "namespace": "ns1", "pod": "p1"},
            annotations={},
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
            starts_at=end.isoformat(),
        ),
        time_window=tw,
        target=TargetRef(target_type="pod", namespace="ns1", pod="p1", playbook="default"),
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "phase": "Running",
                    "container_statuses": [
                        {"name": "app", "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "backoff"}}},
                    ],
                },
                pod_conditions=[{"type": "Ready", "status": "True"}],
            ),
            metrics=MetricsEvidence(
                restart_data={
                    "restart_increase_5m": [
                        {"metric": {"container": "app"}, "values": [[0, "0"], [1, "0"]]},
                    ]
                }
            ),
        ),
    )

    f = compute_features(investigation)
    assert f.family == "crashloop"
    scores, verdict = score_investigation(investigation, f)
    assert verdict.classification == "artifact"
    assert "CRASHLOOP_CONTRADICTION_READY_NO_RESTARTS" in scores.reason_codes
    assert "ARTIFACT_RECOVERED" in scores.reason_codes
    assert verdict.one_liner.startswith("Recovered/stale signal:")


def test_target_down_contradiction_from_prom_baseline_reduces_confidence_and_changes_wording() -> None:
    from agent.core.models import AlertInstance, Analysis, Investigation, NoiseInsights, TargetRef, TimeWindow
    from agent.pipeline.features import compute_features
    from agent.pipeline.scoring import score_investigation

    end = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    start = end - timedelta(minutes=30)
    tw = TimeWindow(window="30m", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp",
            labels={"alertname": "TargetDown", "namespace": "ns1", "job": "j1"},
            annotations={},
            state="active",
            normalized_state="firing",
            ends_at_kind="expires_at",
            starts_at=end.isoformat(),
        ),
        time_window=tw,
        target=TargetRef(target_type="service", namespace="ns1", job="j1"),
        analysis=Analysis(noise=NoiseInsights(prometheus={"firing_instances": 10})),
        evidence={
            "metrics": {
                "prom_baseline": {
                    "up_job_down": [{"metric": {}, "value": [0, "0"]}],
                    "up_job_total": [{"metric": {}, "value": [0, "3"]}],
                }
            }
        },
    )

    f = compute_features(investigation)
    assert f.family == "target_down"
    scores, verdict = score_investigation(investigation, f)
    assert "TARGETDOWN_CONTRADICTION_UP_NONE" in scores.reason_codes
    assert verdict.classification in ("informational", "artifact")
    assert "label-derived up() checks suggest 0 targets down" in verdict.one_liner
