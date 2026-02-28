from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.core.dedup import (
    compute_dedup_key,
    compute_utc_bucket_start,
    detect_family_for_labels,
    format_bucket_label,
)


def _dt(y: int, m: int, d: int, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def test_detect_family_for_labels() -> None:
    assert detect_family_for_labels(labels={"alertname": "CrashLoopBackOff"}) == "crashloop"
    assert detect_family_for_labels(labels={}) == "generic"
    # Non-dict -> generic (defensive)
    assert detect_family_for_labels(labels="nope") == "generic"  # type: ignore[arg-type]


def test_bucket_start_rejects_non_positive_hours() -> None:
    with pytest.raises(ValueError):
        compute_utc_bucket_start(now=_dt(2026, 1, 2, 1), hours=0)
    with pytest.raises(ValueError):
        compute_utc_bucket_start(now=_dt(2026, 1, 2, 1), hours=-1)


def test_bucket_start_floors_to_4h_boundaries_utc() -> None:
    assert compute_utc_bucket_start(now=_dt(2026, 1, 2, 0, 0, 0), hours=4) == _dt(2026, 1, 2, 0, 0, 0)
    assert compute_utc_bucket_start(now=_dt(2026, 1, 2, 3, 59, 59), hours=4) == _dt(2026, 1, 2, 0, 0, 0)
    assert compute_utc_bucket_start(now=_dt(2026, 1, 2, 4, 0, 0), hours=4) == _dt(2026, 1, 2, 4, 0, 0)
    assert compute_utc_bucket_start(now=_dt(2026, 1, 2, 7, 59, 59), hours=4) == _dt(2026, 1, 2, 4, 0, 0)
    assert compute_utc_bucket_start(now=_dt(2026, 1, 2, 23, 59, 59), hours=4) == _dt(2026, 1, 2, 20, 0, 0)


def test_bucket_start_treats_naive_datetime_as_utc() -> None:
    naive = datetime(2026, 1, 2, 7, 59, 59)  # no tzinfo
    got = compute_utc_bucket_start(now=naive, hours=4)
    assert got.tzinfo is not None
    assert got == _dt(2026, 1, 2, 4, 0, 0)


def test_format_bucket_label_is_yyyymmddhh_utc() -> None:
    assert format_bucket_label(_dt(2026, 1, 2, 0, 0, 0)) == "2026010200"
    assert format_bucket_label(_dt(2026, 1, 2, 4, 0, 0)) == "2026010204"
    # naive treated as UTC
    assert format_bucket_label(datetime(2026, 1, 2, 4, 0, 0)) == "2026010204"


def test_dedup_key_pod_identity_ignores_fingerprint_and_label_churn() -> None:
    now = _dt(2026, 1, 2, 1, 0, 0)
    labels = {
        "alertname": "CrashLoop",
        "cluster": "c1",
        "namespace": "ns",
        "pod": "p1",
    }
    k1 = compute_dedup_key(alertname="CrashLoop", labels=labels, fingerprint="fp-a", now=now)
    # fingerprint changes but same identity
    k2 = compute_dedup_key(alertname="CrashLoop", labels=labels, fingerprint="fp-b", now=now)
    assert k1 == k2

    # label churn should not affect identity
    labels2 = dict(labels)
    labels2.update({"severity": "critical", "prometheus_replica": "r1", "endpoint": "http"})
    k3 = compute_dedup_key(alertname="CrashLoop", labels=labels2, fingerprint="fp-c", now=now)
    assert k1 == k3


def test_dedup_key_pod_identity_changes_if_pod_or_namespace_or_cluster_changes() -> None:
    now = _dt(2026, 1, 2, 1, 0, 0)
    base = {"alertname": "CrashLoop", "cluster": "c1", "namespace": "ns", "pod": "p1"}
    k_base = compute_dedup_key(alertname="CrashLoop", labels=base, fingerprint="fp", now=now)

    k_ns = compute_dedup_key(alertname="CrashLoop", labels={**base, "namespace": "ns2"}, fingerprint="fp", now=now)
    k_pod = compute_dedup_key(alertname="CrashLoop", labels={**base, "pod": "p2"}, fingerprint="fp", now=now)
    k_cluster = compute_dedup_key(alertname="CrashLoop", labels={**base, "cluster": "c2"}, fingerprint="fp", now=now)

    assert k_base != k_ns
    assert k_base != k_pod
    assert k_base != k_cluster


def test_dedup_key_excluded_families_do_not_use_pod_identity() -> None:
    now = _dt(2026, 1, 2, 1, 0, 0)
    labels = {
        "alertname": "TargetDown",
        "cluster": "c1",
        "namespace": "ns",
        "pod": "p1",
        "service": "svc1",
    }
    # family=target_down => uses service identity (fingerprint ignored)
    k1 = compute_dedup_key(alertname="TargetDown", labels=labels, fingerprint="fp-a", now=now)
    k2 = compute_dedup_key(alertname="TargetDown", labels=labels, fingerprint="fp-b", now=now)
    assert k1 == k2

    # if no service, excluded family falls back to fingerprint identity
    labels2 = dict(labels)
    labels2.pop("service")
    k3 = compute_dedup_key(alertname="TargetDown", labels=labels2, fingerprint="fp-a", now=now)
    k4 = compute_dedup_key(alertname="TargetDown", labels=labels2, fingerprint="fp-b", now=now)
    assert k3 != k4


def test_dedup_key_service_identity_uses_env_cluster_if_label_missing() -> None:
    now = _dt(2026, 1, 2, 1, 0, 0)
    labels = {"alertname": "ServiceAlert", "service": "svc1"}
    k1 = compute_dedup_key(alertname="ServiceAlert", labels=labels, fingerprint="fp-a", now=now, env_cluster="c-env")
    k2 = compute_dedup_key(alertname="ServiceAlert", labels=labels, fingerprint="fp-b", now=now, env_cluster="c-env")
    assert k1 == k2

    # Different cluster should produce different identity
    k3 = compute_dedup_key(alertname="ServiceAlert", labels=labels, fingerprint="fp-a", now=now, env_cluster="c2")
    assert k1 != k3


def test_dedup_key_fingerprint_fallback_changes_with_fingerprint() -> None:
    now = _dt(2026, 1, 2, 1, 0, 0)
    labels = {"alertname": "WeirdNonPodAlert"}
    k1 = compute_dedup_key(alertname="WeirdNonPodAlert", labels=labels, fingerprint="fp-a", now=now)
    k2 = compute_dedup_key(alertname="WeirdNonPodAlert", labels=labels, fingerprint="fp-b", now=now)
    assert k1 != k2


def test_dedup_key_changes_across_4h_buckets() -> None:
    labels = {"alertname": "CrashLoop", "cluster": "c1", "namespace": "ns", "pod": "p1"}
    # Same bucket (00:00-03:59)
    k1 = compute_dedup_key(alertname="CrashLoop", labels=labels, fingerprint="fp", now=_dt(2026, 1, 2, 0, 1, 0))
    k2 = compute_dedup_key(alertname="CrashLoop", labels=labels, fingerprint="fp", now=_dt(2026, 1, 2, 3, 59, 59))
    assert k1 == k2
    # Next bucket (04:00-07:59)
    k3 = compute_dedup_key(alertname="CrashLoop", labels=labels, fingerprint="fp", now=_dt(2026, 1, 2, 4, 0, 0))
    assert k1 != k3
