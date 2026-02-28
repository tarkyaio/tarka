# Scoring + classification contract (on-call first)

This document defines the operational meaning of:
- the 3 deterministic scores: **Impact**, **Confidence**, **Noise**
- the deterministic **classification** in `analysis.verdict.classification`

The goal is to help an on-call engineer answer, quickly and honestly:
1) **How bad is this if it’s real?** (Impact)
2) **Do we trust that it’s real and correctly attributed?** (Confidence)
3) **Even if true, is this likely actionable for on-call right now?** (Noise)

## The 3 score axes (0–100)

### Impact (how bad if real)

Impact is about **severity of the underlying symptom** and **blast radius** (when we can estimate it).

- High impact examples:
  - rollout stuck for a core service
  - many targets down for a job used by critical alerting
  - OOM kills causing sustained crashloops
- Low impact examples:
  - a single scrape target down for a non-critical job
  - a single pod pending in a batch namespace

Impact **must not** be reduced just because we’re missing evidence. Missing evidence is a **confidence** (and sometimes **noise**) problem.

### Confidence (do we trust the attribution/reproduction)

Confidence is about how much we trust that:
- the symptom is real (not a false positive)
- the agent is looking at the right target (correct attribution)
- multiple independent signals agree (metrics + K8s status + events + logs, depending on family)

Confidence is penalized by:
- missing target identity (namespace/pod/workload/service)
- missing required context for the family (e.g., missing K8s context for pod failures)
- contradictions (alert says X but metrics/K8s strongly disagree)

### Noise (probability of not-actionable even if true)

Noise is about **on-call actionability**, not truth.

High noise examples:
- meta/inhibitor alerts that are not symptoms
- known-flappy signals that page but typically self-resolve
- high-cardinality alerts that fire for “expected churn” without an operator action

Low noise examples:
- strong symptoms with clear remediation hooks (rollout stuck, many targets down)

Noise can be reduced by **strong symptom evidence**, even if the alert is “chatty”.

## Classification (what the on-call should do with this)

Classification is a single label summarizing the relationship between the 3 axes. It is **not a paging policy** by itself.

### actionable

Meaning: **high impact likely**, **we trust the signal**, and **on-call can do something now**.

On-call interpretation: treat as an incident candidate. Follow base triage + enrichment next steps.

### informational

Meaning: either **low impact**, or **not enough evidence to recommend a strong action**, but also **not clearly noise**.

On-call interpretation: keep an eye; do lightweight confirmation; if correlated with customer impact, escalate to actionable investigation.

### noisy

Meaning: the symptom may be real, but **it’s likely not actionable for on-call** (flap, cardinality, meta, expected churn).

On-call interpretation: don’t burn time “debugging the system”; consider alert tuning/routing, and only investigate if independent impact signals exist.

### artifact (MUST NOT be ambiguous)

`artifact` is currently overloaded in many incident tools. We must make it unambiguous in the report output:

- **artifact_low_confidence**: likely false positive / wrong attribution / missing required evidence
- **artifact_recovered**: likely was real, but evidence indicates it is **not ongoing now** (recovered / self-healed / stale window)

Contract requirement:
- If classification is `artifact`, the verdict one-liner (and/or reason codes) must **explicitly say which**: recovered vs low-confidence.

## Relationship between scores and classification (policy)

We keep family-specific scoring profiles, but the *shape* should be consistent:

- `actionable` requires:
  - Impact high (strong symptom) AND
  - Confidence high (we trust it) AND
  - Noise not too high

- `noisy` requires:
  - Noise high AND/OR meta semantics, even if impact is high

- `artifact` requires:
  - either low-confidence, OR recovered/stale (must be explicit)

## Quality bar for verdict wording

Every verdict must be a single sentence that tells on-call:
- what we think is happening (symptom)
- whether it’s ongoing vs recovered (if relevant)
- what evidence drove that claim (at least 1 anchor)
