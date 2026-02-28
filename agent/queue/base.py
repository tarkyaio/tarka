from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol

from pydantic import BaseModel, Field


class AlertJob(BaseModel):
    """
    A single unit of work for investigation.

    Phase 0 scope:
    - Defines the stable job schema we will later publish to JetStream.
    - Not yet used by the webhook server (no behavior changes yet).
    """

    alert: Dict[str, Any]
    time_window: str
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parent_status: Optional[str] = None


class QueueClient(Protocol):
    """
    Minimal queue interface. Implementations can be in-process, JetStream, etc.
    """

    def enqueue(self, job: AlertJob) -> str:
        """
        Enqueue a job for asynchronous processing.

        Returns a queue message id / ack token.
        """


class AsyncQueueClient(Protocol):
    """
    Async queue interface, suitable for asyncio-based clients like NATS JetStream.
    """

    async def enqueue(self, job: AlertJob, *, msg_id: Optional[str] = None) -> str:
        """
        Enqueue a job for asynchronous processing.

        Returns a queue message id / ack token.
        """
