from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import agent.api.webhook as ws
from agent.core.dedup import compute_dedup_key, compute_rollout_workload_key
from agent.core.models import AlertInstance, Analysis, Investigation, TargetRef, TimeWindow


class _FakeStorage:
    def __init__(
        self, *, exists_keys: Optional[set[str]] = None, last_modified: Optional[dict[str, datetime]] = None
    ) -> None:
        self._exists_keys = exists_keys or set()
        self._last_modified = last_modified or {}
        self.markdown_put: List[tuple[str, str]] = []
        self.json_put: List[tuple[str, Any]] = []

    def exists(self, rel_key: str) -> bool:
        return rel_key in self._exists_keys

    def head_metadata(self, rel_key: str):  # type: ignore[no-untyped-def]
        if rel_key in self._exists_keys:
            return True, self._last_modified.get(rel_key)
        return False, None

    def put_markdown(self, rel_key: str, body: str) -> None:
        self.markdown_put.append((rel_key, body))
        self._exists_keys.add(rel_key)
        self._last_modified[rel_key] = datetime.now(timezone.utc)

    def put_json(self, rel_key: str, obj: Any) -> None:
        self.json_put.append((rel_key, obj))
        self._exists_keys.add(rel_key)
        self._last_modified[rel_key] = datetime.now(timezone.utc)

    def key(self, rel_key: str) -> str:
        return rel_key


def _install_fake_incident_index() -> None:
    """
    `process_alerts` imports `agent.memory.incident_index` inside the function.
    For unit tests we stub it out to avoid any Postgres/config side effects.
    """
    mod = types.ModuleType("agent.memory.incident_index")

    def index_incident_run(**_kwargs):  # type: ignore[no-untyped-def]
        return False, "skipped_in_unit_test", None

    mod.index_incident_run = index_incident_run  # type: ignore[attr-defined]
    sys.modules["agent.memory.incident_index"] = mod


def _fake_investigation_for_alert(*, alert: Dict[str, Any], time_window: str) -> Investigation:
    now = datetime.now(timezone.utc)
    return Investigation(
        alert=AlertInstance(
            fingerprint=str(alert.get("fingerprint") or "fp"),
            labels=dict(alert.get("labels") or {}),
            annotations=dict(alert.get("annotations") or {}),
            starts_at=alert.get("starts_at"),
            ends_at=alert.get("ends_at"),
            generator_url=alert.get("generator_url"),
            state=(alert.get("status") or {}).get("state"),
            normalized_state="firing",
            ends_at_kind="expires_at",
        ),
        time_window=TimeWindow(window=time_window, start_time=now, end_time=now),
        target=TargetRef(target_type="unknown"),
        analysis=Analysis(),
    )


def test_normalize_webhook_alert_status_resolution() -> None:
    firing = ws._normalize_webhook_alert({"labels": {"alertname": "A"}, "startsAt": "t"}, parent_status=None)
    assert (firing.get("status") or {}).get("state") == "firing"

    resolved = ws._normalize_webhook_alert(
        {"labels": {"alertname": "A"}, "startsAt": "t", "endsAt": "t2"},
        parent_status=None,
    )
    assert (resolved.get("status") or {}).get("state") == "resolved"

    # Per-alert endsAt wins over parent_status (Alertmanager top-level status can be "firing"
    # even when the payload contains resolved alerts).
    forced = ws._normalize_webhook_alert(
        {"labels": {"alertname": "A"}, "startsAt": "t", "endsAt": "t2"},
        parent_status="firing",
    )
    assert (forced.get("status") or {}).get("state") == "resolved"

    # Alertmanager can emit a "zero time" endsAt placeholder for firing alerts.
    placeholder = ws._normalize_webhook_alert(
        {"labels": {"alertname": "A"}, "startsAt": "t", "endsAt": "0001-01-01T00:00:00Z"},
        parent_status="firing",
    )
    assert (placeholder.get("status") or {}).get("state") == "firing"


def test_process_alerts_skips_when_report_already_exists(monkeypatch) -> None:
    _install_fake_incident_index()

    def _should_not_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("run_investigation must not run when S3 key exists")

    monkeypatch.setattr(ws, "run_investigation", _should_not_run)

    now = datetime(2026, 1, 2, 1, 0, 0, tzinfo=timezone.utc)
    labels = {"alertname": "TestAlert", "cluster": "c1", "namespace": "ns", "pod": "p1"}
    dedup = compute_dedup_key(alertname="TestAlert", labels=labels, fingerprint="fp-1", now=now)
    storage = _FakeStorage(exists_keys={f"TestAlert/{dedup}.md"})
    raw_alert = {"labels": labels, "startsAt": "t", "fingerprint": "fp-1"}

    stats, created = ws.process_alerts(
        [raw_alert],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=None,
        parent_status=None,
        now=now,
    )

    assert stats.received == 1
    assert stats.processed_firing == 1
    assert stats.stored_new == 0
    assert stats.skipped_already_exists == 1
    assert created == []
    assert storage.markdown_put == []


def test_process_alerts_allowlist_skips(monkeypatch) -> None:
    _install_fake_incident_index()
    monkeypatch.setattr(ws, "run_investigation", _fake_investigation_for_alert)
    monkeypatch.setattr(ws, "render_report", lambda _investigation, **_kw: "ok")

    storage = _FakeStorage()
    raw_alert = {"labels": {"alertname": "NotAllowed"}, "startsAt": "t", "fingerprint": "fp-1"}

    stats, created = ws.process_alerts(
        [raw_alert],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=["AllowedOnly"],
        parent_status=None,
    )

    assert stats.received == 1
    assert stats.processed_firing == 1
    assert stats.skipped_allowlist == 1
    assert stats.stored_new == 0
    assert created == []


def test_process_alerts_skips_resolved(monkeypatch) -> None:
    _install_fake_incident_index()

    def _should_not_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("run_investigation must not run for resolved alerts")

    monkeypatch.setattr(ws, "run_investigation", _should_not_run)

    storage = _FakeStorage()
    raw_alert = {"labels": {"alertname": "Resolved"}, "startsAt": "t", "endsAt": "t2", "fingerprint": "fp-1"}

    stats, created = ws.process_alerts(
        [raw_alert],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=None,
        parent_status=None,
    )

    assert stats.received == 1
    assert stats.processed_firing == 0
    assert stats.skipped_resolved == 1
    assert stats.stored_new == 0
    assert created == []


def test_process_alerts_dedupes_same_payload_by_fingerprint(monkeypatch) -> None:
    _install_fake_incident_index()

    calls = {"runs": 0}

    def _fake_run_investigation(*, alert: Dict[str, Any], time_window: str) -> Investigation:
        calls["runs"] += 1
        return _fake_investigation_for_alert(alert=alert, time_window=time_window)

    monkeypatch.setattr(ws, "run_investigation", _fake_run_investigation)
    monkeypatch.setattr(ws, "render_report", lambda _investigation, **_kw: "ok")

    now = datetime(2026, 1, 2, 1, 0, 0, tzinfo=timezone.utc)
    storage = _FakeStorage()
    labels = {"alertname": "CrashLoopBackOff", "cluster": "c1", "namespace": "ns", "pod": "p1"}
    raw1 = {"labels": labels, "startsAt": "t", "fingerprint": "fp-1"}
    raw2 = {"labels": labels, "startsAt": "t", "fingerprint": "fp-2"}

    stats, created = ws.process_alerts(
        [raw1, raw2],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=None,
        parent_status=None,
        now=now,
    )

    assert stats.received == 2
    assert stats.processed_firing == 2  # current behavior: counts firing before dedupe
    assert stats.stored_new == 1
    assert stats.skipped_already_exists == 1
    assert calls["runs"] == 1
    assert len(created) == 1


def test_rollout_gating_skips_fresh_report_pod_not_healthy(monkeypatch) -> None:
    _install_fake_incident_index()

    now = datetime(2026, 1, 2, 1, 0, 0, tzinfo=timezone.utc)

    # Fake K8s owner chain resolution to a stable workload identity.
    def _fake_owner_chain(pod_name: str, namespace: str, max_depth: int = 5):  # type: ignore[no-untyped-def]
        return {"workload": {"kind": "Deployment", "name": "room-management-api"}}

    monkeypatch.setattr("agent.providers.k8s_provider.get_pod_owner_chain", _fake_owner_chain)
    monkeypatch.setenv("CLUSTER_NAME", "c1")

    # Ensure we never run the investigation if the report is fresh.
    def _should_not_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("run_investigation must not run when freshness gate skips")

    monkeypatch.setattr(ws, "run_investigation", _should_not_run)

    labels = {
        "alertname": "KubernetesPodNotHealthy",
        "cluster": "c1",
        "namespace": "test",
        "pod": "room-management-api-7cf5d76f57-67gkr",
    }
    oc = {"workload": {"kind": "Deployment", "name": "room-management-api"}}
    wk = compute_rollout_workload_key(
        alertname="KubernetesPodNotHealthy", labels=labels, owner_chain=oc, env_cluster="c1", include_container=False
    )
    assert wk is not None
    rel_key = f"KubernetesPodNotHealthy/{wk}.md"

    storage = _FakeStorage(exists_keys={rel_key}, last_modified={rel_key: now - timedelta(minutes=10)})

    stats, created = ws.process_alerts(
        [{"labels": labels, "startsAt": "t", "fingerprint": "fp-1"}],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=None,
        parent_status=None,
        now=now,
    )

    assert stats.received == 1
    assert stats.processed_firing == 1
    assert stats.stored_new == 0
    assert stats.skipped_already_exists == 1
    assert created == []
    assert storage.markdown_put == []


def test_rollout_gating_refreshes_when_stale(monkeypatch) -> None:
    _install_fake_incident_index()
    monkeypatch.setenv("CLUSTER_NAME", "c1")

    now = datetime(2026, 1, 2, 1, 0, 0, tzinfo=timezone.utc)

    def _fake_owner_chain(pod_name: str, namespace: str, max_depth: int = 5):  # type: ignore[no-untyped-def]
        return {"workload": {"kind": "Deployment", "name": "room-management-api"}}

    monkeypatch.setattr("agent.providers.k8s_provider.get_pod_owner_chain", _fake_owner_chain)
    monkeypatch.setattr(ws, "run_investigation", _fake_investigation_for_alert)
    monkeypatch.setattr(ws, "render_report", lambda _investigation, **_kw: "ok")

    labels = {
        "alertname": "KubernetesPodNotHealthy",
        "cluster": "c1",
        "namespace": "test",
        "pod": "room-management-api-7cf5d76f57-67gkr",
    }
    oc = {"workload": {"kind": "Deployment", "name": "room-management-api"}}
    wk = compute_rollout_workload_key(
        alertname="KubernetesPodNotHealthy", labels=labels, owner_chain=oc, env_cluster="c1", include_container=False
    )
    assert wk is not None
    rel_key = f"KubernetesPodNotHealthy/{wk}.md"

    storage = _FakeStorage(exists_keys={rel_key}, last_modified={rel_key: now - timedelta(hours=2)})

    stats, created = ws.process_alerts(
        [{"labels": labels, "startsAt": "t", "fingerprint": "fp-1"}],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=None,
        parent_status=None,
        now=now,
    )

    assert stats.received == 1
    assert stats.processed_firing == 1
    assert stats.stored_new == 1
    assert len(created) == 1
    assert storage.markdown_put and storage.markdown_put[0][0] == rel_key


def test_rollout_workload_key_includes_container_for_oom_killer() -> None:
    labels_a = {
        "alertname": "KubernetesContainerOomKiller",
        "cluster": "c1",
        "namespace": "ns",
        "pod": "p1",
        "container": "app",
    }
    labels_b = {
        "alertname": "KubernetesContainerOomKiller",
        "cluster": "c1",
        "namespace": "ns",
        "pod": "p1",
        "container": "sidecar",
    }
    oc = {"workload": {"kind": "Deployment", "name": "room-management-api"}}
    k1 = compute_rollout_workload_key(
        alertname="KubernetesContainerOomKiller",
        labels=labels_a,
        owner_chain=oc,
        env_cluster="c1",
        include_container=True,
    )
    k2 = compute_rollout_workload_key(
        alertname="KubernetesContainerOomKiller",
        labels=labels_b,
        owner_chain=oc,
        env_cluster="c1",
        include_container=True,
    )
    assert k1 is not None and k2 is not None
    assert k1 != k2


def test_rollout_gating_dedupes_multiple_pods_same_workload_in_one_payload(monkeypatch) -> None:
    _install_fake_incident_index()
    monkeypatch.setenv("CLUSTER_NAME", "c1")

    calls = {"runs": 0}

    def _fake_owner_chain(pod_name: str, namespace: str, max_depth: int = 5):  # type: ignore[no-untyped-def]
        return {"workload": {"kind": "Deployment", "name": "room-management-api"}}

    def _fake_run(*, alert: Dict[str, Any], time_window: str) -> Investigation:
        calls["runs"] += 1
        return _fake_investigation_for_alert(alert=alert, time_window=time_window)

    monkeypatch.setattr("agent.providers.k8s_provider.get_pod_owner_chain", _fake_owner_chain)
    monkeypatch.setattr(ws, "run_investigation", _fake_run)
    monkeypatch.setattr(ws, "render_report", lambda _investigation, **_kw: "ok")

    now = datetime(2026, 1, 2, 1, 0, 0, tzinfo=timezone.utc)
    storage = _FakeStorage()
    raw1 = {
        "labels": {
            "alertname": "KubernetesPodNotHealthy",
            "cluster": "c1",
            "namespace": "test",
            "pod": "room-management-api-7cf5d76f57-aaa",
        },
        "startsAt": "t",
        "fingerprint": "fp-1",
    }
    raw2 = {
        "labels": {
            "alertname": "KubernetesPodNotHealthy",
            "cluster": "c1",
            "namespace": "test",
            "pod": "room-management-api-7cf5d76f57-bbb",
        },
        "startsAt": "t",
        "fingerprint": "fp-2",
    }

    stats, _created = ws.process_alerts(
        [raw1, raw2],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=None,
        parent_status=None,
        now=now,
    )
    assert stats.received == 2
    assert stats.processed_firing == 2
    assert stats.stored_new == 1
    assert stats.skipped_already_exists == 1
    assert calls["runs"] == 1
