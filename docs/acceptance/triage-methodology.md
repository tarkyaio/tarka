# Triage methodology

This project is built around one on-call-first idea:

> If critical evidence is missing, **don’t guess**. Be explicit about what’s unknown and provide the **fastest way to learn it**.

## Mental model (what happens when an alert comes in)

1) The agent receives an alert instance (CLI selection or Alertmanager webhook).
2) It constructs an **`Investigation`** (single source of truth for identity, evidence, analysis).
3) A playbook collects evidence best-effort (Prometheus + optional Kubernetes read-only + optional logs).
4) The agent renders a report that starts with base triage, then adds family-specific enrichment, then scoring (when present).

For the deeper architecture flow, see: [`investigation-pipeline.md`](../architecture/investigation-pipeline.md).

## The base triage contract: `analysis.decision`

The first thing in the report is a deterministic “base triage” block that is fully representable from the investigation:

> `analysis.decision = label + why + next`

This is designed to answer in <60 seconds:
**what broke, where, how bad, is it real, what do I do next**.

The contract and quality bar live here:
- [`base-contract.md`](base-contract.md)

### `decision.label` (one line)

A scan-friendly headline of the form:

- `<ScopeLabel> • Impact=<impact_label> • Discriminator=<discriminator_label>`

Examples:
- `Small • Impact=unknown • Discriminator=logs_missing`
- `Scope=unknown • Impact=unknown • Discriminator=blocked_prometheus_unavailable`

### `decision.why` (6–10 bullets)

“Trust + context” facts. Every bullet should be either:
- evidence-backed, or
- explicitly unknown/unavailable (and why)

### `decision.next` (3–7 actions)

Ordered by **highest signal / lowest effort**, and **copy/paste friendly**.

Guiding rule: prefer **PromQL-first** next steps (work even without cluster credentials), with optional `kubectl` fallbacks where appropriate.

## Blocked-mode scenarios (A–D)

When the agent is missing key inputs, it follows one or more scenarios rather than guessing. These are defined as acceptance specs with golden outputs.

### Scenario A: target identity missing

If the agent cannot confidently answer **what is affected** (missing stable identifiers), it does not guess.

- Goal: discover the dimension that matters (cluster/namespace/pod/service/instance/team) and route to the right owner.

### Scenario B: Kubernetes context missing

If the target is pod-scoped but K8s context cannot be fetched (pod/conditions/events unavailable), the report stays actionable using kube-state-metrics PromQL discriminators.

### Scenario C: logs missing (empty or unavailable)

If logs are unavailable or empty, the report should proceed with non-log discriminators and explain the difference between "empty" and "unavailable".

### Scenario D: Prometheus scope unavailable

If the agent cannot compute scope/blast radius, it must explicitly say **scope unknown** and ask the on-call to verify in Prometheus.

## How enrichment fits in

Base triage is family-agnostic. **Family enrichment** is additive and must not contradict base triage honesty about missing evidence.

See:
- Acceptance docs entry point: [`README.md`](README.md)
- Shared playbooks intent: [`playbook-system.md`](../architecture/playbook-system.md)
