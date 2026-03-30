"""Tests for agent.llm.pricing module — covers all 3 tiers."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from agent.llm.pricing import (
    estimate_cost,
    invalidate_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_row(model, in_cost, out_cost, source="seed", age_days=0):
    """Return a dict entry matching the _db_cache shape."""
    updated = datetime.now(timezone.utc) - timedelta(days=age_days)
    return (in_cost, out_cost, source, updated)


def _patch_db_cache(entries):
    """Patch the module-level DB cache with given entries and reset timestamp."""
    return patch.multiple(
        "agent.llm.pricing",
        _db_cache=entries,
        _db_cache_ts=time.monotonic(),
    )


def _patch_litellm_cache(entries):
    """Patch the module-level litellm cache."""
    return patch.multiple(
        "agent.llm.pricing",
        _litellm_cache=entries,
        _litellm_cache_ts=time.monotonic(),
    )


def _patch_no_db():
    """Patch _get_dsn to return None (no Postgres)."""
    return patch("agent.llm.pricing._get_dsn", return_value=None)


# ---------------------------------------------------------------------------
# Tier 1 — DB pricing
# ---------------------------------------------------------------------------


class TestTier1DB:
    """DB-backed pricing with exact and prefix matching."""

    def setup_method(self):
        invalidate_cache()

    def test_exact_match(self):
        db = {"gemini-2.5-flash": _make_db_row("gemini-2.5-flash", 0.15, 0.60)}
        with _patch_db_cache(db), _patch_no_db():
            cost = estimate_cost("gemini-2.5-flash", input_tokens=1000, output_tokens=500)
        assert cost is not None
        assert abs(cost - 0.00045) < 1e-9

    def test_prefix_match(self):
        db = {"claude-sonnet": _make_db_row("claude-sonnet", 3.00, 15.00)}
        with _patch_db_cache(db), _patch_no_db():
            cost = estimate_cost("claude-sonnet-4-20250514", input_tokens=2000, output_tokens=1000)
        assert cost is not None
        # (2000 * 3.00 + 1000 * 15.00) / 1_000_000 = 0.021
        assert abs(cost - 0.021) < 1e-9

    def test_longest_prefix_wins(self):
        db = {
            "claude": _make_db_row("claude", 1.00, 5.00),
            "claude-sonnet": _make_db_row("claude-sonnet", 3.00, 15.00),
        }
        with _patch_db_cache(db), _patch_no_db():
            cost = estimate_cost("claude-sonnet-4-20250514", input_tokens=1000, output_tokens=0)
        # Should match "claude-sonnet" (longer prefix), not "claude"
        assert cost is not None
        assert abs(cost - 0.003) < 1e-9  # 1000 * 3.00 / 1M

    def test_none_tokens_returns_none(self):
        db = {"gemini-2.5-flash": _make_db_row("gemini-2.5-flash", 0.15, 0.60)}
        with _patch_db_cache(db), _patch_no_db():
            assert estimate_cost("gemini-2.5-flash", input_tokens=None, output_tokens=500) is None
            assert estimate_cost("gemini-2.5-flash", input_tokens=1000, output_tokens=None) is None

    def test_zero_tokens_returns_zero(self):
        db = {"gemini-2.5-flash": _make_db_row("gemini-2.5-flash", 0.15, 0.60)}
        with _patch_db_cache(db), _patch_no_db():
            cost = estimate_cost("gemini-2.5-flash", input_tokens=0, output_tokens=0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Tier 2 — litellm JSON fallback
# ---------------------------------------------------------------------------


class TestTier2Litellm:
    """Fallback to litellm community pricing JSON."""

    def setup_method(self):
        invalidate_cache()

    def test_litellm_lookup_returns_cost(self):
        litellm_data = {
            "claude-sonnet-4-20250514": (3.00, 15.00),
        }
        with _patch_db_cache({}), _patch_no_db(), _patch_litellm_cache(litellm_data):
            cost = estimate_cost("claude-sonnet-4-20250514", input_tokens=2000, output_tokens=1000)
        assert cost is not None
        assert abs(cost - 0.021) < 1e-9

    def test_litellm_upserts_to_db(self):
        litellm_data = {
            "gpt-4o": (5.00, 15.00),
        }
        with _patch_db_cache({}), _patch_litellm_cache(litellm_data), patch(
            "agent.llm.pricing._upsert_db"
        ) as mock_upsert, _patch_no_db():
            cost = estimate_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        assert cost is not None
        mock_upsert.assert_called_once_with("gpt-4o", 5.00, 15.00, source="litellm")


# ---------------------------------------------------------------------------
# Stale refresh
# ---------------------------------------------------------------------------


class TestStaleRefresh:
    """litellm-sourced DB rows older than 30 days should fall through to Tier 2."""

    def setup_method(self):
        invalidate_cache()

    def test_stale_litellm_row_falls_through(self):
        # DB has a litellm row that's 31 days old
        db = {
            "claude-sonnet-4-20250514": _make_db_row(
                "claude-sonnet-4-20250514", 2.50, 12.00, source="litellm", age_days=31
            ),
        }
        litellm_data = {
            "claude-sonnet-4-20250514": (3.00, 15.00),
        }
        with _patch_db_cache(db), _patch_litellm_cache(litellm_data), _patch_no_db():
            cost = estimate_cost("claude-sonnet-4-20250514", input_tokens=2000, output_tokens=1000)
        # Should use litellm price (3.00/15.00), not stale DB (2.50/12.00)
        assert cost is not None
        assert abs(cost - 0.021) < 1e-9

    def test_fresh_litellm_row_used(self):
        # DB has a litellm row that's 5 days old — should be used
        db = {
            "claude-sonnet-4-20250514": _make_db_row(
                "claude-sonnet-4-20250514", 2.50, 12.00, source="litellm", age_days=5
            ),
        }
        with _patch_db_cache(db), _patch_no_db():
            cost = estimate_cost("claude-sonnet-4-20250514", input_tokens=2000, output_tokens=1000)
        # Should use the DB row (2.50/12.00)
        assert cost is not None
        expected = (2000 * 2.50 + 1000 * 12.00) / 1_000_000
        assert abs(cost - expected) < 1e-9


# ---------------------------------------------------------------------------
# Manual override
# ---------------------------------------------------------------------------


class TestManualOverride:
    """source='manual' rows should never be skipped regardless of age."""

    def setup_method(self):
        invalidate_cache()

    def test_manual_never_stale(self):
        db = {
            "custom-model": _make_db_row("custom-model", 10.00, 50.00, source="manual", age_days=365),
        }
        with _patch_db_cache(db), _patch_no_db():
            cost = estimate_cost("custom-model", input_tokens=1000, output_tokens=500)
        assert cost is not None
        expected = (1000 * 10.00 + 500 * 50.00) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_seed_never_stale(self):
        db = {
            "claude-opus": _make_db_row("claude-opus", 15.00, 75.00, source="seed", age_days=999),
        }
        with _patch_db_cache(db), _patch_no_db():
            cost = estimate_cost("claude-opus-20240229", input_tokens=1000, output_tokens=500)
        assert cost is not None
        expected = (1000 * 15.00 + 500 * 75.00) / 1_000_000
        assert abs(cost - expected) < 1e-9


# ---------------------------------------------------------------------------
# Tier 3 — unknown model
# ---------------------------------------------------------------------------


class TestTier3Unknown:
    """When both DB and litellm miss, return None."""

    def setup_method(self):
        invalidate_cache()

    def test_unknown_model_returns_none(self):
        with _patch_db_cache({}), _patch_litellm_cache({}), _patch_no_db():
            cost = estimate_cost("nonexistent-model-xyz", input_tokens=1000, output_tokens=500)
        assert cost is None

    def test_litellm_fetch_failure_returns_none(self):
        with _patch_db_cache({}), _patch_litellm_cache({}), _patch_no_db():
            cost = estimate_cost("unknown-model", input_tokens=1000, output_tokens=500)
        assert cost is None


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


class TestCacheInvalidation:
    """invalidate_cache() resets in-memory caches."""

    def test_invalidate_resets_timestamps(self):
        import agent.llm.pricing as mod

        mod._db_cache_ts = time.monotonic()
        mod._litellm_cache_ts = time.monotonic()

        invalidate_cache()

        assert mod._db_cache_ts == 0.0
        assert mod._litellm_cache_ts == 0.0
