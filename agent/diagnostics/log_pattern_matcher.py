"""Generic log pattern matching framework (reusable across all diagnostic modules)."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class LogPattern:
    """Represents a known error pattern that can be matched against logs.

    This is the foundation for deterministic pattern-based diagnostics.
    Diagnostic modules use this to convert parsed log errors into specific hypotheses.
    """

    pattern_id: str
    """Unique identifier for this pattern (e.g., 's3_access_denied')"""

    title: str
    """Human-readable title for the failure mode"""

    patterns: List[str]
    """List of regex patterns to match against log text"""

    confidence: int
    """Confidence score 0-100 for this hypothesis when pattern matches"""

    why_template: str
    """Template string for the 'why' explanation (supports {field} interpolation)"""

    next_tests: List[str]
    """List of diagnostic commands to validate the hypothesis (supports {field} interpolation)

    These are investigation steps, not fixes. Use remediation_steps for actual fixes.
    """

    context_extractors: Dict[str, str]
    r"""Dict of {field_name: regex_pattern} to extract context from logs

    Example:
        {"bucket": r"bucket[:\s]+([a-z0-9.-]+)"}

    The regex should have one capture group that extracts the field value.
    """

    remediation_steps: List[str] = field(default_factory=list)
    """List of remediation actions to fix the issue (supports {field} interpolation)

    These are the actual fixes the user should perform, not diagnostic commands.
    Example: "Add s3:GetObject permission to IAM role" not "Check IAM role"
    """

    def matches(self, log_text: str) -> bool:
        """Check if any pattern matches the log text (case-insensitive)."""
        return any(re.search(p, log_text, re.IGNORECASE) for p in self.patterns)

    def extract_context(self, log_text: str) -> Dict[str, str]:
        """Extract context fields (bucket names, operations, etc.) from logs.

        Returns:
            Dict with extracted field values (e.g., {"bucket": "my-bucket", "operation": "HeadBucket"})
        """
        context = {}
        for field_name, pattern in self.context_extractors.items():
            match = re.search(pattern, log_text, re.IGNORECASE)
            if match:
                context[field_name] = match.group(1)
        return context


class LogPatternMatcher:
    """Matches parsed log errors against a library of known patterns.

    Usage:
        patterns = [S3_ACCESS_DENIED, S3_BUCKET_NOT_FOUND, ...]
        matcher = LogPatternMatcher(patterns)

        matches = matcher.find_matches(investigation.evidence.logs.parsed_errors)
        for pattern, context in matches:
            # Create hypothesis from pattern + context
            hypothesis = Hypothesis(
                hypothesis_id=pattern.pattern_id,
                title=pattern.title,
                confidence_0_100=pattern.confidence,
                why=[pattern.why_template.format(**context)],
                next_tests=[cmd.format(**context) for cmd in pattern.next_tests]
            )
    """

    def __init__(self, patterns: List[LogPattern]):
        """Initialize matcher with a list of patterns to check."""
        self.patterns = patterns

    def find_matches(self, parsed_errors: List[Dict]) -> List[Tuple[LogPattern, Dict[str, str]]]:
        """Match parsed errors against all patterns.

        Args:
            parsed_errors: List of parsed error dicts from evidence.logs.parsed_errors
                          Each dict has keys: message, severity, count, etc.

        Returns:
            List of (pattern, context) tuples for all matching patterns

        Example:
            [
                (S3_ACCESS_DENIED, {"bucket": "my-bucket", "operation": "HeadBucket"}),
                (IAM_ROLE_NOT_FOUND, {"role": "my-role"})
            ]
        """
        if not parsed_errors:
            return []

        # Combine all error messages into searchable text
        error_text = "\n".join(e.get("message", "") for e in parsed_errors)

        matches = []
        for pattern in self.patterns:
            if pattern.matches(error_text):
                context = pattern.extract_context(error_text)
                matches.append((pattern, context))

        return matches
