# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Tarka** converts Prometheus/Alertmanager alerts into actionable triage reports. Read-only by design; never mutates cluster state.

Core principle: if evidence is missing, don't guess. Be explicit about unknowns.

## Tech Stack

- **Backend:** Python 3.12+, Poetry, FastAPI, NATS JetStream, PostgreSQL (pgvector)
- **Frontend:** React + TypeScript, Vite, Vitest, Playwright
- **Deploy:** Helm chart (`deploy/chart/`) or standalone manifests (`deploy/manifests/`), images on `ghcr.io/tarkyaio/`
- **Version** must stay in sync across `pyproject.toml`, `deploy/chart/Chart.yaml`, and `ui/package.json`

## Sensitive Files

**Never read, display, or modify these files:**
- `.env.deploy` — contains live secrets and API keys

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
poetry install                           # setup (add -E anthropic or -E all-providers for LLM support)
make test                                # unit tests (no external deps, excludes integration + e2e)
make test-integration                    # integration tests (requires NATS)
make test-ci                             # full CI suite (Docker required, 20-min timeout)
make test-ui                             # UI unit tests (Vitest)
make test-ui-e2e                         # UI e2e tests (Playwright, Node 20+)
make coverage                            # coverage report to htmlcov/
make format                              # black + isort
make pre-commit                          # all pre-commit hooks
```

**Run a single test:**
```bash
poetry run pytest tests/path/to/test_file.py::test_name -v
```

**Local dev (3 terminals):**
```bash
make dev-up                              # start services: PostgreSQL, NATS, mock Prometheus/Alertmanager
make dev-serve                           # API server (localhost:8080)
make dev-ui                              # UI dev server (localhost:5173)
make dev-down                            # stop services
make dev-send-alert                      # send a random test alert
```

**CLI usage:**
```bash
poetry run python main.py --list-alerts  # list active alerts
poetry run python main.py --alert 0      # investigate alert
poetry run python main.py --alert 0 --llm  # with LLM enrichment
```

## Architecture

`Investigation` is the SSOT, one object mutated through the pipeline:

```
Alert -> Playbook -> Evidence Collection -> Diagnostics -> Base Triage -> Enrichment -> Scoring -> Report
```

**Two entry points:**
- **CLI:** `main.py` for local investigation
- **Webhook server:** `agent/api/webhook.py` (FastAPI, receives alerts, stores in DB, queues via NATS)

**Worker:** `agent/api/worker_jetstream.py` processes investigations from the NATS queue.

**UI:** separate React app (`ui/`) with its own Dockerfile, served by nginx in production.

**Key files to start with:**
- `agent/core/models.py` — domain models (Investigation, Evidence, Analysis)
- `agent/pipeline/pipeline.py` — orchestrator
- `agent/diagnostics/engine.py` — diagnostic system
- `agent/playbooks/__init__.py` — playbook registry
- `agent/chat/tools.py` + `agent/authz/policy.py` — chat tools + policy

**KubeJobFailed gotcha:** the `pod` label points to kube-state-metrics (wrong); use `job_name` label instead. See `agent/collectors/job_failure.py`.

## Testing

- **Markers:** `integration` (needs NATS), `e2e` (full stack). `make test` excludes both by default.
- **conftest.py** auto-stubs the NATS JetStream client so unit tests need no network.
- **Pre-commit hooks** run black, isort, flake8, fast pytest, plus UI lint and prettier.

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
