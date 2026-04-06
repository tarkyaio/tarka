# HTTP source (generic webhook receiver)

Tarka natively consumes alerts from Alertmanager, but you can also push events from **any system that can send an HTTP POST** — Zendesk, Statuspage, custom tooling, or anything else — using the generic HTTP source.

Events are processed by the same investigation pipeline as Alertmanager alerts. The difference is that you define a **Jinja2 field mapping** that tells Tarka how to read your payload's fields.

---

## How it works

1. You write an `http_sources.yaml` file defining one or more sources.
2. Tarka exposes a unique webhook URL per source: `POST /sources/{id}/ingest`.
3. Your external system sends JSON to that URL.
4. Tarka renders your Jinja2 templates against the payload to extract `fingerprint`, `alertname`, `status`, and other fields.
5. The normalised event is enqueued to JetStream and investigated by the standard pipeline.

---

## Quick start

### 1. Write `http_sources.yaml`

Edit `config/http_sources.yaml` (copy the examples already there as a starting point):

```yaml
sources:
  - id: zendesk
    name: "Zendesk Support"
    secret: "your-signing-secret"
    signature_header: "X-Zendesk-Webhook-Secret"
    field_map:
      fingerprint: "{{ payload.ticket.id }}"
      alertname: "{{ payload.ticket.subject }}"
      status: "{{ 'resolved' if payload.ticket.status in ['solved', 'closed'] else 'firing' }}"
      summary: "{{ payload.ticket.subject }}"
      severity: "{{ 'critical' if payload.ticket.priority == 'urgent' else 'warning' }}"
      source_url: "{{ payload.ticket.url }}"
      starts_at: "{{ payload.ticket.created_at }}"
    extra_labels:
      source: "zendesk"
```

### 2. Set the environment variable

```bash
HTTP_SOURCE_CONFIG_FILE=/path/to/http_sources.yaml
```

### 3. Configure your external system

Point it to:

```
POST https://your-tarka-host/sources/zendesk/ingest
Content-Type: application/json
```

That's it. Send a test event and it will appear in Tarka as a new case.

---

## Field mapping reference

All field values are **Jinja2 templates**. The incoming JSON body is available as `payload`.

| Field | Required | Description |
|-------|----------|-------------|
| `fingerprint` | Yes | Stable deduplication key. Must not change across repeated deliveries for the same event. |
| `alertname` | Yes | Short name shown in the case list. |
| `status` | Yes | Must render to `firing` or `resolved` (case-insensitive). Anything else → `unknown`. |
| `severity` | No | Mapped to `labels.severity`. Conventions: `critical`, `warning`, `info`. |
| `summary` | No | Short human-readable description. |
| `description` | No | Longer description shown in case details (truncate long text). |
| `source_url` | No | Link back to the originating record (ticket URL, incident URL, etc.). |
| `starts_at` | No | RFC3339 timestamp used to anchor evidence collection. |
| `ends_at` | No | RFC3339 timestamp set when an event is definitively resolved. |

### `extra_labels`

Static or templated labels added to every event from this source. Values are also Jinja2 templates:

```yaml
extra_labels:
  source: "zendesk"
  team: "{{ payload.ticket.tags | select('startswith', 'team-') | first | default('unknown') }}"
```

### Jinja2 tips

```yaml
# Conditional with default
severity: "{{ 'critical' if payload.data.priority == 'P1' else 'warning' }}"

# String filter
summary: "{{ payload.title | truncate(100) | upper }}"

# Nested access with default for missing fields
description: "{{ payload.body.text | default('No description') }}"

# Multi-line expression (YAML folded scalar)
status: >-
  {{ 'resolved' if payload.state in ['closed', 'done']
     else 'firing' }}
```

If a template path is undefined (e.g. `payload.ticket.missing_field`), it renders as an empty string. Missing **required** fields (`fingerprint`, `alertname`, `status`) cause the event to be rejected with HTTP 400.

---

## Signature verification

To prevent unauthorised events, configure HMAC-SHA256 signature verification per source.

```yaml
sources:
  - id: zendesk
    secret: "your-shared-secret"
    signature_header: "X-Zendesk-Webhook-Secret"  # header name sent by the source
    signature_prefix: ""                           # e.g. "sha256=" for GitHub-style
```

Tarka computes `HMAC-SHA256(raw_request_body, secret)` and compares it to the value in `signature_header` using constant-time comparison (prevents timing attacks).

If `secret` is not set, signature verification is skipped. **Only omit the secret on private, trusted networks.**

### Common signature formats

| Source | Header | Prefix |
|--------|--------|--------|
| Zendesk | `X-Zendesk-Webhook-Secret` | _(none)_ |
| GitHub | `X-Hub-Signature-256` | `sha256=` |
| Stripe | `Stripe-Signature` | `t=...,v1=` _(not yet supported — use raw hex)_ |
| Custom | anything you choose | anything you choose |

---

## Source configuration reference

```yaml
sources:
  - id: string              # URL-safe identifier — appears in the webhook URL
    name: string            # Human-readable display name (used in logs)

    # Optional signature verification
    secret: string          # HMAC-SHA256 shared secret
    signature_header: string  # HTTP header that carries the signature
    signature_prefix: string  # Prefix to strip before comparing (e.g. "sha256=")

    field_map:
      fingerprint: "{{ ... }}"   # Required
      alertname:   "{{ ... }}"   # Required
      status:      "{{ ... }}"   # Required — must render to firing|resolved
      severity:    "{{ ... }}"   # Optional
      summary:     "{{ ... }}"   # Optional
      description: "{{ ... }}"   # Optional
      source_url:  "{{ ... }}"   # Optional
      starts_at:   "{{ ... }}"   # Optional — RFC3339
      ends_at:     "{{ ... }}"   # Optional — RFC3339

    extra_labels:             # Optional static/templated labels
      key: "value or {{ template }}"
```

---

## Worked example: Zendesk

### In Zendesk

1. Go to **Admin → Apps & Integrations → Webhooks → Create webhook**.
2. Set:
   - **Endpoint URL**: `https://your-tarka-host/sources/zendesk/ingest`
   - **HTTP method**: POST
   - **Request format**: JSON
   - **Authentication**: HMAC-SHA256, secret of your choice → copy to `http_sources.yaml`
3. Create a **Trigger** that fires on ticket create/update and calls your webhook.

### Example payload Zendesk sends

```json
{
  "ticket": {
    "id": "ZD-1001",
    "subject": "Database queries taking >10s",
    "status": "open",
    "priority": "high",
    "created_at": "2024-01-15T10:00:00Z",
    "url": "https://yourcompany.zendesk.com/tickets/1001",
    "description": "Users reporting slow page loads. Started after deploy at 09:45.",
    "tags": ["team-platform", "db", "performance"]
  }
}
```

### Mapping config

```yaml
sources:
  - id: zendesk
    name: "Zendesk Support"
    secret: "your-signing-secret"
    signature_header: "X-Zendesk-Webhook-Secret"
    field_map:
      fingerprint: "{{ payload.ticket.id }}"
      alertname: "{{ payload.ticket.subject }}"
      severity: >-
        {{ 'critical' if payload.ticket.priority == 'urgent'
           else 'warning' if payload.ticket.priority == 'high'
           else 'info' }}
      summary: "{{ payload.ticket.subject }} [{{ payload.ticket.id }}]"
      description: "{{ payload.ticket.description | truncate(500) }}"
      source_url: "{{ payload.ticket.url }}"
      starts_at: "{{ payload.ticket.created_at }}"
      status: >-
        {{ 'resolved' if payload.ticket.status in ['solved', 'closed']
           else 'firing' }}
    extra_labels:
      source: "zendesk"
      team: "{{ payload.ticket.tags | select('startswith', 'team-') | first | default('unknown') }}"
```

### Test with curl

```bash
curl -X POST https://your-tarka-host/sources/zendesk/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "ticket": {
      "id": "ZD-1001",
      "subject": "Database queries taking >10s",
      "status": "open",
      "priority": "high",
      "created_at": "2024-01-15T10:00:00Z",
      "url": "https://yourcompany.zendesk.com/tickets/1001",
      "description": "Users reporting slow page loads.",
      "tags": ["team-platform", "db"]
    }
  }'
# Expected: HTTP 202 {"ok": true, "source_id": "zendesk", "enqueued": 1}
```

---

## Worked example: Generic webhook (minimal)

For any system that sends a simple JSON payload:

```yaml
sources:
  - id: my-tool
    name: "My Internal Tool"
    field_map:
      fingerprint: "{{ payload.id }}"
      alertname: "{{ payload.title }}"
      status: "{{ 'resolved' if payload.status == 'resolved' else 'firing' }}"
    extra_labels:
      source: "my-tool"
```

```bash
curl -X POST https://your-tarka-host/sources/my-tool/ingest \
  -H "Content-Type: application/json" \
  -d '{"id": "evt-42", "title": "High error rate", "status": "firing"}'
```

---

## Troubleshooting

**HTTP 404 `unknown source`** — The `id` in the URL doesn't match any entry in `http_sources.yaml`. Check that `HTTP_SOURCE_CONFIG_FILE` is set and points to the right file, and that the source `id` matches exactly.

**HTTP 401 `invalid signature`** — The computed HMAC doesn't match. Common causes:
- Secret mismatch between Tarka config and the external system
- The external system is signing a transformed payload (Tarka signs the raw bytes — make sure there's no JSON re-serialisation)
- Wrong `signature_prefix` — try setting it to `""` and check what the header actually contains

**HTTP 400 `field_map.fingerprint rendered to an empty string`** — The Jinja2 template for `fingerprint` returned nothing. Check the payload structure matches the path in your template (e.g. `payload.ticket.id` requires `{"ticket": {"id": "..."}}`).

**Event arrives but no case is created** — The event was enqueued but the worker filtered it out. Check:
- `ALERTNAME_ALLOWLIST` — if set, `alertname` must be in the list
- The case may be snoozed
- Look at worker logs for `skipping` messages

**Template errors in logs** — Undefined template paths are logged at DEBUG level and render as empty string. Set `LOG_LEVEL=debug` to see which templates are failing.
