from datetime import datetime, timedelta


def test_fetch_recent_logs_not_configured_in_cluster(monkeypatch) -> None:
    from agent.providers.logs_provider import fetch_recent_logs

    # Simulate in-cluster environment
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.delenv("LOGS_URL", raising=False)

    end = datetime.now().astimezone()
    start = end - timedelta(minutes=5)
    res = fetch_recent_logs("p", "ns", start, end, limit=10)
    assert res["status"] == "unavailable"
    assert res["reason"] == "not_configured"


def test_fetch_recent_logs_uses_default_local_url_when_env_missing(monkeypatch) -> None:
    """
    Local dev: if LOGS_URL is not set and we're not in-cluster, we should attempt
    the built-in localhost VictoriaLogs default.
    """
    import requests

    from agent.providers.logs_provider import fetch_recent_logs

    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("LOGS_URL", raising=False)

    called = {"get_urls": [], "post_urls": []}

    def fake_get(url, *args, **kwargs):
        called["get_urls"].append(url)
        raise requests.exceptions.RequestException("no server")

    monkeypatch.setattr(requests, "get", fake_get)

    end = datetime.now().astimezone()
    start = end - timedelta(minutes=5)
    res = fetch_recent_logs("p", "ns", start, end, limit=10)

    # Should have attempted the VictoriaLogs LogSQL endpoint on the default localhost base.
    assert any("/select/logsql/query" in u for u in called["get_urls"])
    assert res["status"] == "unavailable"
    assert res["reason"] in ("connection_error", "http_error", "timeout", "unexpected_error")
