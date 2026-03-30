"""LLM token pricing lookup (cost per 1M tokens, USD).

Three-tier resolution:
  1. PostgreSQL ``llm_pricing`` table (memory-cached, TTL 5 min)
  2. litellm community JSON (memory-cached, TTL 24 h)
  3. ``None`` (unknown model)
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------

# model_pattern -> (input_cost_per_1m, output_cost_per_1m, source, updated_at)
_db_cache: Dict[str, Tuple[float, float, str, datetime]] = {}
_db_cache_ts: float = 0.0
_DB_CACHE_TTL = 300  # 5 minutes

# model -> (input_cost_per_1m, output_cost_per_1m)
_litellm_cache: Dict[str, Tuple[float, float]] = {}
_litellm_cache_ts: float = 0.0
_LITELLM_CACHE_TTL = 86400  # 24 hours

_LITELLM_STALE_DAYS = 30
_LITELLM_JSON_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/" "model_prices_and_context_window.json"

# ---------------------------------------------------------------------------
# Tier 1 — PostgreSQL
# ---------------------------------------------------------------------------


def _get_dsn() -> Optional[str]:
    """Build a Postgres DSN from environment config, or ``None``."""
    try:
        from agent.memory.config import build_postgres_dsn, load_memory_config

        return build_postgres_dsn(load_memory_config())
    except Exception:
        return None


def _load_db_pricing() -> Dict[str, Tuple[float, float, str, datetime]]:
    """Load all rows from ``llm_pricing`` into a dict keyed by model_pattern."""
    global _db_cache, _db_cache_ts

    now = time.monotonic()
    if _db_cache and (now - _db_cache_ts) < _DB_CACHE_TTL:
        return _db_cache

    dsn = _get_dsn()
    if not dsn:
        return _db_cache  # keep stale cache (or empty) if no DB

    try:
        import psycopg  # type: ignore[import-not-found]

        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "SELECT model_pattern, input_cost_per_1m, output_cost_per_1m, source, updated_at " "FROM llm_pricing"
            ).fetchall()

        result: Dict[str, Tuple[float, float, str, datetime]] = {}
        for r in rows:
            result[str(r[0])] = (float(r[1]), float(r[2]), str(r[3]), r[4])

        _db_cache = result
        _db_cache_ts = now
        return _db_cache
    except Exception as exc:
        logger.debug("llm_pricing DB load failed: %s", exc)
        return _db_cache


def _match_db(model: str) -> Optional[Tuple[float, float]]:
    """Exact match first, then longest-prefix match. Skip stale litellm rows."""
    pricing = _load_db_pricing()
    if not pricing:
        return None

    # Exact match
    entry = pricing.get(model)
    if entry is not None:
        in_cost, out_cost, source, updated_at = entry
        if _is_usable(source, updated_at):
            return (in_cost, out_cost)

    # Longest-prefix match
    best_key: Optional[str] = None
    best_len = 0
    for key, entry in pricing.items():
        if model.startswith(key) and len(key) > best_len:
            in_cost, out_cost, source, updated_at = entry
            if _is_usable(source, updated_at):
                best_key = key
                best_len = len(key)

    if best_key is not None:
        return (pricing[best_key][0], pricing[best_key][1])
    return None


def _is_usable(source: str, updated_at: datetime) -> bool:
    """Return True if the row should be used (not stale)."""
    if source in ("seed", "manual"):
        return True
    # litellm-sourced: stale after _LITELLM_STALE_DAYS
    if source == "litellm":
        age_days = (datetime.now(timezone.utc) - updated_at).total_seconds() / 86400
        return age_days < _LITELLM_STALE_DAYS
    return True


# ---------------------------------------------------------------------------
# Tier 2 — litellm community JSON
# ---------------------------------------------------------------------------


def _fetch_litellm_json() -> Dict[str, Tuple[float, float]]:
    """Fetch and parse litellm pricing JSON. Cached 24h."""
    global _litellm_cache, _litellm_cache_ts

    now = time.monotonic()
    if _litellm_cache and (now - _litellm_cache_ts) < _LITELLM_CACHE_TTL:
        return _litellm_cache

    try:
        req = urllib.request.Request(_LITELLM_JSON_URL, headers={"User-Agent": "tarka-agent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        result: Dict[str, Tuple[float, float]] = {}
        for model_key, info in raw.items():
            if not isinstance(info, dict):
                continue
            in_per_tok = info.get("input_cost_per_token")
            out_per_tok = info.get("output_cost_per_token")
            if in_per_tok is None or out_per_tok is None:
                continue
            try:
                result[model_key] = (float(in_per_tok) * 1_000_000, float(out_per_tok) * 1_000_000)
            except (TypeError, ValueError):
                continue

        _litellm_cache = result
        _litellm_cache_ts = now
        logger.debug("Loaded %d models from litellm JSON", len(result))
        return _litellm_cache
    except Exception as exc:
        logger.debug("litellm JSON fetch failed: %s", exc)
        return _litellm_cache


def _litellm_lookup(model: str) -> Optional[Tuple[float, float]]:
    """Look up model in litellm JSON. If found, upsert into DB."""
    pricing = _fetch_litellm_json()
    if not pricing:
        return None

    entry = pricing.get(model)
    if entry is None:
        return None

    in_cost, out_cost = entry

    # Upsert into DB for future fast lookups
    _upsert_db(model, in_cost, out_cost, source="litellm")

    return (in_cost, out_cost)


def _upsert_db(
    model_pattern: str,
    input_cost_per_1m: float,
    output_cost_per_1m: float,
    *,
    source: str = "litellm",
    provider: Optional[str] = None,
) -> bool:
    """Insert or update a pricing row. Returns True on success."""
    dsn = _get_dsn()
    if not dsn:
        return False
    try:
        import psycopg  # type: ignore[import-not-found]

        with psycopg.connect(dsn) as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO llm_pricing (model_pattern, provider, input_cost_per_1m, output_cost_per_1m, source, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, now()) "
                    "ON CONFLICT (model_pattern) DO UPDATE SET "
                    "  provider = COALESCE(EXCLUDED.provider, llm_pricing.provider), "
                    "  input_cost_per_1m = EXCLUDED.input_cost_per_1m, "
                    "  output_cost_per_1m = EXCLUDED.output_cost_per_1m, "
                    "  source = EXCLUDED.source, "
                    "  updated_at = now()",
                    (model_pattern, provider, input_cost_per_1m, output_cost_per_1m, source),
                )
        invalidate_cache()
        return True
    except Exception as exc:
        logger.debug("llm_pricing upsert failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def invalidate_cache() -> None:
    """Reset in-memory caches so the next call re-fetches from DB."""
    global _db_cache_ts, _litellm_cache_ts
    _db_cache_ts = 0.0
    _litellm_cache_ts = 0.0


def estimate_cost(model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    """Return estimated USD cost, or None for unknown models / missing tokens."""
    if input_tokens is None or output_tokens is None:
        return None

    # Tier 1: DB (memory-cached)
    pricing = _match_db(model)

    # Tier 2: litellm JSON
    if pricing is None:
        pricing = _litellm_lookup(model)

    # Tier 3: unknown
    if pricing is None:
        logger.warning("No pricing data for model %r", model)
        return None

    input_cost_per_1m, output_cost_per_1m = pricing
    return (input_tokens * input_cost_per_1m + output_tokens * output_cost_per_1m) / 1_000_000
