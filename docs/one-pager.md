# One pager: Tarka

## Why this exists

In a small company, incidents are expensive not only because something is broken, but because **a few senior people become the "human runbook"**. On-call becomes a constant context switch: stitching together alerts, metrics, Kubernetes context, and logs into a single answer.

This project aims to make on-call survivable with a small team by producing a **consistent, honest, actionable triage report** for each *alert instance*.

## What it is

An AI-powered incident investigation agent that:
- **Ingests** Prometheus/Alertmanager alerts (CLI or webhook)
- **Collects evidence** best-effort (Prometheus + Kubernetes read-only + logs + optional AWS/GitHub)
- **Runs diagnostics** using universal failure mode detectors (27+ diagnostic modules)
- **Parses logs** deterministically for ERROR/FATAL/Exception patterns (no LLM required)
- **Handles edge cases** like TTL-deleted pods with historical log queries
- **Scores investigations** with impact/confidence/noise (0-100 scales)
- **Renders an on-call-first report** that answers: **what broke, where, how bad, is it real, what do I do next**
- **Offers interactive chat** with policy-enforced tool use (PromQL, kubectl, memory search)

## What value it brings (small team lens)

- **Faster time-to-first-action (MTTA)**: the report starts with the fastest discriminators and copy/paste-friendly commands.
- **Less senior dependency**: it encodes “the first 60 seconds” of investigation so newer engineers can do safe triage.
- **Higher trust under uncertainty**: when inputs are missing (scope/identity/K8s/logs), it does not guess; it says “unknown” and shows how to find the answer quickly.
- **Consistency across incidents**: a stable report structure reduces cognitive load and makes postmortems easier.

## What it does (concretely)

### Core Investigation Pipeline
- **Alert-instance triage**: investigate by fingerprint, not just alert name
- **Scope/blast radius**: how many instances are firing and where they concentrate
- **Base triage contract**: a deterministic summary (`label`, `why`, `next`) that stays useful even when the agent is blocked
- **27+ diagnostic modules**: universal failure mode detection (image pull failures, crash loops, resource contention, etc.)
- **Deterministic log parsing**: automatic ERROR/FATAL/Exception pattern extraction (no LLM needed)
- **Historical fallback**: investigates TTL-deleted pods using regex log queries and alert timestamps
- **Family enrichment**: alert-specific interpretation and next steps for 10+ alert families:
  - Pod not healthy (CrashLoopBackOff, ImagePullBackOff, etc.)
  - CPU throttling
  - OOM killed
  - HTTP 5xx errors
  - Memory pressure
  - **Job failures** (NEW: handles Kubernetes Job alerts with TTL-deleted pods)
  - Target down
  - K8s rollout health
  - Observability pipeline issues
  - Meta alerts

### Production Features
- **Scoring system**: Impact/confidence/noise scoring (0-100) for alert prioritization
- **Change correlation**: Links alerts to recent K8s rollouts, AWS CloudTrail events, GitHub deployments
- **Memory/case-based reasoning**: Similarity search across past incidents
- **Interactive chat**: Tool-using assistant with policy enforcement (PromQL, kubectl, logs, memory)
- **Report artifacts**: S3 storage with web UI for browsing investigations
- **NATS JetStream**: Scalable async job queue for high alert volume
- **Authentication**: Session-based auth + optional OIDC/SSO

## Where it fits in your stack

### Deployment Modes
- **CLI mode**: Engineer runs investigation locally for a selected alert instance
  ```bash
  poetry run python main.py --list-alerts
  poetry run python main.py --alert 0 --llm
  ```

- **Webhook mode**: Production deployment with async workers
  ```
  Alertmanager → FastAPI webhook → NATS JetStream → Worker pool → S3 artifacts → Web UI
  ```

- **Infrastructure**: Docker Compose (dev) or Kubernetes (production)

### Integration Points
- **Required**: Prometheus/Alertmanager (for alerts and metrics)
- **Strongly recommended**: Kubernetes (for pod/workload context)
- **Optional**: VictoriaLogs/Loki (for log queries)
- **Optional**: AWS (CloudTrail events, EC2/EBS/RDS context)
- **Optional**: GitHub (deployment correlation via GitHub App)
- **Optional**: PostgreSQL (for memory/case storage)

The agent is designed to be **read-only**: no cluster mutation, no destructive actions.

## What “good” looks like

Success is when an on-call responder can answer in **<60 seconds**:
1) what is affected, 2) where, 3) blast radius, 4) impact (or explicitly “unknown”), 5) the next best action.

See [`examples/`](../examples/README.md) for sample report output and UI screenshots.

## What it does *not* do

- It does **not** "magic" root cause from thin air. If evidence is missing, it stays explicit about unknowns (Scenarios A-D).
- It does **not** require deep access: it prefers **PromQL-first** next steps, with optional `kubectl` fallbacks.
- It does **not** take destructive action (read-only operations only).
- It does **not** require LLM for base functionality (deterministic scoring + log parsing work without AI).

## Current Status (February 2026)

✅ **Production-ready**: All tests passing (419/419), full CI/CD validation
✅ **27+ diagnostic modules**: Universal failure mode detection
✅ **10+ alert families**: Comprehensive playbook coverage
✅ **Job failure support**: Handles TTL-deleted pods with historical fallback
✅ **Deterministic log parsing**: ERROR/FATAL/Exception extraction
✅ **Scoring system**: Impact/confidence/noise (0-100)
✅ **Interactive chat**: Tool-using assistant with policy enforcement
✅ **Web UI**: React-based console with docked chat interface
✅ **Authentication**: Session + OIDC/SSO support
✅ **Cloud integrations**: AWS CloudTrail, GitHub deployments

**Recent improvements** (January-February 2026):
- Added universal historical fallback for TTL-deleted pods
- Implemented deterministic log parsing (no LLM required)
- Complete KubeJobFailed alert support with regex log queries
- Enhanced chat runtime with exception handling and thread persistence
- Comprehensive scoring profiles for all major alert families

## Next steps (how to adopt)

### Quick Start
1. **Local CLI**: Run investigations on your laptop
   ```bash
   git clone <repo>
   poetry install
   export PROMETHEUS_URL=http://your-prometheus:9090
   poetry run python main.py --list-alerts
   ```

2. **Docker Compose**: Full stack (webhook + workers + UI)
   ```bash
   cd deploy/docker-compose
   ./deploy.sh
   ```

3. **Kubernetes**: Production deployment
   ```bash
   cd deploy/k8s
   ./deploy.sh
   ```

### Deep Dive
1. **Review architecture**: [`docs/architecture/README.md`](architecture/README.md)
2. **Understand pipeline**: [`docs/architecture/investigation-pipeline.md`](architecture/investigation-pipeline.md) (includes state diagram)
3. **See triage methodology**: [`docs/acceptance/triage-methodology.md`](acceptance/triage-methodology.md)
4. **Add custom playbooks**: [`docs/guides/extending-playbooks.md`](guides/extending-playbooks.md)
5. **Configure authentication**: [`docs/guides/authentication.md`](guides/authentication.md)

### Key Resources
- **State diagram**: `docs/architecture/investigation-pipeline.md` (lines 56-97)
- **Environment variables**: `docs/guides/environment-variables.md`
- **Test suite**: `make test-ci` (runs all 419 tests)
