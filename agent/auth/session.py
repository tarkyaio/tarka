from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

from itsdangerous import BadSignature, BadTimeSignature, URLSafeTimedSerializer

from agent.auth.config import AuthConfig
from agent.auth.models import AuthUser


def session_cookie_name(cfg: AuthConfig) -> str:
    # `__Host-` requires Secure + Path=/ + no Domain; browsers may reject it on HTTP.
    return "__Host-tarka_session" if cfg.cookie_secure else "tarka_session"


SESSION_SALT = "tarka-console-session-v1"


def _serializer(cfg: AuthConfig) -> Optional[URLSafeTimedSerializer]:
    if not cfg.session_secret:
        return None
    return URLSafeTimedSerializer(secret_key=cfg.session_secret, salt=SESSION_SALT)


def encode_session(cfg: AuthConfig, user: AuthUser) -> Optional[str]:
    s = _serializer(cfg)
    if s is None:
        return None
    payload = asdict(user)
    # Keep cookie small and non-sensitive (no access tokens).
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return s.dumps(raw)


def decode_session(cfg: AuthConfig, value: str | None) -> Optional[AuthUser]:
    if not value:
        return None
    s = _serializer(cfg)
    if s is None:
        return None
    try:
        raw = s.loads(value, max_age=cfg.session_ttl_seconds)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        provider = str(data.get("provider") or "").strip() or "google"
        email = data.get("email")
        name = data.get("name")
        picture = data.get("picture")
        username = data.get("username")
        return AuthUser(
            provider=provider,
            email=str(email) if email else None,
            name=str(name) if name else None,
            picture=str(picture) if picture else None,
            username=str(username) if username else None,
        )
    except (BadSignature, BadTimeSignature, ValueError):
        return None


def clear_session_cookie_kwargs(cfg: AuthConfig) -> dict:
    return {
        "key": session_cookie_name(cfg),
        "value": "",
        "max_age": 0,
        "httponly": True,
        "secure": cfg.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }


def session_cookie_kwargs(cfg: AuthConfig, value: str) -> dict:
    return {
        "key": session_cookie_name(cfg),
        "value": value,
        "max_age": cfg.session_ttl_seconds,
        "httponly": True,
        "secure": cfg.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
