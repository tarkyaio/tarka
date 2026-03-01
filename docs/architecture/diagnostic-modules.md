# Diagnostic modules (universal failure modes)

This agent is K8s-first and on-call-first. To stay **general-purpose** (not “a playbook per alertname”), the core abstraction is a **diagnostic module**: a portable unit that can collect evidence, form hypotheses, and suggest next-best tests.

## Why this exists

Alertnames and alert rule conventions differ across orgs. If we encode “one playbook per alertname,” the system becomes brittle and never finishes.

Instead, we target the small set of **universal failure modes** that explain most incidents:
- change-driven regressions (deploy/config)
- dependency failures (timeouts, DNS/certs)
- capacity/saturation (CPU/memory)
- K8s lifecycle issues (crashloop, image pull, scheduling)
- data-plane errors (5xx/latency spikes)
- control-plane issues (API/CNI/CSI)
- observability pipeline failures

## Core contract

The invariant remains: **`Investigation` is the single source of truth**.

A diagnostic module is responsible for:
- **Collect**: best-effort, read-only evidence gathering (K8s/PromQL/logs). Must not raise.
- **Diagnose**: produce evidence-cited hypotheses with confidence and next tests.
- **(Later) Act**: propose safe actions (never execute without approval and policy gates).

Code lives in:
- `agent/diagnostics/base.py` (module interface)
- `agent/diagnostics/registry.py` (registry)
- `agent/diagnostics/collect.py` (module-driven evidence collection)
- `agent/diagnostics/engine.py` (hypothesis aggregation/ranking)
- `agent/diagnostics/universal.py` (initial universal modules)

## Current family coverage (implemented now)

The module system currently covers the same family taxonomy used by deterministic scoring/enrichment (`agent/pipeline/families.py`):
- `crashloop`
- `pod_not_healthy`
- `cpu_throttling`
- `http_5xx`
- `oom_killed`
- `memory_pressure`
- `target_down`
- `k8s_rollout_health`
- `observability_pipeline`
- `meta`

Anything else is treated as `generic` and will fall back to compatibility collectors until a module/family mapping is added.

## How it runs in the pipeline

High-level:
- Build the investigation skeleton from the alert payload.
- Detect family (best-effort).
- **Collect evidence via diagnostic modules** (default path).
- Fallback: legacy alertname playbook routing if no module applies (for `generic`).
- Compute deterministic features/scoring/verdict.
- Run module diagnosis to populate `analysis.hypotheses`.

## How to extend (add a new universal failure mode)

1) Decide whether this is:
- a **new family** (needs changes in `agent/pipeline/families.py` and acceptance docs), or
- a **sub-mode** of an existing module/family (often best to start here).

2) Add a module or extend an existing one:
- Implement `applies()`, `collect()`, `diagnose()`.
- Keep `collect()` idempotent (don’t overwrite evidence that already exists).

3) Add tests:
- Unit tests for `applies()` and `diagnose()` outputs (hypothesis IDs, ordering).
- Pipeline test to ensure module collection happens and legacy fallback remains safe.

4) Update docs:
- Add/extend a family checklist in `docs/acceptance/families.md` when introducing a new family.

## Wider set of families (roadmap)

These are portable failure modes we can add incrementally:
- Scheduling/placement: FailedScheduling, quota exceeded, taints/tolerations/affinity mismatches
- Storage: PVC pending, mount/attach failures, CSI controller/node failures
- Network/DNS: CoreDNS issues, DNS latency, NetworkPolicy denies, CNI health
- Certificates/identity: cert expiry, x509 errors, IAM/OIDC permission failures
- Node health: NodeNotReady, kubelet down, disk pressure, eviction storms
- Control-plane: API server saturation/errors, admission webhook failures
- Queue/backpressure: consumer lag, queue depth, saturating workers (metric-driven)
