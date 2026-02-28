from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple


class RateLimiter:
    """
    Simple in-memory rate limiter for login attempts.

    Tracks failed login attempts per identifier (username or IP address).
    Rate limits after max_attempts within window_seconds.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        """
        Initialize rate limiter.

        Args:
            max_attempts: Maximum failed attempts before rate limiting (default: 5)
            window_seconds: Time window in seconds (default: 300 = 5 minutes)
        """
        self._attempts: Dict[str, List[datetime]] = defaultdict(list)
        self._max_attempts = max_attempts
        self._window = timedelta(seconds=window_seconds)

    def check_and_increment(self, identifier: str) -> Tuple[bool, int]:
        """
        Check if identifier is rate limited and increment attempt counter.

        Args:
            identifier: Unique identifier (username, IP, etc.)

        Returns:
            Tuple of (is_allowed, attempts_remaining)
            - is_allowed: True if request should be allowed, False if rate limited
            - attempts_remaining: Number of attempts remaining before rate limit
        """
        now = datetime.now()

        # Clean old attempts outside the window
        self._attempts[identifier] = [t for t in self._attempts[identifier] if now - t < self._window]

        # Check if rate limited
        if len(self._attempts[identifier]) >= self._max_attempts:
            return False, 0

        # Increment attempt counter
        self._attempts[identifier].append(now)
        remaining = self._max_attempts - len(self._attempts[identifier])
        return True, remaining

    def reset(self, identifier: str) -> None:
        """
        Reset attempts for an identifier (e.g., after successful login).

        Args:
            identifier: Unique identifier to reset
        """
        if identifier in self._attempts:
            del self._attempts[identifier]


# Global rate limiter instance
_global_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get global rate limiter instance."""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = RateLimiter(max_attempts=5, window_seconds=300)
    return _global_rate_limiter
