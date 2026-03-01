# Tool-using chat (case page)

The Console UI can optionally expose a **Chat** panel on the case detail page. This chat is designed to behave like an on-call SRE assistant:
- it reads the current case/run **SSOT** (`analysis_json`)
- it can gather additional evidence using **read-only tools** (policy gated)
- it returns a cited, on-call-friendly reply

## Endpoints

- `GET /api/v1/chat/config` — returns whether chat is enabled and which tool categories are allowed.
- `POST /api/v1/cases/{case_id}/chat` — sends a user message and returns a reply plus tool execution events.

Note: these endpoints require Console authentication (same as the rest of `/api/v1/*`).

## Policy (admin configuration)

Chat is **off by default**. Enable it via env/ConfigMap:

- `CHAT_ENABLED=1`

Tool category gates:
- `CHAT_ALLOW_PROMQL=1`
- `CHAT_ALLOW_K8S_READ=1`
- `CHAT_ALLOW_K8S_EVENTS=1` (K8s events query tool)
- `CHAT_ALLOW_LOGS_QUERY=1`
- `CHAT_ALLOW_AWS_READ=0` (AWS infrastructure health checks - EC2, EBS, ELB, RDS, etc.)
- `CHAT_ALLOW_GITHUB_READ=0` (GitHub commits, workflows, docs)
- `CHAT_ALLOW_ARGOCD_READ=0` (provider is currently a placeholder)
- `CHAT_ALLOW_REPORT_RERUN=1`
- `CHAT_ALLOW_MEMORY_READ=1`

Scope allowlists (optional):
- `CHAT_NAMESPACE_ALLOWLIST=prod,staging`
- `CHAT_CLUSTER_ALLOWLIST=cluster-a`
- `CHAT_AWS_REGION_ALLOWLIST=us-east-1,us-west-2` (restrict AWS queries to specific regions)
- `CHAT_GITHUB_REPO_ALLOWLIST=myorg/repo1,myorg/repo2` (restrict GitHub queries to specific repos)

Caps (optional):
- `CHAT_MAX_STEPS=4`
- `CHAT_MAX_TOOL_CALLS=6`
- `CHAT_MAX_LOG_LINES=200`
- `CHAT_MAX_PROMQL_SERIES=200`
- `CHAT_MAX_TIME_WINDOW_SECONDS=21600` (6h default)

Redaction:
- `CHAT_REDACT_SECRETS=1` (default) — best-effort redaction of common token patterns in tool outputs.

## LLM configuration (Vertex AI)

Chat uses the unified LLM client (`agent/llm/client.py`) backed by Vertex AI (Gemini).
Configuration is documented in [`environment-variables.md`](../guides/environment-variables.md) (requires `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION` + ADC).

If Vertex AI is not configured or unavailable, chat will fall back to a deterministic reply based on ranked hypotheses.

For local wiring/tests:
- `LLM_MOCK=1` (no external call; returns deterministic stub output)

## Notes / security posture

- Tools are **read-only** today (no action execution).
- Future “safe actions” should be implemented as **proposals**, gated by policy, and require explicit approval + audit logging.

### Action proposals (optional)

If enabled, the chat runtime may use:
- `actions.list` (audit trail)
- `actions.propose` (create a proposal record; still requires human approval)

See [`actions.md`](actions.md) for policy + endpoints.

## Resolution feedback (learning loop)

To make memory useful over time, the UI can capture how a case was resolved (category + summary + link).

Endpoints:
- `POST /api/v1/cases/{case_id}/resolve` — mark case closed and store `resolution_category`, `resolution_summary`, `postmortem_link`.
- `POST /api/v1/cases/{case_id}/reopen` — reopen case and clear resolution fields.

Calibration:
- When `MEMORY_ENABLED=1`, diagnostic hypotheses may receive a small confidence boost when **similar resolved cases** overwhelmingly share a matching resolution category.
- This is surfaced explicitly in the hypothesis `why` list as `Memory: ...` and includes `memory.similar_cases` as a supporting ref.
