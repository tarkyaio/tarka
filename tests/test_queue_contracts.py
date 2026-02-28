from __future__ import annotations


def test_queue_imports() -> None:
    # Contract: queue module exists and is importable (wiring is covered elsewhere).
    from agent.queue import AlertJob, QueueClient  # noqa: F401
