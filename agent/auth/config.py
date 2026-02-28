from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional


@dataclass(frozen=True)
class AuthConfig:
    # OIDC Configuration (generic, optional)
    oidc_discovery_url: Optional[str]
    oidc_client_id: Optional[str]
    oidc_client_secret: Optional[str]
    oidc_provider_name: Optional[str]  # Display name (default: auto-detected)
    oidc_provider_logo: Optional[str]  # Logo URL (default: auto-detected)

    # Session configuration
    public_base_url: Optional[str]  # Required for OIDC redirect
    session_secret: Optional[str]  # Required for session signing
    session_ttl_seconds: int
    cookie_secure: bool

    # OIDC domain enforcement (optional, for restricting SSO to specific domains)
    allowed_domains: List[str]

    # Local auth configuration
    admin_initial_username: str  # Initial admin username (default: "admin")
    admin_initial_password: Optional[str]  # Initial admin password (required on first startup)

    @property
    def oidc_enabled(self) -> bool:
        """OIDC is enabled if discovery URL and credentials are configured."""
        return bool(self.oidc_discovery_url and self.oidc_client_id and self.oidc_client_secret)

    @property
    def local_enabled(self) -> bool:
        """Local auth is always enabled (admin fallback)."""
        return True


def _parse_csv(value: str) -> List[str]:
    items = [x.strip().lower() for x in (value or "").split(",")]
    return [x for x in items if x]


@lru_cache(maxsize=1)
def load_auth_config() -> AuthConfig:
    """
    Load authentication configuration from environment variables.

    OIDC is enabled if OIDC_DISCOVERY_URL, OIDC_CLIENT_ID, and OIDC_CLIENT_SECRET are set.
    Local auth is always enabled as a fallback.
    """
    public_base_url = (os.getenv("AUTH_PUBLIC_BASE_URL", "") or "").strip() or None
    cookie_secure_env = (os.getenv("AUTH_COOKIE_SECURE", "") or "").strip().lower()
    if cookie_secure_env in ("1", "true", "yes", "on"):
        cookie_secure = True
    elif cookie_secure_env in ("0", "false", "no", "off"):
        cookie_secure = False
    else:
        # Default: secure cookies when base URL is https; otherwise allow local dev.
        cookie_secure = True if (public_base_url or "").startswith("https://") else False

    ttl = int(float((os.getenv("AUTH_SESSION_TTL_SECONDS", "") or "43200").strip() or "43200"))  # 12h default
    if ttl <= 60:
        ttl = 60

    allowed_domains = _parse_csv(os.getenv("AUTH_ALLOWED_DOMAINS", ""))

    return AuthConfig(
        # OIDC configuration
        oidc_discovery_url=(os.getenv("OIDC_DISCOVERY_URL", "") or "").strip() or None,
        oidc_client_id=(os.getenv("OIDC_CLIENT_ID", "") or "").strip() or None,
        oidc_client_secret=(os.getenv("OIDC_CLIENT_SECRET", "") or "").strip() or None,
        oidc_provider_name=(os.getenv("OIDC_PROVIDER_NAME", "") or "").strip() or None,
        oidc_provider_logo=(os.getenv("OIDC_PROVIDER_LOGO", "") or "").strip() or None,
        # Session configuration
        public_base_url=public_base_url,
        session_secret=(os.getenv("AUTH_SESSION_SECRET", "") or "").strip() or None,
        session_ttl_seconds=ttl,
        cookie_secure=cookie_secure,
        allowed_domains=allowed_domains,
        # Local auth configuration
        admin_initial_username=(os.getenv("ADMIN_INITIAL_USERNAME", "") or "admin").strip(),
        admin_initial_password=(os.getenv("ADMIN_INITIAL_PASSWORD", "") or "").strip() or None,
    )
