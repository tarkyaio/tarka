"""Minimal S3 storage helper for writing investigation reports."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Union

_s3_client = None
_s3_client_lock = threading.Lock()


def _get_s3_client():
    """Return a cached boto3 S3 client (thread-safe lazy init)."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    with _s3_client_lock:
        if _s3_client is not None:
            return _s3_client
        import boto3  # type: ignore[import-not-found]

        _s3_client = boto3.client("s3")
        return _s3_client


@dataclass
class S3Storage:
    bucket: str
    prefix: str = ""

    def __post_init__(self) -> None:
        self.prefix = (self.prefix or "").strip("/")
        # Uses ambient AWS auth (IRSA in-cluster, env credentials locally, etc.)
        self._client = _get_s3_client()

    def key(self, rel_key: str) -> str:
        rel_key = rel_key.lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{rel_key}"
        return rel_key

    def exists(self, rel_key: str) -> bool:
        """Return True if the object exists; False if not found or if we can't check (403)."""
        key = self.key(rel_key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as e:
            # Handle ClientError exceptions
            try:
                from botocore.exceptions import ClientError  # type: ignore[import-not-found]

                if isinstance(e, ClientError):
                    error_code = e.response.get("Error", {}).get("Code")
                    http_status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")

                    # 404/NoSuchKey means object doesn't exist
                    if error_code in ("404", "NoSuchKey", "NotFound") or http_status == 404:
                        return False

                    # 403 Forbidden means we don't have permission to check
                    # Treat as "doesn't exist" so we proceed with write
                    # (Since fingerprints are unique, overwriting is acceptable)
                    if error_code == "403" or http_status == 403:
                        return False
            except Exception:
                pass

            # Some SDK variants raise a generic exception with status_code
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if status_code == 404:
                return False
            if status_code == 403:
                # Can't check due to permissions, assume doesn't exist
                return False

            # For any other error, re-raise
            raise

    def head_metadata(self, rel_key: str) -> Tuple[bool, Optional[datetime]]:
        """
        Return (exists, last_modified_utc-ish) for an S3 object.

        - exists=False for 404/NotFound, and also for 403 (can't check; treat as missing so caller can proceed).
        - last_modified is best-effort and may be None if missing/unavailable.
        """
        key = self.key(rel_key)
        try:
            res = self._client.head_object(Bucket=self.bucket, Key=key)
            lm = res.get("LastModified")
            return True, lm if isinstance(lm, datetime) else None
        except Exception as e:
            try:
                from botocore.exceptions import ClientError  # type: ignore[import-not-found]

                if isinstance(e, ClientError):
                    error_code = e.response.get("Error", {}).get("Code")
                    http_status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                    if error_code in ("404", "NoSuchKey", "NotFound") or http_status == 404:
                        return False, None
                    if error_code == "403" or http_status == 403:
                        return False, None
            except Exception:
                pass
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if status_code in (403, 404):
                return False, None
            raise

    def put_markdown(self, rel_key: str, body: str) -> None:
        key = self.key(rel_key)
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )

    def put_json(self, rel_key: str, body: Union[str, Dict[str, Any]]) -> None:
        key = self.key(rel_key)
        if isinstance(body, str):
            payload = body
        else:
            payload = json.dumps(body, sort_keys=True, indent=2)
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=payload.encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
