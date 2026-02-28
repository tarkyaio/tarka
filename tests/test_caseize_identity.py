from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agent.memory.caseize import CaseizeInput, caseize_run


class _FakeResult:
    def __init__(self, row: Optional[Tuple[Any, ...]]) -> None:
        self._row = row

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self._row


class _FakeConn:
    """
    Minimal fake DB connection for caseize_run.

    We simulate only the SQL statements used by the workload-identity path and the fingerprint path.
    """

    def __init__(self) -> None:
        self.sql: List[Tuple[str, Tuple[Any, ...]]] = []
        self._cases_by_key: Dict[str, str] = {}
        self._cases_by_fp: Dict[str, str] = {}
        self._next_id = 1

    def _new_id(self) -> str:
        cid = f"case-{self._next_id}"
        self._next_id += 1
        return cid

    def execute(self, sql: str, params: Tuple[Any, ...] = ()):  # type: ignore[no-untyped-def]
        self.sql.append((sql.strip(), tuple(params)))

        # Fingerprint lookup
        if "FROM investigation_runs WHERE alert_fingerprint" in sql:
            fp = str(params[0] or "")
            cid = self._cases_by_fp.get(fp)
            return _FakeResult((cid,) if cid else None)

        # Day bucket fetch (group path). Not needed in these tests; return a stable date.
        if "date_trunc('day'" in sql:
            return _FakeResult(("2026-01-04",))

        # Cases upsert (both fp: and wl:)
        if "INSERT INTO cases" in sql and "RETURNING case_id" in sql:
            ck = str(params[0])
            cid = self._cases_by_key.get(ck)
            if not cid:
                cid = self._new_id()
                self._cases_by_key[ck] = cid
            return _FakeResult((cid,))

        # Group lookup path (not used here)
        if "FROM investigation_runs r" in sql and "JOIN cases c" in sql:
            return _FakeResult(None)

        raise AssertionError(f"Unexpected SQL in fake conn: {sql}")


def test_caseize_workload_identity_groups_different_fingerprints_same_case() -> None:
    conn = _FakeConn()
    inp1 = CaseizeInput(
        alert_fingerprint="fp-a",
        alertname="KubernetesPodNotHealthy",
        family="pod_not_healthy",
        cluster="c1",
        target_type="pod",
        namespace="test",
        container=None,
        workload_kind="Deployment",
        workload_name="room-management-api",
        service=None,
        instance=None,
    )
    inp2 = CaseizeInput(
        alert_fingerprint="fp-b",
        alertname="KubernetesPodNotHealthy",
        family="pod_not_healthy",
        cluster="c1",
        target_type="pod",
        namespace="test",
        container=None,
        workload_kind="Deployment",
        workload_name="room-management-api",
        service=None,
        instance=None,
    )

    case1, reason1, _ = caseize_run(conn, inp1)
    case2, reason2, _ = caseize_run(conn, inp2)

    assert case1 == case2
    assert reason1 == "workload_upsert"
    assert reason2 == "workload_upsert"

    # Ensure we did not even attempt fingerprint lookup (workload path ignores fp churn)
    assert not any("FROM investigation_runs WHERE alert_fingerprint" in s for s, _p in conn.sql)


def test_caseize_workload_identity_includes_container_for_oom_killer() -> None:
    conn = _FakeConn()
    base = dict(
        alertname="KubernetesContainerOomKiller",
        family="oom_killed",
        cluster="c1",
        target_type="pod",
        namespace="test",
        workload_kind="Deployment",
        workload_name="room-management-api",
        service=None,
        instance=None,
    )
    inp_app = CaseizeInput(alert_fingerprint="fp-a", container="app", **base)  # type: ignore[arg-type]
    inp_side = CaseizeInput(alert_fingerprint="fp-b", container="sidecar", **base)  # type: ignore[arg-type]

    c1, _, _ = caseize_run(conn, inp_app)
    c2, _, _ = caseize_run(conn, inp_side)
    assert c1 != c2


def test_caseize_kubejobfailed_uses_workload_identity() -> None:
    """KubeJobFailed alerts should group by Job workload, not by fingerprint."""
    conn = _FakeConn()
    inp1 = CaseizeInput(
        alert_fingerprint="fp-job-1",
        alertname="KubeJobFailed",
        family="job_failure",
        cluster="prod",
        target_type="pod",
        namespace="batch",
        container=None,
        workload_kind="Job",
        workload_name="data-export-job",
        service=None,
        instance=None,
    )
    inp2 = CaseizeInput(
        alert_fingerprint="fp-job-2",
        alertname="KubeJobFailed",
        family="job_failure",
        cluster="prod",
        target_type="pod",
        namespace="batch",
        container=None,
        workload_kind="Job",
        workload_name="data-export-job",
        service=None,
        instance=None,
    )

    case1, reason1, _ = caseize_run(conn, inp1)
    case2, reason2, _ = caseize_run(conn, inp2)

    # Both runs should map to the same case (same Job workload)
    assert case1 == case2
    assert reason1 == "workload_upsert"
    assert reason2 == "workload_upsert"

    # Should NOT attempt fingerprint lookup (workload grouping should apply)
    assert not any("FROM investigation_runs WHERE alert_fingerprint" in s for s, _p in conn.sql)


def test_caseize_non_target_alerts_keep_fingerprint_behavior() -> None:
    conn = _FakeConn()
    inp = CaseizeInput(
        alert_fingerprint="fp-x",
        alertname="SomeOtherAlert",
        family="generic",
        cluster="c1",
        target_type="pod",
        namespace="test",
        container=None,
        workload_kind="Deployment",
        workload_name="room-management-api",
        service=None,
        instance=None,
    )
    case_id, reason, _ = caseize_run(conn, inp)
    assert reason in ("fingerprint_upsert", "fingerprint_existing_run")
    # Fingerprint lookup should have been attempted.
    assert any("FROM investigation_runs WHERE alert_fingerprint" in s for s, _p in conn.sql)
