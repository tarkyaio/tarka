from agent.core.models import AlertInstance, Evidence, Investigation, TargetRef, TimeWindow
from agent.core.targets import extract_target_container
from agent.pipeline.signals import _extract_container_from_investigation


def test_extract_target_container_ignores_kube_state_metrics_scrape_container() -> None:
    labels = {
        "alertname": "KubernetesPodNotHealthy",
        "namespace": "test",
        "pod": "room-management-api-7cf5d76f57-x5kv9",
        "job": "kube-state-metrics",
        "container": "kube-state-metrics",
    }
    assert extract_target_container(labels) is None


def test_signals_container_fallback_uses_target_heuristics() -> None:
    labels = {
        "alertname": "KubernetesPodNotHealthy",
        "namespace": "test",
        "pod": "room-management-api-7cf5d76f57-x5kv9",
        "job": "kube-state-metrics",
        "container": "kube-state-metrics",
    }
    investigation = Investigation(
        alert=AlertInstance(fingerprint="fp", labels=labels, annotations={}),
        time_window=TimeWindow(window="1h", start_time="2025-01-01T00:00:00Z", end_time="2025-01-01T01:00:00Z"),
        target=TargetRef(
            target_type="pod", namespace="test", pod="room-management-api-7cf5d76f57-x5kv9", container=None
        ),
        evidence=Evidence(),
        errors=[],
        meta={},
    )
    assert _extract_container_from_investigation(investigation) is None
