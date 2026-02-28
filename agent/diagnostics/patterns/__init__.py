"""Extensible pattern library for known failure modes.

This module aggregates all pattern sets (S3, RDS, ECR, app errors, etc.)
into a single registry that diagnostic modules can use.

Adding new pattern sets:
1. Create a new file (e.g., rds_patterns.py)
2. Define patterns using LogPattern
3. Export as a list (e.g., RDS_PATTERNS)
4. Import and add to ALL_PATTERNS below

Future pattern sets to add:
- rds_patterns.py: RDS connection errors, authentication failures
- ecr_patterns.py: ECR image pull errors, authentication, rate limits
- app_patterns.py: OOM, segfaults, panics, NPEs, stack overflows
- network_patterns.py: Connection timeouts, DNS failures, TLS errors
"""

from agent.diagnostics.patterns.crashloop_patterns import CRASHLOOP_PATTERNS
from agent.diagnostics.patterns.s3_patterns import S3_PATTERNS

# Future: Add more pattern sets
# from agent.diagnostics.patterns.rds_patterns import RDS_PATTERNS
# from agent.diagnostics.patterns.ecr_patterns import ECR_PATTERNS

# Aggregate all patterns
ALL_PATTERNS = [
    *S3_PATTERNS,
    *CRASHLOOP_PATTERNS,
    # *RDS_PATTERNS,  # Future
    # *ECR_PATTERNS,  # Future
]
