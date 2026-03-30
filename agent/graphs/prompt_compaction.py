"""Prompt size measurement for RCA graph observability."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def measure_prompt(prompt: str, label: str) -> int:
    """Log prompt size at INFO and return char count."""
    chars = len(prompt)
    est_tokens = chars // 4
    logger.info("prompt_size label=%s chars=%d est_tokens=%d", label, chars, est_tokens)
    return chars
