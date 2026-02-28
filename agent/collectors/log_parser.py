"""
Deterministic log parsing for ERROR/FATAL/Exception patterns.

Pure pattern matching module - no LLM required. Provides structured findings
that can be used for:
- Base triage analysis
- Scoring impact/confidence
- Report generation
"""

import re
from typing import Any, Dict, List, Literal

# Severity patterns (ordered by priority)
SEVERITY_PATTERNS = [
    # FATAL/CRITICAL patterns (highest priority)
    (re.compile(r"\b(FATAL|Fatal|fatal|CRITICAL|Critical|critical)\b"), "FATAL"),
    # Exception/panic patterns
    (re.compile(r"\b(Exception|exception|EXCEPTION|Traceback|panic:|PANIC)\b"), "EXCEPTION"),
    # Error patterns
    (re.compile(r"\b(ERROR|Error|error)\b"), "ERROR"),
]

# Common exception patterns for better extraction
EXCEPTION_INDICATORS = [
    r"Exception:",
    r"Traceback \(most recent call last\)",
    r"panic:",
    r"at [a-zA-Z0-9_\.]+\.[a-zA-Z0-9_]+\(",  # Java stack traces
    r"^\s+at\s+",  # Java/Python stack trace lines
    r"Caused by:",
    r"java\.[a-zA-Z0-9\.]+Exception",
]


def parse_log_entries(
    log_entries: List[Dict[str, Any]], *, limit: int = 50, max_message_length: int = 500
) -> Dict[str, Any]:
    """
    Parse log entries for ERROR/FATAL/Exception patterns.

    Args:
        log_entries: List of log entry dicts with 'message' or '_msg' field
        limit: Maximum number of parsed errors to return
        max_message_length: Truncate messages longer than this

    Returns:
        {
            "parsed_errors": [
                {
                    "timestamp": str (if available),
                    "severity": "ERROR" | "FATAL" | "EXCEPTION",
                    "message": str (truncated),
                    "pattern_matched": str,
                    "line_number": int (0-indexed position in input),
                }
            ],
            "metadata": {
                "total_lines": int,
                "error_count": int,
                "fatal_count": int,
                "exception_count": int,
                "unique_patterns": List[str],
            }
        }
    """
    parsed_errors = []
    stats = {
        "total_lines": len(log_entries),
        "error_count": 0,
        "fatal_count": 0,
        "exception_count": 0,
        "unique_patterns": set(),
    }

    for idx, entry in enumerate(log_entries):
        if len(parsed_errors) >= limit:
            break

        # Extract message from various log formats
        message = _extract_message(entry)
        if not message:
            continue

        # Try to match severity patterns
        severity, pattern = _classify_severity(message)
        if severity:
            # Extract timestamp if available
            timestamp = _extract_timestamp(entry)

            # Truncate message if too long
            truncated_message = message[:max_message_length]
            if len(message) > max_message_length:
                truncated_message += "... (truncated)"

            parsed_errors.append(
                {
                    "timestamp": timestamp,
                    "severity": severity,
                    "message": truncated_message,
                    "pattern_matched": pattern,
                    "line_number": idx,
                }
            )

            # Update stats
            if severity == "ERROR":
                stats["error_count"] += 1
            elif severity == "FATAL":
                stats["fatal_count"] += 1
            elif severity == "EXCEPTION":
                stats["exception_count"] += 1

            stats["unique_patterns"].add(pattern)

    return {
        "parsed_errors": parsed_errors,
        "metadata": {
            **stats,
            "unique_patterns": sorted(stats["unique_patterns"]),
        },
    }


def _extract_message(entry: Dict[str, Any]) -> str:
    """Extract message text from log entry dict."""
    # Try common message field names
    for field in ["message", "_msg", "msg", "log", "text"]:
        if field in entry:
            val = entry[field]
            if isinstance(val, str):
                return val
            elif isinstance(val, (list, tuple)) and val:
                return str(val[0])

    # Fallback: stringify entire entry
    return str(entry)


def _extract_timestamp(entry: Dict[str, Any]) -> str:
    """Extract timestamp from log entry if available."""
    for field in ["timestamp", "_time", "time", "@timestamp", "ts"]:
        if field in entry:
            return str(entry[field])
    return ""


def _classify_severity(message: str) -> tuple[Literal["ERROR", "FATAL", "EXCEPTION"] | None, str]:
    """
    Classify log message severity based on patterns.

    Returns:
        (severity, pattern_matched) or (None, "") if no match
    """
    # Try each severity pattern in priority order
    for pattern, severity in SEVERITY_PATTERNS:
        match = pattern.search(message)
        if match:
            return severity, match.group(0)

    return None, ""


def summarize_parsed_errors(parsed_errors: List[Dict[str, Any]], top_n: int = 5) -> str:
    """
    Generate a human-readable summary of parsed errors for reports.

    Args:
        parsed_errors: List of parsed error dicts
        top_n: Number of top errors to include in summary

    Returns:
        Markdown-formatted summary string
    """
    if not parsed_errors:
        return "No ERROR/FATAL/Exception patterns found in logs."

    lines = []
    lines.append(f"Found {len(parsed_errors)} error patterns:")

    # Group by severity
    by_severity = {"FATAL": [], "EXCEPTION": [], "ERROR": []}
    for err in parsed_errors:
        sev = err.get("severity", "ERROR")
        by_severity[sev].append(err)

    # Show counts
    for sev in ["FATAL", "EXCEPTION", "ERROR"]:
        count = len(by_severity[sev])
        if count > 0:
            lines.append(f"- {count} {sev} patterns")

    # Show top N examples
    lines.append(f"\nTop {min(top_n, len(parsed_errors))} examples:")
    for err in parsed_errors[:top_n]:
        sev = err.get("severity", "?")
        msg = err.get("message", "")
        ts = err.get("timestamp", "")

        # Format timestamp
        ts_str = f" [{ts}]" if ts else ""

        # Truncate message for summary
        if len(msg) > 150:
            msg = msg[:150] + "..."

        lines.append(f"- [{sev}]{ts_str} {msg}")

    return "\n".join(lines)
