"""Log selection utilities (on-call UX).

This module centralizes:
- selecting a small, *actionable* snippet for reports
- selecting a single "best" line for verdict one-liners

Goals:
- Prefer real failure signals (ERROR/FATAL/PANIC, Traceback, stack traces).
- Avoid misleading config/banner noise (e.g., Spring banners, VictoriaLogs `_msg` warnings,
  or config keys like `exception.handler`).
- When a winning error line is found, include a small amount of surrounding context
  from the same log entry.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple


def _looks_like_startup_banner(line: str) -> bool:
    t = (line or "").strip()
    if not t:
        return False
    # Spring Boot-style ASCII art
    if "____" in t and ("|_|" in t or "___" in t):
        return True
    if t.startswith((" .   ____", "\\/  ___", " \\\\/  ___", " =========")):
        return True
    if ":: spring boot ::" in t.lower():
        return True
    return False


def _is_noise_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return True
    sl = s.lower()
    if _looks_like_startup_banner(s):
        return True
    if "missing _msg field" in sl:
        return True
    if "docs.victoriametrics.com/victorialogs/keyconcepts/#message-field" in s:
        return True
    return False


_CFG_KV_RE = re.compile(r"^\s*[\w.\-]+\s*=\s*.+$")


def _looks_like_config_noise(line: str) -> bool:
    """
    Detect config-ish lines that include scary words but are not errors.
    Example from Kafka streams:
      default.production.exception.handler = class ...DefaultProductionExceptionHandler
    """
    s = (line or "").strip()
    if not s:
        return False
    sl = s.lower()
    if "exception.handler" in sl:
        return True
    # "ExceptionHandler" class names are common config knobs; don't treat as a runtime exception.
    if "exceptionhandler" in sl and _CFG_KV_RE.match(s):
        return True
    # Generic key/value config pattern with "exception" token.
    if "exception" in sl and _CFG_KV_RE.match(s):
        return True
    return False


def _is_stack_continuation(line: str) -> bool:
    s = (line or "").rstrip("\r")
    if not s:
        return False
    if s.startswith("\tat ") or re.match(r"^\s+at\s+\S+", s):
        return True
    sl = s.lower()
    if sl.startswith("caused by:") or sl.startswith("suppressed:"):
        return True
    if re.match(r"^\s*\.\.\. \d+ more\s*$", s):
        return True
    return False


def _score_line(line: str) -> int:
    s = (line or "").strip()
    if not s:
        return 0
    if _is_noise_line(s):
        return 0
    # Config-ish "exception" lines are false positives; strongly demote.
    if _looks_like_config_noise(s):
        return 1

    sl = s.lower()

    # Strongest signals
    if re.search(r"\b(fatal|panic)\b", sl):
        return 110
    if re.search(r"\b(error)\b", sl) or re.match(r"^\s*error\b", sl):
        return 100
    if "traceback" in sl:
        return 100
    if re.match(r"^\s*exception(\b|:)", sl) or "exception:" in sl:
        return 95
    if "caused by:" in sl:
        return 92
    if _is_stack_continuation(s):
        return 70

    # Probe failures are high-signal for pod health/crashloop
    if "probe" in sl and "failed" in sl:
        return 90

    # Medium
    if re.search(r"\bwarn(ing)?\b", sl):
        return 20
    return 5


def _flatten_entries(entries: List[object]) -> List[Tuple[Optional[datetime], int, List[str]]]:
    """
    Flatten raw evidence entries into per-entry line lists.
    Returns tuples: (timestamp, entry_index, lines[])
    """
    out: List[Tuple[Optional[datetime], int, List[str]]] = []
    for i, e in enumerate(entries or []):
        if not isinstance(e, dict):
            continue
        ts = e.get("timestamp")
        ts_dt: Optional[datetime] = ts if isinstance(ts, datetime) else None
        msg = str(e.get("message") or "")
        lines = [ln.rstrip("\r") for ln in msg.splitlines() if ln.strip()]
        if lines:
            out.append((ts_dt, i, lines))
    return out


def select_best_line(entries: List[object]) -> Optional[str]:
    """
    Pick a single best log line for one-liners.
    Prefer the most recent high-signal line; fall back to the most recent non-noise line.
    """
    flat = _flatten_entries(entries)
    best: Optional[Tuple[float, int, int, str, int]] = None  # (ts_key, entry_i, line_i, line, score)

    for ts, entry_i, lines in flat:
        ts_key = ts.timestamp() if isinstance(ts, datetime) else 0.0
        for line_i, ln in enumerate(lines):
            s = (ln or "").strip()
            if _is_noise_line(s):
                continue
            score = _score_line(s)
            # Prefer score first; then recency.
            key = (ts_key, entry_i, line_i, s, score)
            if best is None:
                best = key
                continue
            # Compare: higher score wins; if tie, newer wins.
            if score > best[4] or (score == best[4] and (ts_key, entry_i, line_i) > (best[0], best[1], best[2])):
                best = key

    if best is None:
        return None
    line = best[3]
    if len(line) > 180:
        return line[:180]
    return line


def select_snippet_latest_error_with_context(
    entries: List[object], *, max_lines: int = 12, before: int = 1, after: int = 6
) -> List[str]:
    """
    Pick a small, actionable snippet:
    - choose the most recent high-signal line (ERROR/Traceback/etc.)
    - include a few surrounding lines from the same entry as context
    - otherwise, fall back to a small tail of non-noise lines
    """
    flat = _flatten_entries(entries)
    if not flat:
        return []

    # Find the most recent high-signal line.
    winner: Optional[Tuple[float, int, int]] = None  # (ts_key, entry_i, line_i)
    for ts, entry_i, lines in flat:
        ts_key = ts.timestamp() if isinstance(ts, datetime) else 0.0
        for line_i, ln in enumerate(lines):
            s = (ln or "").strip()
            if _is_noise_line(s):
                continue
            if _score_line(s) >= 90:
                if winner is None or (ts_key, entry_i, line_i) > winner:
                    winner = (ts_key, entry_i, line_i)

    chosen_lines: List[Tuple[Optional[datetime], str]] = []
    if winner is not None:
        _, win_entry_i, win_line_i = winner
        # Locate the winning entry in `flat`
        entry = next((x for x in flat if x[1] == win_entry_i), None)
        if entry is not None:
            ts, _, lines = entry
            lo = max(0, win_line_i - before)
            hi = min(len(lines), win_line_i + after + 1)
            window = lines[lo:hi]

            # Extend context for stack traces (keep contiguous stack frames after the error line).
            extra = []
            j = hi
            while j < len(lines) and len(window) + len(extra) < max_lines:
                if _is_stack_continuation(lines[j]) or lines[j].strip() == "":
                    extra.append(lines[j])
                    j += 1
                    continue
                break

            for ln in window + extra:
                s = (ln or "").strip()
                if not s or _is_noise_line(s):
                    continue
                chosen_lines.append((ts, s))

    if not chosen_lines:
        # Fallback: tail of non-noise lines across entries (most recent).
        tail: List[Tuple[Optional[datetime], str]] = []
        for ts, _, lines in flat:
            for ln in lines:
                s = (ln or "").strip()
                if not s or _is_noise_line(s) or _looks_like_config_noise(s):
                    continue
                tail.append((ts, s))
        if not tail:
            return []
        chosen_lines = tail[-max_lines:]

    # Format with HH:MM:SSZ prefix when timestamp available.
    out: List[str] = []
    for ts, ln in chosen_lines[:max_lines]:
        if isinstance(ts, datetime):
            try:
                tsu = ts.astimezone(timezone.utc)
                out.append(f"{tsu.strftime('%H:%M:%SZ')} {ln}")
            except Exception:
                out.append(ln)
        else:
            out.append(ln)
    return out
