# Adding playbooks (and refining triage)

> Note: the agent is pivoting toward **diagnostic modules** (universal failure modes) as the core abstraction.
> Playbooks remain useful as evidence collectors, but new “general” behavior should prefer adding/extending a diagnostic module.
> See: [`architecture/diagnostic_modules.md`](architecture/diagnostic_modules.md).

This repo is designed so you can add investigation depth without breaking the base “on-call trust” contract.

## Where to start

- **Docs portal**: [`docs/README.md`](README.md)
- **Triage methodology** (the quality bar): [`triage_methodology.md`](triage_methodology.md)
- **Acceptance criteria**: [`report_acceptance/README.md`](report_acceptance/README.md)

## The intended split of responsibilities

### Playbook = evidence collection (best-effort)

A playbook’s job is to populate `Investigation.evidence` using external systems:
- Prometheus (PromQL / MetricsQL)
- Kubernetes API (read-only)
- Logs backend (best-effort)

Rules:
- **Best-effort**: never fail the whole report if one source is down.
- **Idempotent**: don’t overwrite evidence that already exists unless you mean to.
- **Honest errors**: record failures in the investigation so the report can state what’s missing and why.

### Enricher = deterministic interpretation + next steps

An enricher should only transform existing evidence into:

- a compact label (“what’s most likely going on”)
- a short list of why-bullets backed by evidence
- next steps (PromQL-first, plus optional kubectl fallback)

It should not perform external calls. It should never contradict base triage honesty.

## Where playbooks live (in code)

- Routing and playbook registration: `agent/playbooks.py`
- Base triage contract and scenarios: `docs/report_acceptance/` (specs), and the code that populates `analysis.decision`
- Common evidence helpers:
  - Kubernetes context: `agent/k8s_context.py`
  - Prometheus queries: `agent/prometheus.py`
  - Logs backend: `agent/logs_victorialogs.py` (and `agent/loki.py` where applicable)

## A practical “add a new playbook” workflow

### 1) Pick the alert family

Start with your highest-cost paging alerts (high frequency or high cognitive load). For each family, define:
- target type (pod/workload/service/node/cluster/unknown)
- the top 2–3 discriminators you want the report to surface
- the “first command” you want on-call to run if the agent is blocked

### 2) Define the quality bar (before code)

Write down what “good” looks like in terms of base triage + enrichment:

- Base triage must still be valid under Scenarios A–D
- Enrichment should add concrete, evidence-backed discriminators
- Next steps should be copy/paste friendly and ordered by signal/effort

The base contract lives here: [`report_acceptance/base_checklist.md`](report_acceptance/base_checklist.md)

### 3) Implement evidence collection (playbook)

In `agent/playbooks.py`:

- route your `alertname`(s) to a new playbook
- collect only what you need; prefer shared baselines if/when present
- ensure you record evidence availability status (ok/empty/unavailable) rather than silently skipping

### 4) Implement deterministic enrichment

Interpret the evidence you collected into a small, stable output. Keep it boring and predictable:

- no guessing
- no external calls
- make unknowns explicit

### 5) Add/extend tests

The repo has a strong test suite under `tests/`.

When adding a new family or changing triage behavior, add tests that lock in:

- deterministic base triage behavior under blocked scenarios
- the key discriminators for your family (golden outputs)
- regression coverage for parsing/label extraction where relevant

## Definition of done (DoD) checklist

A new playbook/enricher is “done” when:

- [ ] **Base triage remains honest**: unknowns are explicit; no invented scope/impact/identity
- [ ] **Scenario coverage**: A–D produce sensible `decision.label/why/next`
- [ ] **Evidence status is explicit**: k8s/metrics/logs show ok vs unavailable vs empty
- [ ] **PromQL-first next steps** are present, with optional `kubectl` fallback
- [ ] **No duplicated collection**: avoid fetching the same baseline evidence in multiple places
- [ ] **Tests added/updated** to cover the new family and blocked behaviors

## Suggested next improvement (for maintainability)

If you find yourself duplicating “pod baseline” queries (K8s context + restarts + cpu/memory + logs), consider moving toward the shared playbook approach described here:

- [`architecture/shared_playbooks.md`](architecture/shared_playbooks.md)
