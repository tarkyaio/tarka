from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class AuthUser:
    """Authenticated user (from OIDC or local auth)."""

    provider: str  # oidc|local
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    username: Optional[str] = None  # For local auth


@dataclass
class LocalUser:
    """Local admin user stored in PostgreSQL."""

    id: int
    email: str
    username: str
    password_hash: str
    name: Optional[str]
    created_at: datetime
    created_by: Optional[str]
    last_login_at: Optional[datetime]
    is_active: bool
