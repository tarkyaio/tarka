"""Flexible pattern matching utilities."""

import re
from typing import List


def match_patterns(text: str, patterns: List[str], match_type: str) -> bool:
    """
    Check if text matches any pattern.

    Args:
        text: Text to search in
        patterns: List of patterns to match against
        match_type: One of 'exact', 'substring', 'regex', 'semantic'

    Returns:
        True if any pattern matches, False otherwise
    """
    if not text or not patterns:
        return False

    text_lower = text.lower()

    for pattern in patterns:
        if match_type == "exact":
            if text_lower == pattern.lower():
                return True
        elif match_type == "substring":
            if pattern.lower() in text_lower:
                return True
        elif match_type == "regex":
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return True
        elif match_type == "semantic":
            # Future: use embeddings for semantic similarity
            # For now, fall back to substring matching
            if pattern.lower() in text_lower:
                return True

    return False
