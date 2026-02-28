# Environment variables (defaults + where to configure)

This repo is intentionally **env-driven** (ConfigMap/Secret friendly). This page lists the variables in one place so you don’t have to remember them.

## Conventions

- **ConfigMap (`k8s/configmap.yaml`)**: non-secret configuration and feature flags.
- **Secret (`k8s/secret.yaml`)**: secrets (API keys, passwords, OAuth secrets).
- Defaults below reflect the **code defaults** (if env var is unset). Kubernetes manifests may set different defaults.

## Core inputs (providers)

**ConfigMap**
- `ALERTMANAGER_URL` (default: `http://localhost:19093`)
- `PROMETHEUS_URL` (default: `http://localhost:18481/select/0/prometheus`)
- `LOGS_URL` (default: empty → logs skipped; dev fallback `http://localhost:19471`)
- `CLUSTER_NAME` (default: empty)

## Report generation / webhook

**ConfigMap**
- `TIME_WINDOW` (default: `1h`)
- `ALERTNAME_ALLOWLIST` (default: empty)

## Storage (S3)

**ConfigMap**
- `S3_BUCKET` (**required** for worker mode)
- `S3_PREFIX` (default: empty)

## Queue (NATS JetStream)

**ConfigMap**
- `NATS_URL` (default: `nats://127.0.0.1:4222`)
- `JETSTREAM_STREAM` (default: `TARKA`)
- `JETSTREAM_SUBJECT` (default: `<stream>.alerts`)
- `JETSTREAM_DURABLE` (default: `WORKERS`)
- `JETSTREAM_DUPLICATE_WINDOW_SECONDS` (default: `3600`)
- `JETSTREAM_ACK_WAIT_SECONDS` (default: `1800`)
- `JETSTREAM_MAX_DELIVER` (default: `5`)
- `JETSTREAM_BACKOFF_SECONDS` (default: empty; optional CSV like `5,30,120`)
- `JETSTREAM_DLQ_STREAM` (default: `<stream>_DLQ`)
- `JETSTREAM_DLQ_SUBJECT` (default: `tarka.dlq`)

## Memory / Postgres

**ConfigMap**
- `MEMORY_ENABLED` (default: `0`)
- `DB_AUTO_MIGRATE` (default: `0`)
- `POSTGRES_HOST` (default: empty)
- `POSTGRES_PORT` (default: `5432`)
- `POSTGRES_DB` (default: empty)
- `POSTGRES_USER` (default: empty)

**Secret**
- `POSTGRES_PASSWORD` (default: empty)
- `POSTGRES_DSN` (default: empty; if set, overrides the parts above)

## Console auth (UI login)

**ConfigMap**
- `CONSOLE_AUTH_MODE` (default: `basic`) — `basic|oidc|both|disabled`
- `AUTH_PUBLIC_BASE_URL` (default: empty; required for OIDC)
- `AUTH_ALLOWED_DOMAINS` (default: empty CSV)
- `AUTH_SESSION_TTL_SECONDS` (default: `43200` = 12h)
- `AUTH_COOKIE_SECURE` (default: auto; set `1` to force secure cookies)

**Secret**
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `AUTH_SESSION_SECRET`
- (optional for transitional basic auth) `CONSOLE_AUTH_USERNAME`, `CONSOLE_AUTH_PASSWORD`

## LLM (Vertex AI) — used by case chat + optional report-time enrichment

Important:
- `LLM_ENABLED` gates **report-time enrichment** (`analysis.llm`) only.
- The **case chat** is gated by `CHAT_ENABLED` (policy). Chat uses the LLM only when Vertex AI is configured; otherwise it falls back to deterministic hypotheses.

**ConfigMap**
- `LLM_PROVIDER` (default: `vertexai`)
- `LLM_MODEL` (default: `gemini-2.5-flash`)
- `GOOGLE_CLOUD_PROJECT` (required)
- `GOOGLE_CLOUD_LOCATION` (required)
- `LLM_ENABLED` (default: `0`)
- `LLM_MOCK` (default: `0`)
- `LLM_TIMEOUT_SECONDS` (default: `15`)
- `LLM_MAX_OUTPUT_TOKENS` (default: `4096`)
- `LLM_TEMPERATURE` (default: `0.2`)
- `LLM_INCLUDE_LOGS` (default: `0`)
- `LLM_DEBUG` (default: `0`)
- `LLM_DEBUG_MAX_CHARS` (default: `4000`)
**Secret**
- none required for Vertex if using ADC (Workload Identity / mounted credentials)

### How to make Vertex AI work (ADC in Kubernetes)

Vertex AI uses `google.auth.default(...)` under the hood. That means the pod must have **Application Default Credentials (ADC)** available. This is **not** the same as the Console login OAuth (`GOOGLE_OAUTH_CLIENT_ID/SECRET`)—those are for the **human UI login flow**.

Two common ways to provide ADC to the **webhook** pod:

- **Option A (preferred on GKE): Workload Identity / metadata server ADC**
  - Bind a Kubernetes ServiceAccount (KSA) to a Google Service Account (GSA) with permission to call the **Generative Language API**.
  - No JSON key needed; `google.auth.default()` fetches tokens from the metadata server.

- **Option B (works anywhere): mount a service-account JSON key**
  - Create a Kubernetes Secret containing a GCP service account key JSON.
  - Mount it and set `GOOGLE_APPLICATION_CREDENTIALS` to the mounted path.

If ADC is missing, chat falls back with `Reason: missing_adc_credentials`.

## LangSmith tracing (LangGraph + tool calls)

This repo uses LangGraph as the core orchestration layer. You can enable tracing to **LangSmith Cloud** to visualize:\n
- graph runs (node-by-node)\n
- tool calls (inputs/outputs)\n
- LLM calls\n

**ConfigMap**\n
- `LANGSMITH_TRACING` (default: `false`) — set `true` to enable tracing\n
- `LANGSMITH_PROJECT` (default: `tarka`) — project name in LangSmith\n
- (optional) `LANGSMITH_TAGS` (default: empty CSV)\n
- (optional) `LANGSMITH_RUN_NAME_PREFIX` (default: empty)\n
- (optional) `LANGSMITH_TRACE_EXCLUDE` (default: empty CSV) — exclude noisy run names from traces; supports prefix wildcard `*` (example: `postgres_index_run,s3_put_*`)\n
\n
**Secret**\n
- `LANGSMITH_API_KEY` — your LangSmith API key\n
\n
Notes:\n
- Tracing is **env-gated**: if `LANGSMITH_TRACING` is not truthy, the agent does not emit traces.\n
- Endpoint defaults to LangSmith Cloud (no `LANGSMITH_ENDPOINT` needed).\n

## Tool-using chat (policy)

**ConfigMap**
- `CHAT_ENABLED` (default: `0`)
- `CHAT_ALLOW_PROMQL` (default: `1`)
- `CHAT_ALLOW_K8S_READ` (default: `1`)
- `CHAT_ALLOW_LOGS_QUERY` (default: `1`)
- `CHAT_ALLOW_ARGOCD_READ` (default: `0`)
- `CHAT_ALLOW_REPORT_RERUN` (default: `1`)
- `CHAT_ALLOW_MEMORY_READ` (default: `1`)
- `CHAT_NAMESPACE_ALLOWLIST` (default: empty)
- `CHAT_CLUSTER_ALLOWLIST` (default: empty)
- `CHAT_MAX_STEPS` (default: `4`, capped to 8)
- `CHAT_MAX_TOOL_CALLS` (default: `6`, capped to 20)
- `CHAT_MAX_LOG_LINES` (default: `200`, capped to 2000)
- `CHAT_MAX_PROMQL_SERIES` (default: `200`, capped to 5000)
- `CHAT_MAX_TIME_WINDOW_SECONDS` (default: `21600`, capped to 86400)
- `CHAT_REDACT_SECRETS` (default: `1`)

## Action proposals (policy)

**ConfigMap**
- `ACTIONS_ENABLED` (default: `0`)
- `ACTIONS_REQUIRE_APPROVAL` (default: `1`)
- `ACTIONS_ALLOW_EXECUTE` (default: `0`)
- `ACTIONS_TYPE_ALLOWLIST` (default: empty → allow all known; recommended to set allowlist)
- `ACTIONS_NAMESPACE_ALLOWLIST` (default: empty)
- `ACTIONS_CLUSTER_ALLOWLIST` (default: empty)
- `ACTIONS_MAX_ACTIONS_PER_CASE` (default: `25`, capped to 200)
