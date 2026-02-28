from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from agent.providers.logs_provider import fetch_recent_logs


class _Resp:
    def __init__(self, *, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status={self.status_code}")

    def json(self):
        raise ValueError("not json")  # NDJSON path should be used


def test_victorialogs_ndjson_is_parsed(monkeypatch) -> None:
    # VictoriaLogs returning NDJSON lines.
    # Force VictoriaLogs backend to avoid LOGS_URL environment pollution
    monkeypatch.setenv("LOGS_BACKEND", "victorialogs")

    def _fake_get(url, params=None, timeout=None):
        if "/select/logsql/query" in url:
            assert params is not None
            # We time-bound via absolute start/end params now.
            assert "start" in params and "end" in params
            # Default query should use namespace/pod fields.
            assert 'namespace:"ns"' in str(params.get("query"))
            assert 'pod:"p"' in str(params.get("query"))
            ndjson = (
                '{"_time":"2025-12-15T19:09:53.313177669Z","_msg":"m1","pod":"p","namespace":"ns","container":"c"}\n'
                '{"_time":"2025-12-15T19:09:54.313177669Z","_msg":"m2","pod":"p","namespace":"ns","container":"c"}\n'
            )
            return _Resp(text=ndjson, status_code=200)
        raise AssertionError(f"unexpected url={url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    start = datetime(2025, 12, 15, 19, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    out = fetch_recent_logs("p", "ns", start, end, limit=100, container="c")

    assert out["status"] == "ok"
    assert out["backend"] == "victorialogs"
    assert out["entries"][0]["message"] == "m1"
    assert out["entries"][1]["message"] == "m2"


def test_victorialogs_keeps_tail_of_window(monkeypatch) -> None:
    """
    If the backend returns entries in chronological order, we must keep the newest `limit`,
    not the first `limit` lines we parsed.
    """
    monkeypatch.setenv("LOGS_BACKEND", "victorialogs")

    def _fake_get(url, params=None, timeout=None):
        if "/select/logsql/query" in url:
            assert params is not None
            # 5 ordered entries
            ndjson = ""
            for i in range(1, 6):
                ndjson += f'{{"_time":"2025-12-15T19:09:0{i}.000000000Z","_msg":"m{i}","pod":"p","namespace":"ns"}}\n'
            return _Resp(text=ndjson, status_code=200)
        raise AssertionError(f"unexpected url={url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    start = datetime(2025, 12, 15, 19, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    out = fetch_recent_logs("p", "ns", start, end, limit=2)

    assert out["status"] == "ok"
    # Must keep the newest two messages: m4, m5
    assert [e["message"] for e in out["entries"]] == ["m4", "m5"]


def test_victorialogs_message_field_fallback(monkeypatch) -> None:
    """
    Some VictoriaLogs setups don't populate `_msg`; ensure we fall back to other common fields.
    """
    monkeypatch.setenv("LOGS_BACKEND", "victorialogs")

    def _fake_get(url, params=None, timeout=None):
        if "/select/logsql/query" in url:
            ndjson = (
                '{"_time":"2025-12-15T19:09:53.313177669Z","msg":"hello","pod":"p","namespace":"ns"}\n'
                '{"_time":"2025-12-15T19:09:54.313177669Z","log":"world","pod":"p","namespace":"ns"}\n'
            )
            return _Resp(text=ndjson, status_code=200)
        raise AssertionError(f"unexpected url={url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    start = datetime(2025, 12, 15, 19, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    out = fetch_recent_logs("p", "ns", start, end, limit=10)

    assert out["status"] == "ok"
    assert [e["message"] for e in out["entries"]] == ["hello", "world"]


def test_victorialogs_connection_reset_is_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("LOGS_BACKEND", "victorialogs")

    def _fake_get(url, params=None, timeout=None):
        if "/select/logsql/query" in url:
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        raise AssertionError(f"unexpected url={url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    start = datetime(2025, 12, 15, 19, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    out = fetch_recent_logs("p", "ns", start, end, limit=10)

    assert out["status"] == "unavailable"
    assert out["backend"] == "victorialogs"
    assert out["reason"] == "connection_error"
