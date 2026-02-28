"""Resolve placeholders in commands with actual values from evidence."""

import re
from typing import Dict, Optional

from agent.core.models import Investigation


class PlaceholderResolver:
    def __init__(self, investigation: Investigation):
        self.investigation = investigation
        self.values = self._extract_values()

    def _extract_values(self) -> Dict[str, str]:
        """Extract actual values from investigation evidence."""
        values = {}

        # Service account name
        sa_name = getattr(self.investigation.evidence.k8s, "service_account_name", None)
        if sa_name:
            values["sa_name"] = sa_name
            values["unknown"] = sa_name  # Replace literal "unknown"

        # Namespace
        ns = self.investigation.target.namespace
        if ns:
            values["namespace"] = ns

        # IAM role ARN (from pod annotations - requires additional K8s call)
        # For now, we don't fetch this; the enrichment could populate it if needed

        # Bucket name from logs
        bucket = self._extract_bucket_name()
        if bucket:
            values["bucket_name"] = bucket
            values["ERROR"] = bucket  # Replace literal "ERROR"

        return values

    def _extract_bucket_name(self) -> Optional[str]:
        """Extract S3 bucket name from log errors."""
        logs_evidence = self.investigation.evidence.logs
        if not logs_evidence or not logs_evidence.logs:
            return None

        for line in logs_evidence.logs[:20]:
            content = line.get("content", "") if isinstance(line, dict) else str(line)

            # Try multiple patterns to extract bucket name from S3 errors
            # More specific patterns first to avoid false matches
            patterns = [
                r"bucket region for\s+([a-z0-9.-]+)",  # "Failed to get bucket region for bucket-name"
                r"s3://([a-z0-9.-]+)",  # s3://bucket-name
                r"HeadBucket.*?bucket[=\s]+['\"]?([a-z0-9.-]+)",  # HeadBucket operation with bucket=name
                r"for\s+([a-z0-9.-]+):\s+An error occurred",  # "for bucket-name: An error occurred"
                r"bucket[:\s]+['\"]?([a-z0-9.-]+)['\"]?",  # "bucket: my-bucket" or "bucket 'my-bucket'"
            ]

            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    bucket = match.group(1)
                    # Filter out common false positives
                    if bucket not in ("error", "unknown", "none", "null"):
                        return bucket

        return None

    def resolve(self, command: str) -> str:
        """Replace placeholders in command with actual values."""
        result = command

        # Replace known placeholders
        for placeholder, value in self.values.items():
            # Replace as whole word (not part of other words)
            result = re.sub(r"\b" + re.escape(placeholder) + r"\b", value, result, flags=re.IGNORECASE)

        # Don't add newlines - markdown formatter can't handle multi-line commands in code blocks
        # Placeholders like <ROLE_NAME> are self-explanatory in context

        return result
