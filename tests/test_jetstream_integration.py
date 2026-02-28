from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from agent.core.dedup import compute_dedup_key
from agent.queue.base import AlertJob
from agent.queue.nats_jetstream import JetStreamQueueClient, compute_msg_id_from_dedup_key

pytestmark = pytest.mark.integration
pytest.importorskip("nats", reason="nats-py is required for JetStream integration tests")


async def _run() -> None:
    # CI provides a local NATS server; locally you can set NATS_URL yourself.
    nats_url = (os.getenv("NATS_URL") or "").strip() or "nats://127.0.0.1:4222"
    stream = f"TEST_{uuid.uuid4().hex[:8]}"
    subject = f"{stream.lower()}.alerts"
    durable = "TEST_CONSUMER"

    # If NATS isn't reachable (common locally), skip instead of failing.
    import nats  # type: ignore[import-not-found]

    try:
        nc0 = await asyncio.wait_for(nats.connect(servers=[nats_url]), timeout=1.5)
        await nc0.drain()
    except Exception:
        pytest.skip(f"NATS is not reachable at {nats_url}. Start NATS/JetStream or set NATS_URL.")

    # Publish using our queue client (this exercises msg-id header usage too).
    qc = JetStreamQueueClient(nats_url=nats_url, stream=stream, subject=subject)
    job = AlertJob(
        alert={"labels": {"alertname": "IntegrationTest"}, "startsAt": "t", "fingerprint": "fp-int"},
        time_window="15m",
        parent_status="firing",
    )
    dedup = compute_dedup_key(
        alertname="IntegrationTest",
        labels={"alertname": "IntegrationTest"},
        fingerprint="fp-int",
        # fixed time keeps the test deterministic if we ever assert on msg_id output
        now=datetime(2026, 1, 2, 1, 0, 0, tzinfo=timezone.utc),
    )
    msg_id = compute_msg_id_from_dedup_key(dedup)
    await qc.enqueue(job, msg_id=msg_id)

    # Pull and ack via a durable consumer.
    nc = await nats.connect(servers=[nats_url])
    js = nc.jetstream()
    sub = await js.pull_subscribe(subject, durable=durable, stream=stream)
    msgs = await sub.fetch(1, timeout=2)
    assert len(msgs) == 1
    got = msgs[0].data.decode("utf-8")
    assert "IntegrationTest" in got
    await msgs[0].ack()
    await nc.drain()


def test_jetstream_publish_and_consume() -> None:
    asyncio.run(_run())
