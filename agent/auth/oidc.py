from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

import jwt  # PyJWT
import requests

from agent.auth.config import AuthConfig
from agent.auth.util import b64url

_discovery_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}
_jwks_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


def _get_discovery(discovery_url: str) -> Dict[str, Any]:
    """
    Fetch OIDC discovery document from provider.
    Caches result for 1 hour per discovery URL.
    """
    ts, cached = _discovery_cache.get(discovery_url, (0.0, None))
    now = time.time()
    if cached is not None and now - ts < 3600:
        return cached
    r = requests.get(discovery_url, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid OIDC discovery document")
    _discovery_cache[discovery_url] = (now, data)
    return data


def _get_jwks(jwks_uri: str) -> Dict[str, Any]:
    """
    Fetch JWKS (JSON Web Key Set) from provider.
    Caches result for 1 hour per JWKS URI.
    """
    ts, cached = _jwks_cache.get(jwks_uri, (0.0, None))
    now = time.time()
    if cached is not None and now - ts < 3600:
        return cached
    r = requests.get(jwks_uri, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid JWKS")
    _jwks_cache[jwks_uri] = (now, data)
    return data


def get_provider_metadata(cfg: AuthConfig) -> Dict[str, str]:
    """
    Get provider display metadata (name, logo) from discovery document.
    Returns auto-detected values or configured overrides.
    """
    if not cfg.oidc_discovery_url:
        raise ValueError("OIDC discovery URL not configured")

    disc = _get_discovery(cfg.oidc_discovery_url)

    # Try to extract provider name from issuer URL if not explicitly configured
    provider_name = cfg.oidc_provider_name
    if not provider_name:
        issuer = str(disc.get("issuer") or "")
        if "google" in issuer.lower():
            provider_name = "Google"
        elif "okta" in issuer.lower():
            provider_name = "Okta"
        elif "azure" in issuer.lower() or "microsoft" in issuer.lower():
            provider_name = "Microsoft"
        elif "auth0" in issuer.lower():
            provider_name = "Auth0"
        else:
            # Fallback: extract domain from issuer
            from urllib.parse import urlparse

            parsed = urlparse(issuer)
            provider_name = parsed.netloc.split(".")[0].title() if parsed.netloc else "SSO Provider"

    # Try to get logo URL (if not configured, use default based on provider)
    provider_logo = cfg.oidc_provider_logo
    if not provider_logo:
        if "google" in (cfg.oidc_discovery_url or "").lower():
            provider_logo = "https://www.google.com/favicon.ico"
        elif "okta" in (cfg.oidc_discovery_url or "").lower():
            provider_logo = "https://www.okta.com/favicon.ico"
        elif "microsoft" in (cfg.oidc_discovery_url or "").lower() or "azure" in (cfg.oidc_discovery_url or "").lower():
            provider_logo = "https://www.microsoft.com/favicon.ico"
        elif "auth0" in (cfg.oidc_discovery_url or "").lower():
            provider_logo = "https://cdn.auth0.com/styleguide/latest/lib/logos/img/favicon.png"
        else:
            # Generic OIDC logo
            provider_logo = ""

    return {
        "name": provider_name,
        "logo": provider_logo,
    }


def build_authorize_url(
    cfg: AuthConfig,
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    hd: Optional[str] = None,
) -> str:
    """
    Build authorization URL for OIDC provider.
    Supports PKCE (Proof Key for Code Exchange) for security.
    """
    if not cfg.oidc_discovery_url:
        raise ValueError("OIDC discovery URL not configured")
    if not cfg.oidc_client_id:
        raise ValueError("OIDC client ID not configured")

    disc = _get_discovery(cfg.oidc_discovery_url)
    auth_endpoint = str(disc.get("authorization_endpoint") or "")
    if not auth_endpoint:
        raise ValueError("OIDC discovery missing authorization_endpoint")

    params = {
        "client_id": cfg.oidc_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if hd:
        # Google-specific: hosted domain restriction
        params["hd"] = hd

    # Manually encode to keep dependencies minimal.
    from urllib.parse import urlencode

    return f"{auth_endpoint}?{urlencode(params)}"


def exchange_code_for_tokens(
    cfg: AuthConfig,
    *,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> Dict[str, Any]:
    """
    Exchange authorization code for tokens (id_token, access_token).
    Uses PKCE code_verifier for security.
    """
    if not cfg.oidc_discovery_url:
        raise ValueError("OIDC discovery URL not configured")
    if not cfg.oidc_client_id or not cfg.oidc_client_secret:
        raise ValueError("OIDC client ID/secret not configured")

    disc = _get_discovery(cfg.oidc_discovery_url)
    token_endpoint = str(disc.get("token_endpoint") or "")
    if not token_endpoint:
        raise ValueError("OIDC discovery missing token_endpoint")

    payload = {
        "client_id": cfg.oidc_client_id,
        "client_secret": cfg.oidc_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    r = requests.post(token_endpoint, data=payload, timeout=10)
    if r.status_code >= 400:
        # Avoid leaking sensitive info; include minimal context.
        raise ValueError(f"Token exchange failed (status={r.status_code})")
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid token response")
    return data


def validate_id_token(
    cfg: AuthConfig,
    *,
    id_token: str,
    expected_nonce: str,
) -> Dict[str, Any]:
    """
    Validate ID token from OIDC provider.
    - Verifies JWT signature using provider's public keys
    - Validates issuer, audience, nonce
    - Checks email verification status
    """
    if not cfg.oidc_discovery_url:
        raise ValueError("OIDC discovery URL not configured")
    if not cfg.oidc_client_id:
        raise ValueError("OIDC client ID not configured")

    disc = _get_discovery(cfg.oidc_discovery_url)
    issuer = str(disc.get("issuer") or "")
    jwks_uri = str(disc.get("jwks_uri") or "")
    if not issuer or not jwks_uri:
        raise ValueError("OIDC discovery missing issuer/jwks_uri")

    hdr = jwt.get_unverified_header(id_token)
    kid = str(hdr.get("kid") or "")
    if not kid:
        raise ValueError("ID token missing kid")

    jwks = _get_jwks(jwks_uri)
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise ValueError("Invalid JWKS keys")

    jwk = None
    for k in keys:
        if isinstance(k, dict) and str(k.get("kid") or "") == kid:
            jwk = k
            break
    if jwk is None:
        raise ValueError("Unknown signing key (kid)")

    # Construct RSA public key from JWK.
    key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))

    claims = jwt.decode(
        id_token,
        key=key,
        algorithms=["RS256"],
        audience=cfg.oidc_client_id,
        issuer=issuer,
        options={
            "require": ["exp", "iat", "iss", "aud"],
        },
    )
    if not isinstance(claims, dict):
        raise ValueError("Invalid ID token claims")

    nonce = str(claims.get("nonce") or "")
    if not nonce or nonce != expected_nonce:
        raise ValueError("Nonce mismatch")

    # Some providers may not include email_verified claim; treat as optional
    email_verified = claims.get("email_verified")
    if email_verified is not None and email_verified is not True:
        raise ValueError("Email not verified")

    return claims


def pkce_challenge(verifier: str) -> str:
    """
    Generate PKCE challenge from verifier using SHA256.
    """
    import hashlib

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return b64url(digest)
