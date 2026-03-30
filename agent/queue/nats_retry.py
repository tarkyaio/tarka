"""NATS connection helper with exponential backoff retry."""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


async def connect_nats_with_retry(
    servers: list[str],
    *,
    max_retries: int | None = None,
    backoff_base: float | None = None,
    max_backoff: float = 60,
    **kwargs,
) -> object:
    """
    Connect to NATS with exponential backoff retry.

    Reads defaults from env vars:
    - NATS_CONNECT_RETRIES (default: 10)
    - NATS_CONNECT_BACKOFF_BASE (default: 2)

    After connecting, the returned client is configured for automatic
    reconnection on later disconnects (unlimited attempts, 2s wait).
    """
    import nats  # type: ignore[import-not-found]

    if max_retries is None:
        max_retries = _env_int("NATS_CONNECT_RETRIES", 10)
    if backoff_base is None:
        backoff_base = float(_env_int("NATS_CONNECT_BACKOFF_BASE", 2))

    max_retries = max(1, max_retries)

    # Ensure resilient reconnection after the initial connect succeeds.
    kwargs.setdefault("max_reconnect_attempts", -1)
    kwargs.setdefault("reconnect_time_wait", 2)

    for attempt in range(max_retries):
        try:
            return await nats.connect(servers=servers, **kwargs)
        except Exception as exc:
            if attempt == max_retries - 1:
                logger.error(
                    "NATS connect failed after %d attempt(s): %s",
                    max_retries,
                    exc,
                )
                raise
            delay = min(backoff_base * (2**attempt), max_backoff)
            logger.warning(
                "NATS connect failed (attempt %d/%d): %s. Retrying in %.1fs...",
                attempt + 1,
                max_retries,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    # Unreachable, but keeps type checkers happy.
    raise RuntimeError("NATS connect failed")  # pragma: no cover
