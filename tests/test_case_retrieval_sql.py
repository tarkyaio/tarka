from __future__ import annotations

from datetime import datetime, timedelta


class _Conn:
    def __init__(self) -> None:
        self.last_sql = None
        self.last_params = None

    def execute(self, sql: str, params):  # type: ignore[no-untyped-def]
        self.last_sql = sql
        self.last_params = params
        return self

    def fetchall(self):  # type: ignore[no-untyped-def]
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        return False


def test_find_similar_runs_does_not_emit_null_typed_params(monkeypatch) -> None:
    from agent.core.models import AlertInstance, DerivedFeatures, Investigation, TargetRef, TimeWindow
    from agent.memory.case_retrieval import find_similar_runs

    # Minimal investigation with family, but no cluster/namespace/workload -> optional filters should be omitted.
    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    inv = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "X"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=start, end_time=end),
        target=TargetRef(target_type="pod"),
    )
    inv.analysis.features = DerivedFeatures(family="cpu_throttling")

    class _Cfg:
        memory_enabled = True
        db_auto_migrate = False
        postgres_dsn = "dsn"
        postgres_host = None
        postgres_port = 5432
        postgres_db = None
        postgres_user = None
        postgres_password = None

    monkeypatch.setattr("agent.memory.case_retrieval.load_memory_config", lambda: _Cfg())
    monkeypatch.setattr("agent.memory.case_retrieval.build_postgres_dsn", lambda _cfg: "dsn")
    conn = _Conn()
    monkeypatch.setattr("agent.memory.case_retrieval._connect", lambda _dsn: conn)

    ok, msg, sims = find_similar_runs(inv, limit=5)
    assert ok is True
    assert sims == []
    assert conn.last_sql is not None
    assert "r.family = %s" in conn.last_sql
    # Ensure no optional filters were added.
    assert "r.cluster IS NOT DISTINCT FROM" not in conn.last_sql
    assert "r.namespace IS NOT DISTINCT FROM" not in conn.last_sql
    assert "r.workload_name IS NOT DISTINCT FROM" not in conn.last_sql
    assert "r.workload_kind IS NOT DISTINCT FROM" not in conn.last_sql
    # Params should be: family, fp exclusion, limit (fp exclusion is included because inv.alert.fingerprint is set).
    assert isinstance(conn.last_params, tuple)
    assert conn.last_params[0] == "cpu_throttling"
    assert conn.last_params[-1] == 5
