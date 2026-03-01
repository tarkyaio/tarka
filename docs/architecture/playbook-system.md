# Shared playbooks + enrichers (pod baseline)

This document describes the intended architecture for **shared playbooks** and **family enrichers**.

## Why

We want the agent to be:
- **clear**: one obvious place to understand “what evidence gets collected”
- **non-duplicative**: no double-fetching logs/metrics from multiple layers
- **on-call-first**: stable outputs, best-effort data gathering, and actionable next steps

## Definitions

- **Playbook**: collects evidence (K8s/PromQL/logs) and mutates `Investigation.evidence` best-effort.
- **Baseline playbook**: a shared collector used by multiple families.
- **Family add-on**: a small extra evidence collector (usually 1–2 extra queries) specific to a family.
- **Family enricher**: deterministic interpretation that populates `investigation.analysis.enrichment` (`label`, `why[]`, `next[]`).
- **Diagnostic module**: the primary abstraction for universal failure modes. A module owns (1) evidence collection (often by reusing playbook collectors), (2) diagnosis hypotheses, and (3) next-best tests (and later action proposals).

## Update: modules are now the default collection path

The system has pivoted toward **diagnostic modules** as the default evidence collector + diagnosis engine.

- Modules run first and collect evidence using shared collectors (baseline + add-ons).
- Legacy alertname-based playbook routing remains as a fallback for unsupported (`generic`) families.

See: [`diagnostic-modules.md`](diagnostic-modules.md).

## Target state (pod-scoped baseline in `agent/playbooks.py`)

### Shared pod baseline

Implement a shared `pod_baseline_playbook(investigation)` that gathers **full baseline** evidence:
- K8s context (pod info/conditions/events/owner chain/rollout status)
- Logs (best-effort)
- Restarts signal (PromQL)
- CPU usage/limits (PromQL)
- Memory usage/limits (PromQL)
- Pod phase signal (PromQL)

The baseline must be **idempotent** and **best-effort**:
- do not overwrite evidence that already exists
- never raise; append errors to `investigation.errors`

### Family playbooks become “baseline + add-on”

Examples:
- `cpu_throttling`: baseline + throttling query
- `oom_killed`: baseline + attach OOM hint (labels/annotations)
- `pod_not_healthy`: baseline (plus higher K8s events limit if needed)

### `default_playbook`

`default_playbook` becomes an alias for `pod_baseline_playbook` (pod-scoped).

## Signals layer rule

To avoid duplicate collection, `agent/signals.py` should be **thin**:
- keep only **non-pod-safe** fallbacks (e.g., deriving `http_5xx` from alert labels)
- do not perform pod baseline collection (logs/cpu/memory/restarts) if playbooks already do it

## Non-pod baseline (deferred)

Introduce as a separate shared playbook class for non-pod families (`target_down`, `k8s_rollout_health`, `observability_pipeline`, `meta`).

### Shared non-pod baseline (Prometheus + read-only K8s)

Implement a shared `nonpod_baseline_playbook(investigation)` that is safe for service/node/cluster/unknown targets:
- Prometheus best-effort baseline (derived from alert labels): `up{}` / scrape hints (when labels exist)
- Read-only Kubernetes API when identity exists:
  - if `investigation.target.workload_kind/name` exist (or can be inferred from alert labels), fetch workload rollout status and store into `investigation.evidence.k8s.rollout_status`

This baseline should never assume `namespace+pod` exist and should never emit “missing pod target” errors.
