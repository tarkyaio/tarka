"""Tests for NATS connection retry with backoff."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.queue.nats_retry import connect_nats_with_retry


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Replace asyncio.sleep with a no-op so tests don't wait."""
    monkeypatch.setattr("agent.queue.nats_retry.asyncio.sleep", AsyncMock())


def test_connect_succeeds_first_try():
    fake_nc = SimpleNamespace(jetstream=lambda: None)
    with patch("nats.connect", new_callable=AsyncMock, return_value=fake_nc) as mock_connect:
        nc = asyncio.run(connect_nats_with_retry(["nats://localhost:4222"], max_retries=3, backoff_base=1))
    assert nc is fake_nc
    assert mock_connect.call_count == 1


def test_connect_retries_then_succeeds():
    fake_nc = SimpleNamespace(jetstream=lambda: None)
    mock_connect = AsyncMock(
        side_effect=[ConnectionRefusedError("refused"), ConnectionRefusedError("refused"), fake_nc]
    )
    with patch("nats.connect", mock_connect):
        nc = asyncio.run(connect_nats_with_retry(["nats://localhost:4222"], max_retries=5, backoff_base=1))
    assert nc is fake_nc
    assert mock_connect.call_count == 3


def test_connect_exhausts_retries():
    mock_connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))
    with patch("nats.connect", mock_connect):
        with pytest.raises(ConnectionRefusedError):
            asyncio.run(connect_nats_with_retry(["nats://localhost:4222"], max_retries=3, backoff_base=1))
    assert mock_connect.call_count == 3


def test_backoff_cap():
    """Backoff should never exceed max_backoff."""
    delays = []
    original_sleep = AsyncMock(side_effect=lambda d: delays.append(d))

    mock_connect = AsyncMock(side_effect=[ConnectionRefusedError("refused")] * 9 + [SimpleNamespace()])
    with patch("nats.connect", mock_connect), patch("agent.queue.nats_retry.asyncio.sleep", original_sleep):
        asyncio.run(
            connect_nats_with_retry(
                ["nats://localhost:4222"],
                max_retries=10,
                backoff_base=2,
                max_backoff=60,
            )
        )
    # 2, 4, 8, 16, 32, 60, 60, 60, 60
    assert all(d <= 60 for d in delays)
    assert delays[-1] == 60


def test_reconnect_defaults_passed():
    """Ensure max_reconnect_attempts and reconnect_time_wait are passed to nats.connect."""
    fake_nc = SimpleNamespace()
    mock_connect = AsyncMock(return_value=fake_nc)
    with patch("nats.connect", mock_connect):
        asyncio.run(connect_nats_with_retry(["nats://localhost:4222"], max_retries=1, backoff_base=1))
    _, kwargs = mock_connect.call_args
    assert kwargs["max_reconnect_attempts"] == -1
    assert kwargs["reconnect_time_wait"] == 2


def test_reconnect_defaults_can_be_overridden():
    """Caller can override reconnect params."""
    fake_nc = SimpleNamespace()
    mock_connect = AsyncMock(return_value=fake_nc)
    with patch("nats.connect", mock_connect):
        asyncio.run(
            connect_nats_with_retry(
                ["nats://localhost:4222"],
                max_retries=1,
                backoff_base=1,
                max_reconnect_attempts=5,
                reconnect_time_wait=10,
            )
        )
    _, kwargs = mock_connect.call_args
    assert kwargs["max_reconnect_attempts"] == 5
    assert kwargs["reconnect_time_wait"] == 10


def test_env_var_defaults(monkeypatch):
    """NATS_CONNECT_RETRIES and NATS_CONNECT_BACKOFF_BASE env vars are respected."""
    monkeypatch.setenv("NATS_CONNECT_RETRIES", "2")
    monkeypatch.setenv("NATS_CONNECT_BACKOFF_BASE", "5")

    mock_connect = AsyncMock(side_effect=ConnectionRefusedError("refused"))
    with patch("nats.connect", mock_connect):
        with pytest.raises(ConnectionRefusedError):
            asyncio.run(connect_nats_with_retry(["nats://localhost:4222"]))
    # Should have tried exactly 2 times (from env var)
    assert mock_connect.call_count == 2
