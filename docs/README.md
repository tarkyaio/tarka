# Tarka Documentation

**Quick navigation**: If you're new, start with [Quickstart](guides/quickstart.md) → [One Pager](one-pager.md) → [Triage Methodology](acceptance/triage-methodology.md).

---

## Getting Started

- **[Quickstart Guide](guides/quickstart.md)** - First investigation in 5 minutes
- **[One Pager](one-pager.md)** - Leadership overview (why this exists)
- **[Environment Variables](guides/environment-variables.md)** - Configuration reference

## Operating the Agent

- **[Deployment Guide](guides/deployment.md)** - Kubernetes deployment
- **[Operations](guides/operations.md)** - Webhook setup, smoke testing, troubleshooting
- **[Testing](guides/testing.md)** - Running unit and integration tests
- **[UI Styles](guides/ui-styles.md)** - Console UI styling guidelines

## Architecture

- **[Architecture Overview](architecture/README.md)** - System design and key abstractions
- **[Investigation Pipeline](architecture/investigation-pipeline.md)** - How investigations work (SSOT, 11 stages)
- **[Diagnostic Modules](architecture/diagnostic-modules.md)** - Universal failure mode detection
- **[Playbook System](architecture/playbook-system.md)** - Alert routing and evidence collection

## Extending the Agent

- **[Extending Playbooks](guides/extending-playbooks.md)** - How to add new alert types
- **[Triage Methodology](acceptance/triage-methodology.md)** - Quality philosophy (honesty over guessing)

## Quality Standards (Acceptance Specs)

These define the contract for deterministic triage reports:

- **[Triage Methodology](acceptance/triage-methodology.md)** - Core philosophy
- **[Base Contract](acceptance/base-contract.md)** - label + why + next; blocked scenarios A-D
- **[Family Specs](acceptance/families.md)** - Alert-family-specific enrichment rules

## Scoring System

- **[Scoring Overview](scoring/README.md)** - Impact, confidence, noise axes
- **[Scoring Contract](scoring/contract.md)** - 0-100 scales and classification rules
- **[Golden Corpus](scoring/golden-corpus.md)** - Real-world calibration examples

## Optional Features

- **[Tool-Using Chat](features/chat.md)** - Case investigation assistant (PromQL, K8s, logs, memory)
- **[Action Proposals](features/actions.md)** - Policy-gated remediation suggestions
- **[Case Memory](features/memory.md)** - Learn from past incidents (PostgreSQL + embeddings)

## Roadmap

- **[Completed Initiatives](roadmap/completed.md)** - Major features shipped
- **[Planned Enhancements](roadmap/planned.md)** - Future work and backlog

---

## Recommended Reading Paths

**Path 1: Decision Maker (10 min)**
1. [One Pager](one-pager.md)
2. [Scoring Overview](scoring/README.md)

**Path 2: On-Call Engineer (20 min)**
1. [Quickstart](guides/quickstart.md)
2. [Triage Methodology](acceptance/triage-methodology.md)
3. [Base Contract](acceptance/base-contract.md)

**Path 3: Developer Adding Features (45 min)**
1. [Architecture Overview](architecture/README.md)
2. [Investigation Pipeline](architecture/investigation-pipeline.md)
3. [Diagnostic Modules](architecture/diagnostic-modules.md)
4. [Extending Playbooks](guides/extending-playbooks.md)

**Path 4: Operator Deploying to Production (30 min)**
1. [Deployment Guide](guides/deployment.md)
2. [Operations](guides/operations.md)
3. [Environment Variables](guides/environment-variables.md)
