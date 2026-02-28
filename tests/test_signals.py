from datetime import datetime


def test_enrich_investigation_with_signal_queries_populates_http_5xx_without_pod(monkeypatch) -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.signals import enrich_investigation_with_signal_queries

    now = datetime(2025, 1, 1, 0, 0, 0)
    tw = TimeWindow(window="1h", start_time=now, end_time=now)

    investigation = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "Http5xxRateHigh"}, annotations={}),
        time_window=tw,
        # No pod target: signals should still attempt http_5xx derived from labels.
        target={"target_type": "service", "namespace": "ns1", "service": "api", "playbook": "default"},
    )

    monkeypatch.setattr(
        "agent.providers.prom_provider.query_http_5xx_generic",
        lambda *a, **k: {"query_used": "q", "series": [{"metric": {}, "values": [[0, "1"]]}]},
    )

    enrich_investigation_with_signal_queries(investigation)

    assert investigation.evidence.metrics.http_5xx is not None
    assert (investigation.evidence.metrics.http_5xx or {}).get("query_used") == "q"
