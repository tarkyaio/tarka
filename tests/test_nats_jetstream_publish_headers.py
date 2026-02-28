from __future__ import annotations

import asyncio
import types

from agent.queue.base import AlertJob
from agent.queue.nats_jetstream import JetStreamQueueClient


def test_jetstream_enqueue_uses_headers_for_msg_id(monkeypatch) -> None:
    """
    Regression test:
    nats-py versions differ; our code must not pass `msg_id=` kwarg to publish().
    """

    class _FakeJS:
        def __init__(self) -> None:
            self.calls = []

        async def publish(self, subject, data, headers=None):  # type: ignore[no-untyped-def]
            self.calls.append({"subject": subject, "data": data, "headers": headers})
            return types.SimpleNamespace(seq=123)

    c = JetStreamQueueClient(nats_url="nats://x", stream="TARKA", subject="tarka.alerts")

    async def _fake_ensure_connected():  # type: ignore[no-untyped-def]
        c._js = _FakeJS()  # type: ignore[attr-defined]

    monkeypatch.setattr(c, "_ensure_connected", _fake_ensure_connected)

    job = AlertJob(alert={"labels": {"alertname": "A"}, "startsAt": "t", "fingerprint": "fp"}, time_window="15m")
    seq = asyncio.run(c.enqueue(job, msg_id="abc"))
    assert seq == "123"
    js = c._js  # type: ignore[assignment]
    assert js.calls
    assert js.calls[0]["headers"] is not None
