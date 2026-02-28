"""Memory layer: incident store + skill library (optional, feature-flagged).

This package is intentionally dependency-light at import time. Postgres drivers are
imported lazily inside functions so the rest of the agent can run without DB access.
"""

from __future__ import annotations
