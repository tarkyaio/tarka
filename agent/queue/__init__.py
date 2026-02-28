"""
Queue abstraction (Phase 0).

This is intentionally a small, dependency-free interface so later phases can wire in
NATS JetStream without forcing a large refactor of the webhook server.
"""

from agent.queue.base import AlertJob, AsyncQueueClient, QueueClient

__all__ = ["AlertJob", "QueueClient", "AsyncQueueClient"]
