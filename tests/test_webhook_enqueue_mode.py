from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

import agent.api.webhook as ws


class _FakeQueueClient:
    def __init__(self) -> None:
        self.msg_ids: List[str] = []
        self.jobs: List[Dict[str, Any]] = []

    async def warmup(self) -> None:
        return None

    async def enqueue(self, job, *, msg_id: Optional[str] = None) -> str:  # type: ignore[no-untyped-def]
        self.msg_ids.append(msg_id or "")
        self.jobs.append(job.model_dump(mode="json"))
        return "1"


def test_enqueue_mode_does_not_require_s3_bucket(monkeypatch) -> None:
    # Receiver is enqueue-only; it must not require S3_BUCKET.
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.delenv("ALERTNAME_ALLOWLIST", raising=False)  # Clear allowlist to allow test alerts
    monkeypatch.setenv("TIME_WINDOW", "15m")

    fake = _FakeQueueClient()

    async def _fake_get_client_from_env():  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr("agent.queue.nats_jetstream.get_client_from_env", _fake_get_client_from_env)

    client = TestClient(ws.app)
    payload = {
        "status": "firing",
        "alerts": [
            {"labels": {"alertname": "A"}, "startsAt": "t", "fingerprint": "fp-1"},
        ],
    }
    r = client.post("/alerts", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert body["mode"] == "enqueue"
    assert body["received"] == 1
    assert body["enqueued"] == 1
    assert len(fake.jobs) == 1
    assert fake.jobs[0]["time_window"] == "15m"


def test_enqueue_mode_filters_resolved_and_dedupes(monkeypatch) -> None:
    monkeypatch.delenv("ALERTNAME_ALLOWLIST", raising=False)  # Clear allowlist to allow test alerts
    monkeypatch.setenv("TIME_WINDOW", "15m")
    monkeypatch.setenv("CLUSTER_NAME", "c1")

    fake = _FakeQueueClient()

    async def _fake_get_client_from_env():  # type: ignore[no-untyped-def]
        return fake

    monkeypatch.setattr("agent.queue.nats_jetstream.get_client_from_env", _fake_get_client_from_env)

    # Freeze time to a fixed UTC hour so hour-bucketed msg-ids are stable.
    from datetime import datetime, timezone

    monkeypatch.setattr(ws, "utcnow", lambda: datetime(2026, 1, 4, 12, 10, 0, tzinfo=timezone.utc))

    # Same workload for any pod in this test
    def _fake_owner_chain(pod_name: str, namespace: str, max_depth: int = 5):  # type: ignore[no-untyped-def]
        return {"workload": {"kind": "Deployment", "name": "room-management-api"}}

    monkeypatch.setattr("agent.providers.k8s_provider.get_pod_owner_chain", _fake_owner_chain)

    client = TestClient(ws.app)
    payload = {
        "status": "firing",
        "alerts": [
            {
                "labels": {"alertname": "CrashLoopBackOff", "cluster": "c1", "namespace": "ns", "pod": "p1"},
                "startsAt": "t",
                "fingerprint": "fp-1",
            },
            {
                "labels": {"alertname": "CrashLoopBackOff", "cluster": "c1", "namespace": "ns", "pod": "p1"},
                "startsAt": "t",
                "fingerprint": "fp-2",  # same identity, different fingerprint
            },
            # Rollout-noisy: same workload, different pods/fingerprints => single enqueue per hour
            {
                "labels": {
                    "alertname": "KubernetesPodNotHealthy",
                    "cluster": "c1",
                    "namespace": "test",
                    "pod": "room-management-api-aaa",
                },
                "startsAt": "t",
                "fingerprint": "fp-a",
            },
            {
                "labels": {
                    "alertname": "KubernetesPodNotHealthy",
                    "cluster": "c1",
                    "namespace": "test",
                    "pod": "room-management-api-bbb",
                },
                "startsAt": "t",
                "fingerprint": "fp-b",
            },
            # Critical variant: dedup separately by alertname (still workload+hour)
            {
                "labels": {
                    "alertname": "KubernetesPodNotHealthyCritical",
                    "cluster": "c1",
                    "namespace": "test",
                    "pod": "room-management-api-ccc",
                },
                "startsAt": "t",
                "fingerprint": "fp-c",
            },
            {
                "labels": {
                    "alertname": "KubernetesPodNotHealthyCritical",
                    "cluster": "c1",
                    "namespace": "test",
                    "pod": "room-management-api-ddd",
                },
                "startsAt": "t",
                "fingerprint": "fp-d",
            },
            # OOM: include container in identity, so two containers => two enqueues
            {
                "labels": {
                    "alertname": "KubernetesContainerOomKiller",
                    "cluster": "c1",
                    "namespace": "test",
                    "pod": "room-management-api-eee",
                    "container": "app",
                },
                "startsAt": "t",
                "fingerprint": "fp-e",
            },
            {
                "labels": {
                    "alertname": "KubernetesContainerOomKiller",
                    "cluster": "c1",
                    "namespace": "test",
                    "pod": "room-management-api-fff",
                    "container": "sidecar",
                },
                "startsAt": "t",
                "fingerprint": "fp-f",
            },
            {"labels": {"alertname": "B"}, "startsAt": "t", "endsAt": "t2", "fingerprint": "fp-2"},  # resolved
        ],
    }
    r = client.post("/alerts", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert body["received"] == 9
    # Enqueued:
    # - CrashLoopBackOff: pod identity is stable; fingerprint churn does not create new work => 1
    # - PodNotHealthy: 2 alerts => 1 (workload+hour)
    # - PodNotHealthyCritical: 2 alerts => 1 (workload+hour)
    # - OOMKiller: 2 alerts diff containers => 2
    # Total = 5
    assert body["enqueued"] == 5
    # Deduped within payload:
    # - CrashLoopBackOff one duplicate
    # - PodNotHealthy one duplicate
    # - PodNotHealthyCritical one duplicate
    assert body["skipped_duplicate"] == 3
    assert body["skipped_resolved"] == 1
    assert len(fake.jobs) == 5


def test_webhook_startup_fails_if_jetstream_warmup_fails(monkeypatch) -> None:
    class _BadClient:
        async def warmup(self) -> None:
            raise RuntimeError("jetstream_unreachable")

        async def enqueue(self, _job, *, msg_id=None):  # type: ignore[no-untyped-def]
            return "0"

    async def _fake_get_client_from_env():  # type: ignore[no-untyped-def]
        return _BadClient()

    monkeypatch.setattr("agent.queue.nats_jetstream.get_client_from_env", _fake_get_client_from_env)

    with pytest.raises(Exception) as e:
        with TestClient(ws.app) as c:
            c.get("/healthz")
    assert "jetstream_unreachable" in str(e.value)
