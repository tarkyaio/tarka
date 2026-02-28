from __future__ import annotations

import base64
import os


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def random_token(nbytes: int = 32) -> str:
    return b64url(os.urandom(nbytes))


def sanitize_next_path(next_path: str | None) -> str:
    """
    Prevent open-redirects: allow only relative paths like `/inbox`.
    """
    p = (next_path or "").strip()
    if not p:
        return "/"
    if not p.startswith("/"):
        return "/"
    # Disallow scheme-relative: `//evil.com`
    if p.startswith("//"):
        return "/"
    # Keep it simple: strip any CR/LF.
    p = p.replace("\r", "").replace("\n", "")
    return p or "/"
