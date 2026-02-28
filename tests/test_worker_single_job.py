from __future__ import annotations

from typing import Any, Dict, List

import agent.api.webhook as ws
import agent.api.worker as worker


class _FakeStorage:
    def __init__(self) -> None:
        self.exists_calls: List[str] = []

    def exists(self, rel_key: str) -> bool:
        self.exists_calls.append(rel_key)
        return False

    def put_markdown(self, _rel_key: str, _body: str) -> None:
        raise AssertionError("worker tests do not expect storage writes (process_alerts is stubbed)")

    def put_json(self, _rel_key: str, _obj: Any) -> None:
        raise AssertionError("worker tests do not expect storage writes (process_alerts is stubbed)")

    def key(self, rel_key: str) -> str:
        return rel_key


def test_load_job_parses_json() -> None:
    j = worker.load_job(
        {
            "alert": {"labels": {"alertname": "A"}, "startsAt": "t", "fingerprint": "fp"},
            "time_window": "15m",
            "parent_status": "firing",
        }
    )
    assert j.time_window == "15m"
    assert j.parent_status == "firing"
    assert (j.alert.get("labels") or {}).get("alertname") == "A"


def test_run_alert_job_passes_expected_args(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _fake_process_alerts(  # type: ignore[no-untyped-def]
        alerts,
        *,
        time_window,
        storage,
        allowlist,
        parent_status=None,
    ):
        captured["alerts"] = alerts
        captured["time_window"] = time_window
        captured["storage"] = storage
        captured["allowlist"] = allowlist
        captured["parent_status"] = parent_status

        class _Stats:
            received = 1
            processed_firing = 1
            stored_new = 0
            errors = 0

        return _Stats(), []

    monkeypatch.setattr(ws, "process_alerts", _fake_process_alerts)

    job = worker.load_job(
        {
            "alert": {"labels": {"alertname": "A"}, "startsAt": "t", "fingerprint": "fp"},
            "time_window": "15m",
            "parent_status": "firing",
        }
    )
    storage = _FakeStorage()
    stats, created = worker.run_alert_job(job, storage=storage, allowlist=["A"])

    assert getattr(stats, "received", None) == 1
    assert created == []
    assert captured["alerts"] == [job.alert]
    assert captured["time_window"] == "15m"
    assert captured["storage"] is storage
    assert captured["allowlist"] == ["A"]
    assert captured["parent_status"] == "firing"
