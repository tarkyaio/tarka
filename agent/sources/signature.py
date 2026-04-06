from __future__ import annotations

import hashlib
import hmac


def verify_signature(raw_body: bytes, secret: str, header_value: str, prefix: str = "") -> bool:
    """
    Verify an HMAC-SHA256 webhook signature.

    Strips ``prefix`` from the header value before comparing (e.g. ``sha256=`` for
    GitHub-style signatures).  Uses constant-time comparison to prevent timing attacks.

    Returns False (rather than raising) on any malformed input so the caller can
    return a clean 401 without leaking internal details.
    """
    if not header_value:
        return False

    candidate = header_value
    if prefix and candidate.startswith(prefix):
        candidate = candidate[len(prefix) :]

    try:
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    except Exception:
        return False

    return hmac.compare_digest(expected, candidate)
