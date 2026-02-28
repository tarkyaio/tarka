"""
Pytest config.

This repo isn't packaged/installed (no [project] metadata in pyproject.toml), so local
imports like `import agent` rely on the repo root being on sys.path.

In some environments (e.g. when invoking a global `pytest` entrypoint), that doesn't
happen reliably during collection. We pin the behavior here so tests can always import
the local `agent/` package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ensure_repo_root_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


_ensure_repo_root_on_syspath()


@pytest.fixture(autouse=True)
def _stub_jetstream_client_for_unit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The webhook server is enqueue-only and fails-fast at startup if JetStream is unreachable.

    Most unit tests do not run a real NATS server, but they still create `TestClient(ws.app)`
    (which triggers FastAPI startup hooks). Stub the JetStream client by default so tests
    don't attempt network connections during startup.

    Individual tests can override this by monkeypatching
    `agent.queue.nats_jetstream.get_client_from_env` themselves.
    """

    class _NoopQueueClient:
        async def warmup(self) -> None:  # noqa: D401
            return None

        async def enqueue(self, _job, *, msg_id=None):  # type: ignore[no-untyped-def]
            return "0"

    async def _fake_get_client_from_env():  # type: ignore[no-untyped-def]
        return _NoopQueueClient()

    monkeypatch.setattr("agent.queue.nats_jetstream.get_client_from_env", _fake_get_client_from_env)
