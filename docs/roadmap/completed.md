# Completed Initiatives

Major features and improvements that have been implemented and shipped.

---

## NATS JetStream Worker Queue

**Status**: ✅ Shipped
**Completed**: Q4 2025

### Overview

Async job processing architecture using NATS JetStream for production scalability and reliability.

### What Was Delivered

- **Webhook Receiver** (`agent/api/webhook.py`): FastAPI endpoint that enqueues jobs and returns 202 quickly
- **JetStream Integration** (`agent/api/worker_jetstream.py`): Full consumer implementation with:
  - Explicit ACK/NAK semantics
  - Backpressure via bounded concurrency
  - Dead letter queue (DLQ) for poison messages
  - Heartbeat to extend ACK deadline during long investigations
  - Configurable retry policy with exponential backoff
- **Worker Pool**: Horizontally scalable worker pods consuming from queue
- **Deduplication**: Queue-level deduplication via JetStream message ID
- **Kubernetes Manifests**: NATS StatefulSet, receiver deployment, worker deployment

### Configuration

```bash
NATS_URL=nats://nats.tarka.svc.cluster.local:4222
JETSTREAM_STREAM=TARKA
JETSTREAM_SUBJECT=tarka.alerts
JETSTREAM_DURABLE=WORKERS
JETSTREAM_ACK_WAIT_SECONDS=1800
JETSTREAM_MAX_DELIVER=5
WORKER_CONCURRENCY=2
```

### Benefits

- **Reliability**: Durable message queue with guaranteed delivery
- **Scalability**: Horizontal worker scaling independent of receiver
- **Backpressure**: Graceful handling of burst traffic
- **Observability**: DLQ for failed jobs, metrics-ready architecture

---

## LangGraph RCA Integration

**Status**: ✅ Shipped
**Completed**: Q4 2025

### Overview

LangGraph-powered root cause analysis workflow with tool-using LLM for deeper investigation.

### What Was Delivered

- **RCA Graph** (`agent/graphs/rca.py`): Multi-step LLM workflow that:
  - Assesses evidence quality from initial investigation
  - Plans which tools to use for additional evidence
  - Executes tools (PromQL, K8s, logs queries) adaptively
  - Synthesizes root cause + remediation recommendations
- **Tool Integration**: RCA graph has access to same tools as chat (PromQL, K8s, logs, memory, reruns)
- **Evidence Quality Detection**: Automatic trigger when confidence < 70% or missing critical inputs
- **Policy Enforcement**: Same `ChatPolicy` controls apply to RCA workflow
- **Tracing** (`agent/graphs/tracing.py`): LangSmith integration for debugging workflows

### Configuration

```bash
LLM_ENABLED=true
LLM_PROVIDER=vertex
LLM_MODEL=gemini-2.5-flash
CHAT_ENABLED=true  # Master switch for RCA + chat
```

### Benefits

- **Deeper Analysis**: LLM can reason about complex failure modes
- **Adaptive Evidence Gathering**: Tools selected based on what's missing
- **Transparency**: Full trace of tool calls and reasoning
- **Cost Effective**: Only triggered when baseline confidence is low

---

## Case Memory & Skills

**Status**: ✅ Shipped
**Completed**: Q4 2025

### Overview

Case-based reasoning system that learns from past incidents and provides context-aware suggestions.

### What Was Delivered

- **PostgreSQL Storage** (`agent/memory/case_index.py`): Cases and investigation runs indexed with metadata
- **Similarity Search** (`agent/memory/case_retrieval.py`): Find past incidents matching current alert by:
  - Alert family (exact match)
  - Cluster + namespace/workload (weighted)
  - Temporal decay (recent cases ranked higher)
- **Skills Library** (`agent/memory/skills.py`): Distilled runbooks with versioning (draft/active/retired)
- **Caseization** (`agent/memory/caseize.py`): Automatic conversion of investigations to searchable cases
- **Chat Integration**: Memory tools (`memory.similar_cases`, `memory.skills`) available in Console chat
- **Feedback System**: On-call can rate skill suggestions (helpful/unhelpful)
- **Database Migrations** (`agent/memory/migrate.py`): Versioned SQL migrations with Postgres advisory lock

### Configuration

```bash
MEMORY_ENABLED=1
POSTGRES_HOST=postgres.tarka.svc.cluster.local
POSTGRES_PORT=5432
POSTGRES_DB=tarka
POSTGRES_USER=tarka
POSTGRES_PASSWORD=<secret>
DB_AUTO_MIGRATE=1  # Dev only; run explicitly in production
```

### Benefits

- **Learn from History**: Past resolutions inform current investigations
- **Confidence Boosting**: Similar cases increase hypothesis confidence
- **Skill Suggestions**: Context-aware runbook recommendations
- **Continuous Improvement**: Feedback loop refines skill library

---

## Console Authentication (OIDC)

**Status**: ✅ Shipped
**Completed**: Q4 2025

### Overview

Google OIDC authentication for Console UI with session management and domain allowlisting.

### What Was Delivered

- **Authentication Modes** (`agent/auth/config.py`): Basic, OIDC, both, or disabled
- **Google OAuth Integration**: Standard OAuth 2.0 flow with Google as identity provider
- **Session Management** (`agent/auth/session.py`): Secure session cookies with TTL
- **Domain Allowlisting**: Restrict access to specific email domains (e.g., `yourcompany.com`)
- **Public Base URL**: Configurable for local dev (HTTP) or production (HTTPS)
- **Callback Handling**: `/api/auth/callback/google` endpoint for OAuth flow

### Configuration

```bash
AUTH_MODE=oidc  # or "basic", "both", "disabled"
GOOGLE_OAUTH_CLIENT_ID=<client-id>.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=<secret>
AUTH_SESSION_SECRET=<random-32-char-secret>
AUTH_PUBLIC_BASE_URL=https://sre-console.yourcompany.com
AUTH_ALLOWED_DOMAINS=yourcompany.com
```

### Benefits

- **Enterprise SSO**: No separate password management
- **Domain Restriction**: Only company employees can access
- **Secure Sessions**: HTTP-only cookies, configurable TTL
- **Flexible**: Supports basic auth for development, OIDC for production

---

## Other Notable Improvements

### Diagnostic Modules System

- Universal failure mode detection framework
- Registry-based module loading
- Confidence scoring (0-100)
- Action proposals (policy-gated)

### Scoring System

- Impact, confidence, noise (0-100 scales)
- Classification: actionable, informational, noisy, artifact
- Feature-based scoring with reason codes

### Base Triage Contract

- Deterministic verdict builder
- Explicit blocked scenarios (A, B, C, D)
- PromQL-first next steps
- Evidence quality tracking

### Noise Analysis

- Flapping detection (configurable lookback)
- Cardinality analysis (ephemeral labels)
- Recommended label drops

### Change Correlation

- K8s rollout event detection
- Deployment timeline extraction
- Last change timestamp tracking

### Capacity Analysis

- CPU/memory over/under-request detection
- Rightsizing recommendations
- Top consumers by utilization
