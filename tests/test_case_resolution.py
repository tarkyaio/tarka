from __future__ import annotations


class _FakeConn:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, sql: str, params):  # type: ignore[no-untyped-def]
        self.calls.append((sql, params))
        return self


def test_update_case_resolution_requires_category_and_summary_for_closed() -> None:
    import agent.api.webhook as ws

    conn = _FakeConn()
    ok, msg = ws._update_case_resolution(
        conn,
        case_id="c1",
        status="closed",
        resolution_category="",
        resolution_summary="x",
        postmortem_link=None,
    )
    assert ok is False
    assert msg == "resolution_category_required"

    ok2, msg2 = ws._update_case_resolution(
        conn,
        case_id="c1",
        status="closed",
        resolution_category="deploy",
        resolution_summary="",
        postmortem_link=None,
    )
    assert ok2 is False
    assert msg2 == "resolution_summary_required"


def test_update_case_resolution_executes_update_sql_for_closed_and_open() -> None:
    import agent.api.webhook as ws

    conn = _FakeConn()
    ok, msg = ws._update_case_resolution(
        conn,
        case_id="c1",
        status="closed",
        resolution_category="deploy",
        resolution_summary="rolled back",
        postmortem_link="https://example.com/postmortem",
    )
    assert ok is True
    assert msg == "ok"
    assert conn.calls
    sql0, params0 = conn.calls[-1]
    assert "UPDATE cases" in sql0
    assert params0[-1] == "c1"

    ok2, msg2 = ws._update_case_resolution(
        conn,
        case_id="c1",
        status="open",
        resolution_category=None,
        resolution_summary=None,
        postmortem_link=None,
    )
    assert ok2 is True
    assert msg2 == "ok"
    sql1, params1 = conn.calls[-1]
    assert "SET status = 'open'" in sql1
    assert params1 == ("c1",)
