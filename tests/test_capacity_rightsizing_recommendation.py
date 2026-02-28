from datetime import datetime


def test_capacity_emits_extreme_overrequested_cpu_recommendation(monkeypatch) -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.capacity import build_capacity_report_for_investigation

    start = datetime(2025, 1, 1, 0, 0, 0)
    end = datetime(2025, 1, 1, 1, 0, 0)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "A"}, annotations={}),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1"},
        evidence={"k8s": {"rollout_status": {"kind": "Deployment", "name": "demo"}}},
    )

    # Instant vectors (used for cheap gating + request value)
    def fake_query(q: str, at):
        if "container_cpu_usage_seconds_total" in q:
            return [{"metric": {"pod": "p1", "container": "c1"}, "value": [0, "0.0034"]}]
        if "kube_pod_container_resource_requests" in q and 'resource="cpu"' in q:
            return [{"metric": {"pod": "p1", "container": "c1"}, "value": [0, "0.3"]}]
        if "container_memory_working_set_bytes" in q:
            return []
        if "kube_pod_container_resource_requests" in q and 'resource="memory"' in q:
            return []
        return []

    monkeypatch.setattr("agent.pipeline.capacity.query_prometheus_instant", fake_query)

    # Range query (used to compute p95 usage over the window)
    def fake_cpu_usage_and_limits(pod_name: str, namespace: str, start_time, end_time, container=None):
        assert pod_name == "p1"
        assert namespace == "ns1"
        assert container == "c1"
        # constant series -> p95 is same
        return {
            "cpu_usage": [
                {
                    "metric": {"pod": "p1", "namespace": "ns1", "container": "c1"},
                    "values": [[0, "0.0034"], [1, "0.0034"], [2, "0.0034"]],
                }
            ],
            "cpu_limits": [],
            "cpu_requests": [],
        }

    monkeypatch.setattr("agent.providers.prom_provider.query_cpu_usage_and_limits", fake_cpu_usage_and_limits)

    out = build_capacity_report_for_investigation(investigation, end_time=end, top_n=10)
    assert out["status"] == "ok"
    assert out.get("recommendations"), "expected a deterministic recommendation"
    rec = out["recommendations"][0]
    assert "CPU request 300m" in rec
    assert "p95 usage ~3m" in rec or "p95 usage ~4m" in rec  # rounding tolerance
    assert "20m–50m" in rec
