from __future__ import annotations


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_nats_jetstream_is_statefulset_with_pvc() -> None:
    txt = _read("k8s/nats-jetstream.yaml")
    assert "kind: StatefulSet" in txt
    assert "volumeClaimTemplates" in txt
    assert "storage:" in txt


def test_worker_has_dlq_env_defaults() -> None:
    txt = _read("k8s/worker-deployment.yaml")
    assert "JETSTREAM_DLQ_SUBJECT" in txt
    assert "JETSTREAM_DLQ_STREAM" in txt
    assert "JETSTREAM_MAX_DELIVER" in txt
    assert "JETSTREAM_ACK_WAIT_SECONDS" in txt


def test_configmap_has_queue_knobs() -> None:
    txt = _read("k8s/configmap.yaml")
    assert "NATS_URL" in txt
    assert "JETSTREAM_STREAM" in txt
    assert "JETSTREAM_SUBJECT" in txt
    assert "JETSTREAM_DLQ_STREAM" in txt
    assert "JETSTREAM_DLQ_SUBJECT" in txt
