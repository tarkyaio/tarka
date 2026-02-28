"""Tests for LLM timeout error detection and handling."""

from agent.llm.client import _classify_error


def test_timeout_error_classification_timeout_error_instance():
    """TimeoutError instances should be classified as 'timeout'."""
    error = TimeoutError("Request timed out")
    result = _classify_error(error, model="test-model")
    assert result == "timeout"


def test_timeout_error_classification_timeout_keyword():
    """Errors with 'TIMEOUT' keyword should be classified as 'timeout'."""
    error = Exception("Connection TIMEOUT after 120 seconds")
    result = _classify_error(error, model="test-model")
    assert result == "timeout"


def test_timeout_error_classification_timed_out_keyword():
    """Errors with 'TIMED OUT' keyword should be classified as 'timeout'."""
    error = Exception("Request timed out while waiting for response")
    result = _classify_error(error, model="test-model")
    assert result == "timeout"


def test_timeout_error_classification_deadline_exceeded():
    """Errors with 'DEADLINE_EXCEEDED' should be classified as 'deadline_exceeded'."""
    error = Exception("DEADLINE_EXCEEDED: RPC call exceeded deadline")
    result = _classify_error(error, model="test-model")
    assert result == "deadline_exceeded"


def test_timeout_error_classification_deadline_exceeded_spaced():
    """Errors with 'DEADLINE EXCEEDED' (spaced) should be classified as 'deadline_exceeded'."""
    error = Exception("DEADLINE EXCEEDED during API call")
    result = _classify_error(error, model="test-model")
    assert result == "deadline_exceeded"


def test_timeout_error_classification_http_408():
    """HTTP 408 errors should be classified as 'timeout'."""
    error = Exception("HTTP error 408: Request Timeout")
    result = _classify_error(error, model="test-model")
    assert result == "timeout"


def test_timeout_error_classification_http_408_space():
    """HTTP 408 errors with space should be classified as 'timeout'."""
    error = Exception("HTTP 408 Request Timeout")
    result = _classify_error(error, model="test-model")
    assert result == "timeout"


def test_timeout_error_classification_http_504():
    """HTTP 504 errors should be classified as 'gateway_timeout'."""
    error = Exception("HTTP error 504: Gateway Timeout")
    result = _classify_error(error, model="test-model")
    assert result == "gateway_timeout"


def test_timeout_error_classification_http_504_space():
    """HTTP 504 errors with space should be classified as 'gateway_timeout'."""
    error = Exception("HTTP 504 Gateway Timeout")
    result = _classify_error(error, model="test-model")
    assert result == "gateway_timeout"


def test_timeout_error_classification_case_insensitive():
    """Timeout detection should be case-insensitive."""
    error_lower = Exception("timeout occurred")
    error_upper = Exception("TIMEOUT OCCURRED")
    error_mixed = Exception("TimeOut Occurred")

    assert _classify_error(error_lower, model="test-model") == "timeout"
    assert _classify_error(error_upper, model="test-model") == "timeout"
    assert _classify_error(error_mixed, model="test-model") == "timeout"


def test_timeout_takes_precedence_over_other_errors():
    """Timeout patterns should be checked before other error patterns."""
    # Even if error contains other keywords, timeout should be detected first
    error = Exception("TIMEOUT: 403 PERMISSION_DENIED")
    result = _classify_error(error, model="test-model")
    assert result == "timeout"


def test_non_timeout_errors_still_classified():
    """Non-timeout errors should still be classified correctly."""
    # Test that existing error classification still works
    error_403 = Exception("403 Forbidden")
    error_401 = Exception("401 Unauthorized")
    error_429 = Exception("429 Rate Limited")

    assert _classify_error(error_403, model="test-model") == "permission_denied"
    assert _classify_error(error_401, model="test-model") == "unauthenticated"
    assert _classify_error(error_429, model="test-model") == "rate_limited"


def test_generic_error_classification():
    """Unknown errors should fall back to generic classification."""
    error = Exception("Some unknown error")
    result = _classify_error(error, model="test-model")
    assert result == "llm_error:Exception"


def test_timeout_with_context_message():
    """Timeout errors with additional context should still be detected."""
    error = Exception("Failed to complete request: Connection timed out after 180 seconds while calling API endpoint")
    result = _classify_error(error, model="test-model")
    assert result == "timeout"


def test_deadline_with_context_message():
    """Deadline errors with additional context should still be detected."""
    error = Exception("Request failed: DEADLINE_EXCEEDED - RPC took longer than allowed deadline of 120s")
    result = _classify_error(error, model="test-model")
    assert result == "deadline_exceeded"
