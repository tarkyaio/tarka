"""
Evidence collectors (idempotent, best-effort, read-only).

Collectors are the reusable building blocks for diagnostic modules and (legacy) playbooks.
They should:
- mutate Investigation.evidence in-place
- never raise (append errors to investigation.errors)
- avoid overwriting already-populated evidence (idempotent)
"""

from agent.collectors.nonpod_baseline import collect_nonpod_baseline
from agent.collectors.pod_baseline import collect_pod_baseline

__all__ = [
    "collect_pod_baseline",
    "collect_nonpod_baseline",
]
