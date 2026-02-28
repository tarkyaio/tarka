"""
Tests for logs backend auto-detection and dual Loki/VictoriaLogs support.

Verifies that:
- Backend is auto-detected from LOGS_URL
- Loki queries use LogQL syntax
- VictoriaLogs queries use LogsQL syntax
- Both backends parse responses correctly
- Manual override with LOGS_BACKEND works
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


def test_detect_loki_from_url():
    """Should detect Loki when URL contains 'loki'."""
    from agent.providers.logs_provider import _detect_backend

    assert _detect_backend("http://loki-distributed:3100") == "loki"
    assert _detect_backend("http://loki-gateway.observability:3100") == "loki"
    assert _detect_backend("http://loki:3100") == "loki"


def test_detect_victorialogs_from_url():
    """Should detect VictoriaLogs when URL doesn't contain 'loki'."""
    from agent.providers.logs_provider import _detect_backend

    assert _detect_backend("http://victorialogs:9428") == "victorialogs"
    assert _detect_backend("http://localhost:19471") == "victorialogs"
    assert _detect_backend("http://logs.example.com") == "victorialogs"


def test_backend_override_loki():
    """LOGS_BACKEND env var should override auto-detection."""
    from agent.providers.logs_provider import _detect_backend

    with patch.dict(os.environ, {"LOGS_BACKEND": "loki"}):
        # Should return loki even for non-loki URL
        assert _detect_backend("http://localhost:9428") == "loki"


def test_backend_override_victorialogs():
    """LOGS_BACKEND env var should override auto-detection."""
    from agent.providers.logs_provider import _detect_backend

    with patch.dict(os.environ, {"LOGS_BACKEND": "victorialogs"}):
        # Should return victorialogs even for loki URL
        assert _detect_backend("http://loki:3100") == "victorialogs"


def test_loki_query_syntax():
    """Loki should use LogQL syntax: {namespace="...", pod="..."}"""
    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch("agent.providers.logs_provider.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "result": [
                    {
                        "stream": {"namespace": "default", "pod": "test-pod"},
                        "values": [
                            [str(int(start.timestamp() * 1e9)), "log line 1"],
                            [str(int(end.timestamp() * 1e9)), "log line 2"],
                        ],
                    }
                ]
            }
        }
        mock_get.return_value = mock_response

        result = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="test-pod",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container=None,
            timeout_s=10.0,
        )

        # Verify Loki endpoint was called
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "/loki/api/v1/query_range" in call_args[0][0]

        # Verify LogQL syntax
        params = call_args[1]["params"]
        assert params["query"] == '{namespace="default", pod="test-pod"}'
        assert "start" in params
        assert "end" in params

        # Verify result
        assert result["status"] == "ok"
        assert result["backend"] == "loki"
        assert len(result["entries"]) == 2


def test_loki_query_with_container():
    """Loki should include container in LogQL when provided."""
    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch("agent.providers.logs_provider.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "result": [
                    {
                        "stream": {"namespace": "default", "pod": "test-pod", "container": "app"},
                        "values": [[str(int(start.timestamp() * 1e9)), "log line"]],
                    }
                ]
            }
        }
        mock_get.return_value = mock_response

        _ = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="test-pod",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container="app",
            timeout_s=10.0,
        )

        # Verify container is in query
        params = mock_get.call_args[1]["params"]
        assert 'container="app"' in params["query"]


def test_victorialogs_query_syntax():
    """VictoriaLogs should use LogsQL syntax: namespace:"..." AND pod:"..."""
    from agent.providers.logs_provider import _fetch_from_victorialogs

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch("agent.providers.logs_provider.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = (
            '{"_time":"2024-01-01T00:00:00Z","_msg":"log line 1","namespace":"default","pod":"test-pod"}\n'
            '{"_time":"2024-01-01T00:01:00Z","_msg":"log line 2","namespace":"default","pod":"test-pod"}\n'
        )
        mock_get.return_value = mock_response

        result = _fetch_from_victorialogs(
            logs_url="http://victorialogs:9428",
            pod_name="test-pod",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container=None,
            timeout_s=10.0,
        )

        # Verify VictoriaLogs endpoint was called
        mock_get.assert_called()
        call_args = mock_get.call_args
        assert "/select/logsql/query" in call_args[0][0]

        # Verify LogsQL syntax
        params = call_args[1]["params"]
        assert params["query"] == 'namespace:"default" AND pod:"test-pod"'

        # Verify result
        assert result["status"] == "ok"
        assert result["backend"] == "victorialogs"
        assert len(result["entries"]) == 2


def test_fetch_recent_logs_routes_to_loki():
    """fetch_recent_logs should route to Loki for loki URLs."""
    from agent.providers.logs_provider import fetch_recent_logs

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch.dict(os.environ, {"LOGS_URL": "http://loki:3100"}):
        with patch("agent.providers.logs_provider._fetch_from_loki") as mock_loki:
            mock_loki.return_value = {
                "entries": [],
                "status": "ok",
                "reason": "ok",
                "backend": "loki",
                "query_used": '{namespace="default"}',
            }

            result = fetch_recent_logs(
                pod_name="test-pod",
                namespace="default",
                start_time=start,
                end_time=end,
                limit=100,
            )

            mock_loki.assert_called_once()
            assert result["backend"] == "loki"


def test_fetch_recent_logs_routes_to_victorialogs():
    """fetch_recent_logs should route to VictoriaLogs for non-loki URLs."""
    from agent.providers.logs_provider import fetch_recent_logs

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch.dict(os.environ, {"LOGS_URL": "http://victorialogs:9428"}):
        with patch("agent.providers.logs_provider._fetch_from_victorialogs") as mock_vl:
            mock_vl.return_value = {
                "entries": [],
                "status": "ok",
                "reason": "ok",
                "backend": "victorialogs",
                "query_used": 'namespace:"default"',
            }

            result = fetch_recent_logs(
                pod_name="test-pod",
                namespace="default",
                start_time=start,
                end_time=end,
                limit=100,
            )

            mock_vl.assert_called_once()
            assert result["backend"] == "victorialogs"


def test_loki_http_error_handling():
    """Loki should return unavailable status on HTTP errors."""
    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch("agent.providers.logs_provider.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 500")
        mock_get.return_value = mock_response

        result = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="test-pod",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container=None,
            timeout_s=10.0,
        )

        assert result["status"] == "unavailable"
        assert result["backend"] == "loki"


def test_loki_timeout_handling():
    """Loki should return unavailable status on timeout."""
    import requests

    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch("agent.providers.logs_provider.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.Timeout("Timeout")

        result = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="test-pod",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container=None,
            timeout_s=1.0,
        )

        assert result["status"] == "unavailable"
        assert result["reason"] == "timeout"


def test_loki_empty_results():
    """Loki should return empty status when no logs found."""
    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    with patch("agent.providers.logs_provider.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"result": []}}
        mock_get.return_value = mock_response

        result = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="test-pod",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container=None,
            timeout_s=10.0,
        )

        assert result["status"] == "empty"
        assert result["backend"] == "loki"
        assert len(result["entries"]) == 0


def test_loki_fallback_to_k8s_labels():
    """Loki should try k8s_namespace and k8s_pod as fallback when standard labels return empty."""
    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    # Track which queries were attempted
    attempted_queries = []

    def mock_get_side_effect(url, **kwargs):
        query = kwargs["params"]["query"]
        attempted_queries.append(query)

        mock_response = MagicMock()
        mock_response.status_code = 200

        # k8s_ prefixed labels succeed
        if 'k8s_namespace="default"' in query and 'k8s_pod="mysql"' in query:
            mock_response.json.return_value = {
                "data": {
                    "result": [
                        {
                            "stream": {"k8s_namespace": "default", "k8s_pod": "mysql"},
                            "values": [[str(int(start.timestamp() * 1e9)), "mysql log entry"]],
                        }
                    ]
                }
            }
        # All other attempts return empty (including standard namespace/pod)
        else:
            mock_response.json.return_value = {"data": {"result": []}}

        return mock_response

    with patch("agent.providers.logs_provider.requests.get", side_effect=mock_get_side_effect):
        result = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="mysql",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container=None,
            timeout_s=10.0,
        )

        # Verify fallback was tried
        assert len(attempted_queries) >= 2, "Should have tried multiple label combinations"
        assert any('namespace="default"' in q for q in attempted_queries), "Should try standard labels first"
        assert any('k8s_namespace="default"' in q for q in attempted_queries), "Should try k8s_ fallback"

        # Verify successful result from fallback
        assert result["status"] == "ok"
        assert result["backend"] == "loki"
        assert len(result["entries"]) == 1
        assert result["entries"][0]["message"] == "mysql log entry"
        assert 'k8s_namespace="default"' in result["query_used"]


def test_loki_fallback_to_pod_name_label():
    """Loki should try pod_name label as second fallback."""
    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    attempted_queries = []

    def mock_get_side_effect(url, **kwargs):
        query = kwargs["params"]["query"]
        attempted_queries.append(query)

        mock_response = MagicMock()
        mock_response.status_code = 200

        # pod_name label succeeds
        if 'pod_name="mysql"' in query:
            mock_response.json.return_value = {
                "data": {
                    "result": [
                        {
                            "stream": {"namespace": "default", "pod_name": "mysql"},
                            "values": [[str(int(start.timestamp() * 1e9)), "mysql log from pod_name"]],
                        }
                    ]
                }
            }
        # All other attempts return empty (standard and k8s_ labels)
        else:
            mock_response.json.return_value = {"data": {"result": []}}

        return mock_response

    with patch("agent.providers.logs_provider.requests.get", side_effect=mock_get_side_effect):
        result = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="mysql",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container=None,
            timeout_s=10.0,
        )

        # Verify all fallbacks were tried
        assert len(attempted_queries) >= 3, "Should have tried multiple fallback combinations"
        assert any('pod_name="mysql"' in q for q in attempted_queries), "Should try pod_name fallback"

        # Verify successful result from pod_name fallback
        assert result["status"] == "ok"
        assert result["backend"] == "loki"
        assert len(result["entries"]) == 1
        assert 'pod_name="mysql"' in result["query_used"]


def test_loki_fallback_with_container():
    """Loki fallback should work with container parameter."""
    from agent.providers.logs_provider import _fetch_from_loki

    start = datetime.now() - timedelta(hours=1)
    end = datetime.now()

    attempted_queries = []

    def mock_get_side_effect(url, **kwargs):
        query = kwargs["params"]["query"]
        attempted_queries.append(query)

        mock_response = MagicMock()
        mock_response.status_code = 200

        # k8s_ labels with container succeed
        if 'k8s_namespace="default"' in query and 'k8s_pod="mysql"' in query and 'container="mysql"' in query:
            mock_response.json.return_value = {
                "data": {
                    "result": [
                        {
                            "stream": {"k8s_namespace": "default", "k8s_pod": "mysql", "container": "mysql"},
                            "values": [[str(int(start.timestamp() * 1e9)), "container log"]],
                        }
                    ]
                }
            }
        else:
            mock_response.json.return_value = {"data": {"result": []}}

        return mock_response

    with patch("agent.providers.logs_provider.requests.get", side_effect=mock_get_side_effect):
        result = _fetch_from_loki(
            logs_url="http://loki:3100",
            pod_name="mysql",
            namespace="default",
            start_time=start,
            end_time=end,
            limit=100,
            container="mysql",
            timeout_s=10.0,
        )

        # Verify container is included in fallback attempts
        assert any(
            'container="mysql"' in q and 'k8s_namespace="default"' in q for q in attempted_queries
        ), "Should try k8s_ labels with container"

        # Verify successful result
        assert result["status"] == "ok"
        assert 'container="mysql"' in result["query_used"]
