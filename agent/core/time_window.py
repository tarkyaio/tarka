"""Shared time window parsing utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple


def parse_time_window(time_window: str) -> Tuple[datetime, datetime]:
    """
    Parse time window string (e.g., '1h', '30m', '2h30m') into start and end times.

    Returns:
        (start_time, end_time) where end_time is now (UTC, timezone-aware).
    """
    end_time = datetime.now(timezone.utc)

    hours = 0
    minutes = 0

    if "h" in time_window:
        parts = time_window.split("h")
        hours = int(parts[0])
        if len(parts) > 1 and parts[1]:
            minutes = int(parts[1].replace("m", ""))
    elif "m" in time_window:
        minutes = int(time_window.replace("m", ""))
    else:
        raise ValueError(f"Invalid time window format: {time_window}")

    start_time = end_time - timedelta(hours=hours, minutes=minutes)
    return start_time, end_time
