from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ParsedSearchQuery:
    """
    Hybrid search query:
    - key:value filters (normalized keys -> list of values)
    - free-text tokens (already split; caller decides AND/OR semantics)
    """

    filters: Dict[str, List[str]]
    tokens: List[str]


_KEY_ALIASES: Dict[str, str] = {
    # Namespace
    "ns": "namespace",
    "namespace": "namespace",
    # Pod
    "pod": "pod",
    # Workload / deployment
    "deploy": "workload",
    "deployment": "workload",
    "workload": "workload",
    # Service
    "svc": "service",
    "service": "service",
    # Cluster
    "cluster": "cluster",
    # Alertname
    "alert": "alertname",
    "alertname": "alertname",
}


def _normalize_key(k: str) -> Optional[str]:
    kk = (k or "").strip().lower()
    if not kk:
        return None
    return _KEY_ALIASES.get(kk)


def _consume_quoted(s: str, i: int) -> Tuple[Optional[str], int]:
    """
    Consume a quoted value starting at index i (s[i] is quote char).
    Returns (value, next_index).
    """
    if i >= len(s):
        return None, i
    q = s[i]
    if q not in ("'", '"'):
        return None, i
    i += 1
    out: List[str] = []
    while i < len(s):
        ch = s[i]
        if ch == q:
            return "".join(out), i + 1
        # minimal escape handling for \" and \'
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in ("\\", q):
            out.append(s[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    # Unterminated quote: treat as no-quote and let caller fall back.
    return None, i


def parse_search_query(q: str) -> ParsedSearchQuery:
    """
    Parse a hybrid search string into filters and tokens.

    Supported:
    - key:value (e.g., ns:payments pod:api-123)
    - quoted values (e.g., ns:\"payments prod\")
    - free text tokens (split on whitespace)

    Notes:
    - Keys are normalized via aliases.
    - Unknown keys are treated as plain tokens (the whole `k:v` is tokenized).
    """
    s = (q or "").strip()
    filters: Dict[str, List[str]] = {}
    tokens: List[str] = []

    i = 0
    n = len(s)

    def _push_filter(key: str, val: str) -> None:
        v = (val or "").strip()
        if not v:
            return
        filters.setdefault(key, []).append(v)

    def _push_token(tok: str) -> None:
        t = (tok or "").strip()
        if not t:
            return
        tokens.append(t)

    while i < n:
        # skip whitespace
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break

        # Read a "word" up to whitespace, but allow key:value with quoted value.
        # First read potential key
        start = i
        while i < n and not s[i].isspace() and s[i] != ":":
            i += 1

        # If this isn't key:value, treat as token word (maybe quoted)
        if i >= n or s[i] != ":":
            # handle quoted token
            if s[start] in ("'", '"'):
                v, j = _consume_quoted(s, start)
                if v is not None:
                    _push_token(v)
                    i = j
                    continue
            # consume until whitespace
            while i < n and not s[i].isspace():
                i += 1
            _push_token(s[start:i])
            continue

        # We have something like key:
        raw_key = s[start:i]
        norm_key = _normalize_key(raw_key)
        i += 1  # skip ':'
        if i >= n:
            # trailing "k:" -> ignore
            break

        # Parse value (quoted or until whitespace)
        if s[i] in ("'", '"'):
            v, j = _consume_quoted(s, i)
            if v is not None and norm_key:
                _push_filter(norm_key, v)
                i = j
                continue
            # quote couldn't be consumed; fall back to raw tokenization

        v_start = i
        while i < n and not s[i].isspace():
            i += 1
        raw_val = s[v_start:i]

        if norm_key:
            _push_filter(norm_key, raw_val)
        else:
            # unknown key => treat whole thing as a token
            _push_token(f"{raw_key}:{raw_val}")

    return ParsedSearchQuery(filters=filters, tokens=tokens)
