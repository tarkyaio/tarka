from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from agent.api.worker import run_job_from_env
from agent.queue.base import AlertJob

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_csv_ints(name: str) -> list[int]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        s = part.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except Exception:
            continue
    return out


def _ns_from_seconds(seconds: int) -> int:
    return max(0, int(seconds)) * 1_000_000_000


def decide_disposition(*, errors: int, delivery_count: int, max_deliver: int) -> str:
    """
    Return one of: "ack", "nak", "dlq".
    """
    max_deliver_i = max(1, int(max_deliver or 1))
    delivered_i = max(1, int(delivery_count or 1))
    if errors <= 0:
        return "ack"
    if delivered_i >= max_deliver_i:
        return "dlq"
    return "nak"


def should_retry_from_stats(stats: Any) -> bool:
    """
    Decide whether a job should be retried based on the webhook-style stats object.

    Current conservative policy:
    - if any errors were recorded, retry (NAK) to handle transient dependency failures
    - otherwise ACK
    """
    try:
        return int(getattr(stats, "errors", 0) or 0) > 0
    except Exception:
        return True


async def _msg_delivery_count(msg: Any) -> int:
    """
    Best-effort delivery count from JetStream message metadata.
    """
    md = getattr(msg, "metadata", None)
    n = getattr(md, "num_delivered", None) if md is not None else None
    try:
        return int(n or 1)
    except Exception:
        return 1


async def _safe_in_progress(msg: Any) -> None:
    try:
        await msg.in_progress()
    except Exception:
        return


async def _dlq_publish(js: Any, *, dlq_subject: str, payload: dict) -> None:
    try:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except Exception:
        data = b"{}"
    try:
        await js.publish(dlq_subject, data)
    except Exception:
        # Never crash the worker loop due to DLQ publishing.
        return


async def _handle_msg(*, js: Any, msg: Any, max_deliver: int, dlq_subject: str) -> None:
    """
    Handle one JetStream message.
    """
    logger.info("Getting delivery count...")
    delivery_count = await _msg_delivery_count(msg)
    logger.info(f"Handling message (delivery #{delivery_count})...")

    # Parse job
    try:
        logger.info("Extracting message data...")
        data = getattr(msg, "data", b"") or b""
        logger.info(f"Parsing JSON ({len(data)} bytes)...")
        payload = json.loads(data.decode("utf-8"))
        logger.info("Validating AlertJob model...")
        logger.info(f"Payload keys: {list(payload.keys())}")
        try:
            # Run validation in thread to avoid blocking event loop
            job = await asyncio.to_thread(AlertJob.model_validate, payload)
            logger.info("✓ Model validation succeeded")
        except Exception as e:
            logger.error(f"Model validation failed: {e}", exc_info=True)
            raise
        logger.info(f"✓ Job parsed: {len(job.alerts) if hasattr(job, 'alerts') else 'N/A'} alert(s)")
    except Exception:
        await _dlq_publish(
            js,
            dlq_subject=dlq_subject,
            payload={
                "kind": "poison_message",
                "reason": "json_or_schema_error",
                "delivery_count": delivery_count,
                "raw": (getattr(msg, "data", b"") or b"")[:4096].decode("utf-8", errors="replace"),
            },
        )
        await msg.ack()
        return

    # Heartbeat: keep ack timer extended during long investigations.
    in_progress_s = max(0, _env_int("WORKER_IN_PROGRESS_SECONDS", 30))
    stop = asyncio.Event()

    async def _heartbeat() -> None:
        if in_progress_s <= 0:
            return
        while not stop.is_set():
            await asyncio.sleep(in_progress_s)
            if stop.is_set():
                break
            await _safe_in_progress(msg)

    hb_task = asyncio.create_task(_heartbeat())
    try:
        logger.info(f"Running investigation job (delivery #{delivery_count})...")
        stats, _created = await asyncio.to_thread(run_job_from_env, job)
        logger.info(
            f"Job completed. Stats: errors={getattr(stats, 'errors', 0)}, processed_firing={getattr(stats, 'processed_firing', 0)}"
        )
    except Exception as e:
        logger.error(f"Job execution failed: {e}", exc_info=True)
        stats = None
        errors = 1
    finally:
        stop.set()
        hb_task.cancel()

    errors = 0
    try:
        errors = int(getattr(stats, "errors", 0) or 0) if stats else 1
    except Exception:
        errors = 1

    disposition = decide_disposition(errors=errors, delivery_count=delivery_count, max_deliver=max_deliver)
    logger.info(
        f"Disposition: {disposition} (errors={errors}, delivery_count={delivery_count}, max_deliver={max_deliver})"
    )

    if disposition == "ack":
        await msg.ack()
        logger.info("✓ Message ACKed")
        return
    if disposition == "nak":
        await msg.nak()
        logger.warning("⚠ Message NAKed (will be redelivered)")
        return
    # DLQ on final attempt
    await _dlq_publish(
        js,
        dlq_subject=dlq_subject,
        payload={
            "kind": "job_failed",
            "delivery_count": delivery_count,
            "max_deliver": max_deliver,
            "job": job.model_dump(mode="json"),
        },
    )
    await msg.ack()


async def run_worker_forever() -> None:
    """
    Phase 3: JetStream worker loop.

    Env (defaults are dev-friendly):
    - NATS_URL (default: nats://127.0.0.1:4222)
    - JETSTREAM_STREAM (default: TARKA)
    - JETSTREAM_SUBJECT (default: <stream>.alerts)
    - JETSTREAM_DURABLE (default: WORKERS)
    - JETSTREAM_ACK_WAIT_SECONDS (default: 1800)
    - JETSTREAM_MAX_DELIVER (default: 5)
    - JETSTREAM_BACKOFF_SECONDS (optional: comma-separated seconds, e.g. "5,30,120")
    - JETSTREAM_DLQ_SUBJECT (default: tarka.dlq)
    - WORKER_CONCURRENCY (default: 2)
    - WORKER_FETCH_BATCH (default: 10)
    - WORKER_FETCH_TIMEOUT_SECONDS (default: 1)
    - WORKER_IN_PROGRESS_SECONDS (default: 30) heartbeat interval to extend ack deadline
    """
    try:
        import nats  # type: ignore[import-not-found]
        from nats.errors import TimeoutError  # type: ignore[import-not-found]
        from nats.js.errors import NotFoundError  # type: ignore[import-not-found]
    except Exception as e:
        raise RuntimeError("nats-py is required to run the JetStream worker") from e

    nats_url = (os.getenv("NATS_URL") or "").strip() or "nats://127.0.0.1:4222"
    stream = (os.getenv("JETSTREAM_STREAM") or "").strip() or "TARKA"
    subject = (os.getenv("JETSTREAM_SUBJECT") or "").strip() or f"{stream.lower()}.alerts"
    durable = (os.getenv("JETSTREAM_DURABLE") or "").strip() or "WORKERS"

    concurrency = max(1, _env_int("WORKER_CONCURRENCY", 2))
    batch = max(1, _env_int("WORKER_FETCH_BATCH", 10))
    timeout_s = max(1, _env_int("WORKER_FETCH_TIMEOUT_SECONDS", 1))

    logger.info(f"Connecting to NATS at {nats_url}...")
    nc = await nats.connect(servers=[nats_url])
    logger.info("✓ Connected to NATS")

    js = nc.jetstream()
    logger.info(f"Configuring JetStream (stream={stream}, subject={subject}, durable={durable})...")

    dup_s = _env_int("JETSTREAM_DUPLICATE_WINDOW_SECONDS", 3600)
    dup_s = max(0, int(dup_s))

    # Best-effort stream provisioning for dev (idempotent).
    logger.info(f"Checking if stream '{stream}' exists...")
    try:
        await js.stream_info(stream)
        logger.info(f"✓ Stream '{stream}' already exists")
    except NotFoundError:
        logger.info(f"Stream '{stream}' not found, creating...")
        try:
            from nats.js.api import StreamConfig  # type: ignore[import-not-found]

            cfg = StreamConfig(name=stream, subjects=[subject], duplicate_window=_ns_from_seconds(dup_s))
            await js.add_stream(config=cfg)
        except Exception:
            await js.add_stream(name=stream, subjects=[subject])
    else:
        # Best-effort ensure duplicate window is large enough for queue-level dedupe.
        try:
            si = await js.stream_info(stream)
            cfg = getattr(si, "config", None)
            cur = getattr(cfg, "duplicate_window", None) if cfg is not None else None
            cur_s = int(cur or 0) / 1_000_000_000 if cur is not None else 0
            if dup_s and (not cur_s or cur_s < dup_s):
                from nats.js.api import StreamConfig  # type: ignore[import-not-found]

                new_cfg = StreamConfig(name=stream, subjects=[subject], duplicate_window=_ns_from_seconds(dup_s))
                try:
                    await js.update_stream(config=new_cfg)
                except Exception:
                    await js.update_stream(stream, config=new_cfg)
        except Exception:
            pass

    # Explicit consumer config (Phase 4 hardening).
    ack_wait_s = max(10, _env_int("JETSTREAM_ACK_WAIT_SECONDS", 1800))
    max_deliver = max(1, _env_int("JETSTREAM_MAX_DELIVER", 5))
    backoff_s = [x for x in _env_csv_ints("JETSTREAM_BACKOFF_SECONDS") if x > 0]
    # Ensure ack_wait is >= max backoff to avoid immediate redeliveries.
    if backoff_s and ack_wait_s < max(backoff_s):
        ack_wait_s = max(backoff_s) + 60

    try:
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy  # type: ignore[import-not-found]

        cfg = ConsumerConfig(
            durable_name=durable,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=_ns_from_seconds(ack_wait_s),
            max_deliver=max_deliver,
            deliver_policy=DeliverPolicy.ALL,
            filter_subject=subject,
            backoff=[_ns_from_seconds(x) for x in backoff_s] if backoff_s else None,
        )
        try:
            await js.add_consumer(stream=stream, durable_name=durable, config=cfg)
        except TypeError:
            # Some nats-py versions accept only (stream, config=...).
            await js.add_consumer(stream, config=cfg)
        except Exception:
            # If it already exists or can't be created, continue; pull_subscribe may bind.
            pass
    except Exception:
        # If ConsumerConfig isn't available, fall back to defaults.
        pass

    # DLQ stream provisioning (best-effort).
    dlq_subject = (os.getenv("JETSTREAM_DLQ_SUBJECT") or "").strip() or "tarka.dlq"
    dlq_stream = (os.getenv("JETSTREAM_DLQ_STREAM") or "").strip() or f"{stream}_DLQ"
    try:
        await js.stream_info(dlq_stream)
    except Exception:
        try:
            await js.add_stream(name=dlq_stream, subjects=[dlq_subject])
        except Exception:
            pass

    sub = await js.pull_subscribe(subject, durable=durable, stream=stream)

    sem = asyncio.Semaphore(concurrency)

    max_deliver_env = max(1, _env_int("JETSTREAM_MAX_DELIVER", 5))

    async def _guarded(msg: Any) -> None:
        async with sem:
            try:
                await _handle_msg(js=js, msg=msg, max_deliver=max_deliver_env, dlq_subject=dlq_subject)
            except Exception:
                # If handler fails, NAK so JetStream can redeliver.
                try:
                    await msg.nak()
                except Exception:
                    pass

    logger.info(f"✓ Worker started (concurrency={concurrency}, batch={batch}, timeout={timeout_s}s)")
    logger.info(f"Waiting for messages on stream '{stream}' (subject='{subject}')...")

    while True:
        try:
            msgs = await sub.fetch(batch, timeout=timeout_s)
        except TimeoutError:
            continue
        except asyncio.TimeoutError:
            continue

        tasks = [asyncio.create_task(_guarded(m)) for m in (msgs or [])]
        if tasks:
            logger.info(f"Processing {len(tasks)} message(s)...")
            await asyncio.gather(*tasks, return_exceptions=True)
