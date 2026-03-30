"""LLM token pricing lookup (cost per 1M tokens, USD)."""

from __future__ import annotations

from typing import Optional

# (input_cost_per_1M, output_cost_per_1M)
_PRICING = {
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-2.5-pro": (1.25, 10.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-3-opus-20240229": (15.00, 75.00),
}


def estimate_cost(model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    """Return estimated USD cost, or None for unknown models / missing tokens."""
    if input_tokens is None or output_tokens is None:
        return None

    # Exact match first
    pricing = _PRICING.get(model)

    # Prefix match fallback
    if pricing is None:
        for key, val in _PRICING.items():
            if model.startswith(key):
                pricing = val
                break

    if pricing is None:
        return None

    input_cost_per_1m, output_cost_per_1m = pricing
    return (input_tokens * input_cost_per_1m + output_tokens * output_cost_per_1m) / 1_000_000
