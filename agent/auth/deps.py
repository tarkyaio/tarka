from __future__ import annotations

from typing import Optional

from fastapi import Request

from agent.auth.config import load_auth_config
from agent.auth.models import AuthUser


def authenticate_request(request: Request) -> Optional[AuthUser]:
    """
    Authenticate a request and return an AuthUser if present/valid.

    This checks session cookies from both OIDC and local auth.
    Authentication is always required (no "disabled" mode).
    """
    cfg = load_auth_config()

    # Check session cookie (works for both OIDC and local auth)
    try:
        from agent.auth.session import decode_session, session_cookie_name
    except Exception:
        # If session deps not available, fail closed
        return None

    user = decode_session(cfg, request.cookies.get(session_cookie_name(cfg)))
    if user is not None:
        return user

    # No valid authentication found
    return None
