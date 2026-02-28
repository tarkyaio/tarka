from datetime import datetime


def test_capacity_filters_over_under_lists(monkeypatch) -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.capacity import build_capacity_report_for_investigation

    end = datetime(2025, 1, 1, 0, 0, 0)
    tw = TimeWindow(window="1h", start_time=end, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(
            fingerprint="fp", labels={"alertname": "A", "namespace": "ns1", "pod": "p1"}, annotations={}
        ),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1"},
        evidence={"k8s": {"rollout_status": {"kind": "Deployment", "name": "demo"}}},
    )

    # Build vectors that produce one row where usage < request (negative delta)
    def fake_query(q: str, at):
        if "container_cpu_usage_seconds_total" in q:
            return [{"metric": {"pod": "demo-x", "container": "c"}, "value": [0, "0.1"]}]
        if "kube_pod_container_resource_requests" in q and 'resource="cpu"' in q:
            return [{"metric": {"pod": "demo-x", "container": "c"}, "value": [0, "0.5"]}]
        if "container_memory_working_set_bytes" in q:
            return [{"metric": {"pod": "demo-x", "container": "c"}, "value": [0, "100"]}]
        if "kube_pod_container_resource_requests" in q and 'resource="memory"' in q:
            return [{"metric": {"pod": "demo-x", "container": "c"}, "value": [0, "200"]}]
        return []

    monkeypatch.setattr("agent.pipeline.capacity.query_prometheus_instant", fake_query)
    out = build_capacity_report_for_investigation(investigation, end_time=end, top_n=10)
    assert out["status"] == "ok"
    assert out["top_cpu_over_request"] == []
    assert len(out["top_cpu_under_request"]) == 1
    assert out["top_mem_over_request"] == []
    assert len(out["top_mem_under_request"]) == 1
