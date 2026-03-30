"""Tests for agent.llm.pricing module."""

from agent.llm.pricing import estimate_cost


def test_known_model_cost():
    """Known model returns a positive cost."""
    cost = estimate_cost("gemini-2.5-flash", input_tokens=1000, output_tokens=500)
    assert cost is not None
    # Expected: (1000 * 0.15 + 500 * 0.60) / 1_000_000 = 0.00045
    assert abs(cost - 0.00045) < 1e-9


def test_unknown_model_returns_none():
    """Unknown model returns None."""
    cost = estimate_cost("unknown-model-xyz", input_tokens=1000, output_tokens=500)
    assert cost is None


def test_none_tokens_returns_none():
    """None tokens returns None."""
    assert estimate_cost("gemini-2.5-flash", input_tokens=None, output_tokens=500) is None
    assert estimate_cost("gemini-2.5-flash", input_tokens=1000, output_tokens=None) is None
    assert estimate_cost("gemini-2.5-flash", input_tokens=None, output_tokens=None) is None


def test_zero_tokens_returns_zero():
    """Zero tokens returns zero cost."""
    cost = estimate_cost("gemini-2.5-flash", input_tokens=0, output_tokens=0)
    assert cost is not None
    assert cost == 0.0


def test_anthropic_model_cost():
    """Anthropic model pricing works."""
    cost = estimate_cost("claude-3-5-sonnet-20241022", input_tokens=2000, output_tokens=1000)
    assert cost is not None
    # Expected: (2000 * 3.00 + 1000 * 15.00) / 1_000_000 = 0.021
    assert abs(cost - 0.021) < 1e-9


def test_prefix_match():
    """Models with version suffixes match via prefix."""
    # "gemini-2.5-flash-preview-123" should match "gemini-2.5-flash"
    cost = estimate_cost("gemini-2.5-flash-preview-123", input_tokens=1000, output_tokens=500)
    assert cost is not None
    assert abs(cost - 0.00045) < 1e-9
