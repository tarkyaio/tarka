from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from agent.queue.base import AlertJob


def load_job(payload: str | bytes | Dict[str, Any]) -> AlertJob:
    """
    Parse a Phase 1 job payload into an AlertJob.

    Payload is expected to be JSON containing at least:
      { "alert": {..}, "time_window": "15m", "parent_status": "firing" }
    """
    if isinstance(payload, dict):
        return AlertJob.model_validate(payload)
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    s = str(payload or "").strip()
    data = json.loads(s) if s else {}
    return AlertJob.model_validate(data)


def run_alert_job(
    job: AlertJob,
    *,
    storage: Any,
    allowlist: Optional[List[str]] = None,
) -> Tuple[Any, List[str]]:
    """
    Run a single job using the existing webhook processing logic.

    Phase 1 is intentionally conservative:
    - We reuse `agent.api.webhook.process_alerts` so behavior matches the live webhook path.
    - This is a building block for Phase 2+ where jobs come from JetStream.
    """
    import agent.api.webhook as ws

    stats, created = ws.process_alerts(
        [job.alert],
        time_window=job.time_window,
        storage=storage,
        allowlist=allowlist,
        parent_status=job.parent_status,
    )
    return stats, created


def run_job_from_env(job: AlertJob) -> Tuple[Any, List[str]]:
    """
    Convenience wrapper for running a job using the same env vars as the webhook server:
    - S3_BUCKET (optional for local dev, required for production)
    - S3_PREFIX (optional)
    - LOCAL_STORAGE_DIR (default: ./investigations, used if S3_BUCKET not set)
    - ALERTNAME_ALLOWLIST (optional)
    """
    import agent.api.webhook as ws

    bucket = (os.getenv("S3_BUCKET") or "").strip()
    allowlist = ws._get_allowlist()

    if bucket:
        # Use S3 storage (production)
        from agent.storage.s3_store import S3Storage

        prefix = (os.getenv("S3_PREFIX") or "").strip()
        storage = S3Storage(bucket=bucket, prefix=(prefix or "").strip("/"))
    else:
        # Use local filesystem storage (development)
        from agent.storage.local_store import LocalStorage

        local_dir = (os.getenv("LOCAL_STORAGE_DIR") or "./investigations").strip()
        storage = LocalStorage(base_dir=local_dir)

    return run_alert_job(job, storage=storage, allowlist=allowlist)
