# CLAUDE.md

## Project

**Tarka** — converts Prometheus/Alertmanager alerts into actionable triage reports. Read-only by design; never mutates cluster state.

Core principle: if evidence is missing, don't guess. Be explicit about unknowns.

## Safety Rules

**Ask before running any state-modifying command:**
- AWS: `aws iam put-*`, `aws s3 rm`, `aws eks update-*`, etc.
- K8s: `kubectl apply/delete/patch/scale/drain` (read-only `get/describe/logs` is safe)
- Git: `commit/push/rebase/reset/merge` (read-only `status/log/diff` is safe)
- DB: `DROP/DELETE/UPDATE/ALTER` (`SELECT` is safe)
- FS: `rm -rf`, destructive `mv` (creating files in /tmp is safe)

**Safe without asking:** read-only commands, `poetry run pytest`, `make test`, `docker build`, `poetry install`

## Dev Commands

```bash
poetry install                           # setup
make test                                # unit tests (no external deps)
make test-ci                             # full CI suite (Docker required)
make format                              # black + isort
make pre-commit                          # all pre-commit hooks
poetry run python main.py --list-alerts  # CLI usage
poetry run python main.py --alert 0      # investigate alert
```

## Architecture

`Investigation` is the SSOT — one object mutated through the pipeline:

```
Alert → Playbook → Evidence Collection → Diagnostics → Base Triage → Enrichment → Scoring → Report
```

**Key files to start with:**
- `agent/core/models.py` — domain models (Investigation, Evidence, Analysis)
- `agent/pipeline/pipeline.py` — orchestrator
- `agent/diagnostics/engine.py` — diagnostic system
- `agent/playbooks/__init__.py` — playbook registry
- `agent/chat/tools.py` + `agent/authz/policy.py` — chat tools + policy

**KubeJobFailed gotcha:** the `pod` label points to kube-state-metrics (wrong); use `job_name` label instead. See `agent/collectors/job_failure.py`.

## Extension Patterns

**New playbook:** create in `agent/playbooks/`, register in `__init__.py`, add tests.

**New diagnostic:** inherit `DiagnosticModule`, implement `applicable()` + `diagnose()`, register in `agent/diagnostics/registry.py`.

**New chat tool:** implement in `agent/chat/tools.py`, add policy flag in `agent/authz/policy.py`, register in `runtime._allowed_tools()`.

## Environment

See `docs/guides/environment-variables.md` for the full list. Key dev vars:
- `PROMETHEUS_URL`, `ALERTMANAGER_URL`, `LOGS_URL` — data sources
- `AUTH_SESSION_SECRET`, `ADMIN_INITIAL_PASSWORD` — auth (required)
- `LLM_ENABLED` / `LLM_MOCK` — LLM enrichment (off by default)
- `AWS_EVIDENCE_ENABLED` / `CHAT_ALLOW_AWS_READ` — AWS integration (off by default)
- `GITHUB_EVIDENCE_ENABLED` / `CHAT_ALLOW_GITHUB_READ` — GitHub integration (off by default)
