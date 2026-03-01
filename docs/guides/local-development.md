# Local Development Guide

This guide helps you set up a complete local development environment for Tarka.

## Quick Start

Get up and running in 3 steps:

**Platform Note:** This setup works on macOS (Intel & Apple Silicon), Linux (amd64 & arm64), and Windows with Docker Desktop. All images are multi-architecture and run natively on your platform.

1. **Copy environment template**:
   ```bash
   cp .env.example .env
   ```

2. **Start local services** (PostgreSQL, NATS, mock monitoring services):
   ```bash
   make dev-up
   ```
   Note: PostgreSQL starts empty. Migrations run in the next step.

3. **Start webhook server** (Terminal 2):
   ```bash
   make dev-serve
   ```
   This will:
   - Run database migrations (creates tables, vector extension, admin user)
   - Start the API server on http://localhost:8080

4. **Start UI dev server** (Terminal 3):
   ```bash
   make dev-ui
   ```
   This will:
   - Install UI dependencies (if needed)
   - Start Vite dev server on http://localhost:5173
   - Proxy `/api/*` requests to backend

5. **Open UI**: Navigate to http://localhost:5173
   - Username: `admin`
   - Password: `admin123` (or what you set in `.env`)
   - Hot module reloading enabled for UI development

6. **Stop services** when done:
   ```bash
   make dev-down
   ```

## Available Services

When you run `make dev-up`, the following services start:

| Service | Port | URL | Purpose |
|---------|------|-----|---------|
| Webhook Server | 8080 | http://localhost:8080 | Backend API (start with `make dev-serve`) |
| UI Dev Server | 5173 | http://localhost:5173 | Frontend UI (start with `make dev-ui`) |
| PostgreSQL | 5432 | localhost:5432 | Database with pgvector |
| NATS JetStream | 4222 | nats://localhost:4222 | Job queue |
| NATS Monitoring | 8222 | http://localhost:8222 | NATS metrics |
| Mock Prometheus | 18481 | http://localhost:18481 | Metrics API (returns empty data) |
| Mock Alertmanager | 19093 | http://localhost:19093 | Alerts API (returns empty data) |
| Mock VictoriaLogs | 19471 | http://localhost:19471 | Logs API (returns empty data) |

## Development Workflow

### Basic Workflow

```bash
# Terminal 1: Start infrastructure services
make dev-up          # PostgreSQL, NATS, mock services (database is empty)

# Terminal 2: Start webhook server (runs migrations first)
make dev-serve       # Migrations + API server

# Terminal 3: Start UI dev server
make dev-ui          # Vite dev server with HMR

# Terminal 4: Start worker (processes alerts)
make dev-worker

# Terminal 4: Send test alert
make dev-send-alert

# Check logs
make dev-logs
```

### Using Real Services

To use real Prometheus/Alertmanager/VictoriaLogs instead of mocks:

1. **Port-forward your services**:
   ```bash
   # In separate terminals or background
   kubectl port-forward -n monitoring svc/prometheus 9090:9090
   kubectl port-forward -n monitoring svc/alertmanager 9093:9093
   kubectl port-forward -n monitoring svc/victorialogs 9428:9428
   ```

2. **Update `.env`**:
   ```bash
   # Comment out mock URLs and use real ones
   PROMETHEUS_URL=http://localhost:9090/api/v1
   ALERTMANAGER_URL=http://localhost:9093
   LOGS_URL=http://localhost:9428
   ```

3. **Restart webhook server**:
   ```bash
   # Ctrl+C in terminal 2, then:
   make dev-serve
   ```

Now the agent will query real metrics, logs, and alerts!

### Testing the Full Flow

To test the complete alert → investigation → report pipeline:

1. **Start all services**:
   ```bash
   make dev-up
   ```

2. **Start webhook server** (Terminal 1):
   ```bash
   make dev-serve
   ```

3. **Start worker** (Terminal 2):
   ```bash
   make dev-worker
   ```

4. **Send test alert** (Terminal 3):
   ```bash
   make dev-send-alert
   ```

5. **Watch the worker logs** (Terminal 2):
   - You'll see the alert picked up from NATS
   - Evidence collection (using mock data)
   - Diagnostic checks
   - Base triage analysis
   - Report generation

The worker will log:
```
INFO: Investigation started for alert PodCPUThrottling (fingerprint: test-fp-12345)
INFO: Evidence collection complete (using mock data)
INFO: Running diagnostics...
INFO: Base triage complete
INFO: Investigation complete
```

### Sending Custom Alerts

You can send custom alerts by modifying the webhook payload:

```bash
curl -X POST http://localhost:8080/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "status": "firing",
    "receiver": "webhook",
    "groupLabels": {"alertname": "YourAlert"},
    "commonLabels": {
      "alertname": "PodNotHealthy",
      "severity": "critical",
      "namespace": "production",
      "pod": "api-server-abc123",
      "cluster": "prod-us-west"
    },
    "commonAnnotations": {
      "summary": "Pod api-server-abc123 is not healthy"
    },
    "externalURL": "http://alertmanager:9093",
    "alerts": [{
      "status": "firing",
      "labels": {
        "alertname": "PodNotHealthy",
        "severity": "critical",
        "namespace": "production",
        "pod": "api-server-abc123",
        "cluster": "prod-us-west"
      },
      "annotations": {
        "summary": "Pod not healthy"
      },
      "startsAt": "2024-01-15T10:00:00Z",
      "fingerprint": "custom-fp-123"
    }]
  }'
```

## Running Tests

### Unit Tests Only

```bash
make test
```

### Integration Tests (requires NATS)

```bash
# Services must be running
make dev-up
make test-integration
```

### E2E Tests (full stack)

```bash
# Starts services, runs backend + UI tests
make dev-test
```

### All Tests

```bash
make dev-up
make test-all
```

## Environment Configuration

The `.env` file controls all configuration. Key variables:

### Authentication (Required)

```bash
# Session secret for signing cookies
# Generate: openssl rand -hex 32
AUTH_SESSION_SECRET=your-secret-here-must-be-at-least-64-chars

# Initial admin credentials
ADMIN_INITIAL_USERNAME=admin
ADMIN_INITIAL_PASSWORD=admin123
```

### Database (Auto-configured)

```bash
MEMORY_ENABLED=true
DB_AUTO_MIGRATE=true
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=tarka
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
```

### Monitoring Services

```bash
# Use mocks (default)
PROMETHEUS_URL=http://localhost:18481/select/0/prometheus
ALERTMANAGER_URL=http://localhost:19093
LOGS_URL=http://localhost:19471

# Or use real services (port-forwarded)
# PROMETHEUS_URL=http://localhost:9090/api/v1
# ALERTMANAGER_URL=http://localhost:9093
# LOGS_URL=http://localhost:9428
```

### LLM (Optional)

```bash
LLM_ENABLED=false
LLM_MOCK=true

# To enable real LLM enrichment (Vertex AI)
# LLM_ENABLED=true
# LLM_MOCK=false
# GOOGLE_CLOUD_PROJECT=your-project
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

### OIDC (Optional)

```bash
# For testing SSO integration
# OIDC_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
# OIDC_CLIENT_ID=your-client-id
# OIDC_CLIENT_SECRET=your-client-secret
# AUTH_PUBLIC_BASE_URL=http://localhost:8080
```

## Troubleshooting

### "Database not configured" error

**Symptom**: Webhook server fails to start with database connection error.

**Solution**:
```bash
# 1. Ensure services are running
make dev-logs

# 2. Check PostgreSQL is healthy
docker ps | grep postgres

# 3. Restart services
make dev-restart
```

### "NATS connection failed"

**Symptom**: Worker can't connect to NATS.

**Solution**:
```bash
# 1. Check NATS is running
docker ps | grep nats

# 2. Check NATS health
curl http://localhost:8222/healthz

# 3. Restart NATS
make dev-restart
```

### No alerts appear in UI

**Symptom**: Alert list is empty.

**Solution**: This is expected with mock services! They return empty data.

To see real alerts:
1. Port-forward Alertmanager: `kubectl port-forward svc/alertmanager 9093:9093`
2. Update `.env`: `ALERTMANAGER_URL=http://localhost:9093`
3. Restart server: `make dev-serve`

### "Session secret not configured"

**Symptom**: Webhook server fails to start with authentication error.

**Solution**:
```bash
# Generate a secure secret
openssl rand -hex 32

# Add to .env
echo "AUTH_SESSION_SECRET=$(openssl rand -hex 32)" >> .env

# Restart server
make dev-serve
```

### Port already in use

**Symptom**: `bind: address already in use` error.

**Solution**:
```bash
# Find and kill process using port (e.g., 8080)
lsof -ti:8080 | xargs kill -9

# Or use different ports in .env
```

### Mock services not returning data

**Symptom**: Expected behavior! Mock services return empty results.

Mock services are designed to:
- Accept valid requests
- Return empty but valid responses
- Allow the agent to run without errors

This lets you test the full pipeline without real infrastructure.

### Database migration errors

**Symptom**: Migrations fail or tables missing.

**Important**: Migrations run when the **webhook server starts**, not when PostgreSQL starts.

**Solution**:
```bash
# 1. Stop everything
make dev-down

# 2. Restart infrastructure services
make dev-up

# 3. Start webhook server (this runs migrations)
make dev-serve

# 4. Check migrations in server logs
# You should see: "Running migrations..." and "Migrations complete"
```

Verify migration status:
```bash
docker exec -it tarka-postgres psql -U postgres -d tarka -c "\dt"
# Should show: local_users, cases, chat_sessions, chat_messages, etc.
```

## Fresh Start

To start completely fresh (wipe all data):

```bash
# Remove all containers, volumes, and artifacts
make dev-clean

# Start fresh
make dev-up
```

## CI/CD Integration

The local development setup is designed for CI:

```yaml
# .github/workflows/e2e.yml (example)
jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install Poetry
        run: pip install poetry
      - name: Install dependencies
        run: poetry install
      - name: Start services
        run: make dev-up
      - name: Run e2e tests
        run: make dev-test
      - name: Stop services
        run: make dev-down
```

## Next Steps

- **Production Deployment**: See [deployment guide](./deployment.md) (if exists)
- **Authentication Setup**: See [authentication guide](./authentication.md)
- **Contributing**: See [CONTRIBUTING.md](../../CONTRIBUTING.md) (if exists)
- **Architecture**: See [CLAUDE.md](../../CLAUDE.md) for system design

## Getting Help

- **Logs**: `make dev-logs` shows all service logs
- **Health checks**:
  - Webhook: http://localhost:8080/healthz
  - NATS: http://localhost:8222/healthz
  - Mock Prometheus: http://localhost:18481/healthz
- **Issues**: Report bugs at https://github.com/your-org/tarka/issues (update URL)

## Advanced Topics

### Running Multiple Workers

To handle high alert volume:

```bash
# Terminal 1: Webhook server
make dev-serve

# Terminal 2-4: Workers
make dev-worker  # Repeat in multiple terminals
```

Workers share the NATS queue, so alerts are distributed automatically.

### Debugging Investigations

To debug specific alerts:

```bash
# CLI mode (single alert, no queue)
poetry run python main.py --list-alerts
poetry run python main.py --alert 0 --dump-json investigation > debug.json

# Then inspect the JSON
cat debug.json | jq '.evidence'
cat debug.json | jq '.analysis.hypotheses'
```

### Using Real LLM

To test with real LLM enrichment:

```bash
# 1. Set up Google Cloud credentials
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
export GOOGLE_CLOUD_PROJECT=your-project

# 2. Update .env
LLM_ENABLED=true
LLM_MOCK=false

# 3. Restart server
make dev-serve
```

Note: This will incur Google Cloud costs!
