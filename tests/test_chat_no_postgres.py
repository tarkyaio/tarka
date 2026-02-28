def _clear_pg_env(monkeypatch):
    for k in (
        "POSTGRES_DSN",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(k, raising=False)


def test_threaded_chat_returns_graceful_error_without_postgres(monkeypatch):
    _clear_pg_env(monkeypatch)

    from agent.memory.chat import get_or_create_global_thread, list_threads

    ok, msg, thr = get_or_create_global_thread(user_key="mock@example.com")
    assert ok is False
    assert thr is None
    assert "Postgres not configured" in msg

    ok2, msg2, items = list_threads(user_key="mock@example.com", limit=10)
    assert ok2 is False
    assert items == []
    assert "Postgres not configured" in msg2


def test_global_chat_tools_return_graceful_error_without_postgres(monkeypatch):
    _clear_pg_env(monkeypatch)

    from agent.authz.policy import ChatPolicy
    from agent.chat.global_tools import run_global_tool

    policy = ChatPolicy(enabled=True)
    res = run_global_tool(policy=policy, tool="cases.count", args={"status": "all"})
    assert res.ok is False
    assert res.error in ("postgres_not_configured", "Postgres not configured")
