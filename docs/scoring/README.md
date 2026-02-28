# Scoring (how to interpret scores)

Scoring is intended to be **on-call helpful**, not “ML magic”. It summarizes what the report already says in the triage/enrichment sections.

## What scoring answers

The scoring system is designed to answer quickly and honestly:

1) **How bad is this if it’s real?** (Impact)
2) **Do we trust it’s real and correctly attributed?** (Confidence)
3) **Even if true, is it likely actionable for on-call right now?** (Noise)

These three axes roll up into a single **classification** (e.g., `actionable`, `informational`, `noisy`, `artifact`).

## Important principles (trust-first)

- **Scoring comes after triage**: base triage should remain useful even if scoring is missing or imperfect.
- **Missing evidence reduces confidence (and sometimes increases noise)**, but it should not “fake certainty”.
- **Blocked scenarios matter**: if scope/identity/K8s/logs are missing, scoring should reflect lower confidence and avoid urgent language.

## Read next

- **Scoring contract (authoritative)**: [`contract.md`](contract.md)
- **Golden corpus (calibration examples)**: [`golden_corpus.md`](golden_corpus.md)
