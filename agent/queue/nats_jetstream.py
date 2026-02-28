from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Any, Dict, Optional, Tuple

from agent.queue.base import AlertJob, AsyncQueueClient


class JetStreamQueueClient(AsyncQueueClient):
    """
    NATS JetStream queue client (async).

    Notes:
    - We keep a single cached connection per-process for speed.
    - Stream creation is best-effort for dev; production can pre-provision streams.
    """

    def __init__(self, *, nats_url: str, stream: str, subject: str) -> None:
        self.nats_url = (nats_url or "").strip()
        self.stream = (stream or "").strip()
        self.subject = (subject or "").strip()
        self._nc = None
        self._js = None

    async def _ensure_connected(self) -> None:
        if self._nc is not None and self._js is not None:
            return

        try:
            import nats  # type: ignore[import-not-found]
            from nats.js.errors import NotFoundError  # type: ignore[import-not-found]
        except Exception as e:
            raise RuntimeError("Missing NATS client dependency. Install `nats-py` to use JetStream.") from e

        nc = await nats.connect(servers=[self.nats_url])
        js = nc.jetstream()

        dup_s_raw = (os.getenv("JETSTREAM_DUPLICATE_WINDOW_SECONDS") or "").strip()
        try:
            dup_s = int(dup_s_raw) if dup_s_raw else 3600
        except Exception:
            dup_s = 3600
        dup_s = max(0, int(dup_s))

        # Best-effort stream provisioning (idempotent).
        try:
            await js.stream_info(self.stream)
        except NotFoundError:
            try:
                from nats.js.api import StreamConfig  # type: ignore[import-not-found]

                cfg = StreamConfig(
                    name=self.stream,
                    subjects=[self.subject],
                    duplicate_window=dup_s * 1_000_000_000,
                )
                await js.add_stream(config=cfg)
            except Exception:
                # Fallback for older nats-py versions.
                await js.add_stream(name=self.stream, subjects=[self.subject])
        else:
            # If the stream exists, best-effort ensure duplicate window is large enough.
            try:
                si = await js.stream_info(self.stream)
                cfg = getattr(si, "config", None)
                cur = getattr(cfg, "duplicate_window", None) if cfg is not None else None
                cur_s = int(cur or 0) / 1_000_000_000 if cur is not None else 0
                if dup_s and cur_s and cur_s >= dup_s:
                    pass
                elif dup_s:
                    from nats.js.api import StreamConfig  # type: ignore[import-not-found]

                    new_cfg = StreamConfig(
                        name=self.stream,
                        subjects=[self.subject],
                        duplicate_window=dup_s * 1_000_000_000,
                    )
                    try:
                        await js.update_stream(config=new_cfg)
                    except Exception:
                        # Some nats-py versions accept (name, config=...).
                        await js.update_stream(self.stream, config=new_cfg)
            except Exception:
                pass

        self._nc = nc
        self._js = js

    async def warmup(self) -> None:
        """
        Eagerly connect and ensure the JetStream stream exists.

        Used by the webhook server to fail-fast at startup if JetStream is unreachable
        or misconfigured.
        """
        await self._ensure_connected()

    async def enqueue(self, job: AlertJob, *, msg_id: Optional[str] = None) -> str:
        await self._ensure_connected()
        assert self._js is not None

        payload = job.model_dump(mode="json")
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

        # JetStream de-dupe uses a "message id" within the stream duplicate window.
        # In nats-py, the portable way to set it is the NATS header `Nats-Msg-Id`.
        headers = None
        if msg_id:
            try:
                from nats.aio.msg import Header  # type: ignore[import-not-found]

                h = Header()
                h["Nats-Msg-Id"] = str(msg_id)
                headers = h
            except Exception:
                headers = {"Nats-Msg-Id": str(msg_id)}

        pa = await self._js.publish(self.subject, data, headers=headers)  # type: ignore[union-attr]
        # `pa.seq` is the stream sequence number.
        return str(getattr(pa, "seq", "") or "")


_cache_lock = asyncio.Lock()
_cached: Optional[JetStreamQueueClient] = None
_cached_key: Optional[Tuple[str, str, str]] = None


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def default_subject(*, stream: str) -> str:
    # Convention: subject is stream name lowercased with `.alerts`.
    s = (stream or "TARKA").strip() or "TARKA"
    return f"{s.lower()}.alerts"


async def get_client_from_env() -> JetStreamQueueClient:
    """
    Cached JetStream client from env.

    Env:
    - NATS_URL (default: nats://127.0.0.1:4222)
    - JETSTREAM_STREAM (default: TARKA)
    - JETSTREAM_SUBJECT (default: <stream>.alerts)
    """
    global _cached, _cached_key

    nats_url = _env("NATS_URL", "nats://127.0.0.1:4222")
    stream = _env("JETSTREAM_STREAM", "TARKA")
    subject = _env("JETSTREAM_SUBJECT", default_subject(stream=stream))
    key = (nats_url, stream, subject)

    async with _cache_lock:
        if _cached is not None and _cached_key == key:
            return _cached
        _cached = JetStreamQueueClient(nats_url=nats_url, stream=stream, subject=subject)
        _cached_key = key
        return _cached


def compute_msg_id(*, alertname: str, fingerprint: str) -> str:
    # Keep it short and stable.
    a = (alertname or "Unknown").strip()
    fp = (fingerprint or "").strip()
    raw = f"{a}:{fp}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_msg_id_from_dedup_key(dedup_key: str) -> str:
    """
    JetStream stream-level dedupe key derived from our computed dedup key.

    We hash again to decouple the queue msg-id from the dedup key format (if the latter
    ever changes to a non-hash string).
    """
    s = (dedup_key or "").strip() or "unknown"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def compute_fingerprint_fallback(labels: Dict[str, Any]) -> str:
    payload = json.dumps(labels or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
