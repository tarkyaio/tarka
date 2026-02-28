# Development Mock Services

This directory contains lightweight mock services for local development.

## Services

- **mock-prometheus.py**: Mock Prometheus API server (port 18481)
- **mock-alertmanager.py**: Mock Alertmanager API server (port 19093)
- **mock-logs.py**: Mock VictoriaLogs API server (port 19471)

## Purpose

These mock services allow you to:
- Test Tarka locally without real infrastructure
- Verify the full alert processing pipeline works
- Develop and debug without port-forwarding

All services return valid but empty responses (no alerts, no metrics, no logs).

## Usage

Normally started via Docker Compose:

```bash
make dev-up    # Starts all services
make dev-down  # Stops all services
```

Or run manually for debugging:

```bash
# Install dependencies
pip install -r requirements.txt

# Run individual service
python mock-prometheus.py     # Port 18481
python mock-alertmanager.py   # Port 19093
python mock-logs.py          # Port 19471
```

## Health Checks

All services expose a `/healthz` endpoint:

```bash
curl http://localhost:18481/healthz
curl http://localhost:19093/healthz
curl http://localhost:19471/healthz
```

## Using Real Services

To use real Prometheus/Alertmanager/Logs instead:

1. Port-forward your services:
   ```bash
   kubectl port-forward svc/prometheus 9090:9090
   ```

2. Update `.env`:
   ```bash
   PROMETHEUS_URL=http://localhost:9090/api/v1
   ```

3. Restart the agent

See [Local Development Guide](../docs/guides/local-development.md) for details.
