# Operations Guide

How to operate Tarka in production: webhook setup, smoke testing, troubleshooting.

## Webhook Architecture

The agent runs as an in-cluster webhook service receiving alerts from Alertmanager in real-time.

### Data Flow

```
Alertmanager → FastAPI Receiver → NATS JetStream → Workers → S3 + PostgreSQL
```

1. **Alertmanager** sends webhook notifications to the agent Service endpoint
2. **Receiver** (`/alerts` endpoint) processes **firing** alerts only (resolved are ignored)
3. **Receiver enqueues** jobs to **NATS JetStream** and returns **202 Accepted** quickly
4. **Workers** consume jobs asynchronously, run investigations, write reports to S3
5. **Deduplication**: One report per `(identity + family + 4h bucket)` via HEAD-before-PUT on S3

### Rollout-Noisy Alerts

Some Kubernetes alerts are noisy during rollouts because pod names (and thus fingerprints) churn.

For these alertnames, the agent derives the owning workload via Kubernetes `ownerReferences` and applies a **1-hour freshness gate**:

- `KubernetesPodNotHealthy`
- `KubernetesContainerOomKiller` (deduped per workload + container)

If a report exists and is newer than 1 hour, the agent skips investigation. If older, it refreshes by overwriting the same S3 key.

### Components

**Receiver Process:**
```bash
python main.py --serve-webhook
```
- FastAPI HTTP server
- Receives Alertmanager webhooks on `/alerts`
- Publishes jobs to NATS JetStream
- Returns 202 quickly (no blocking investigation)

**Worker Process:**
```bash
python main.py --run-worker
```
- Consumes from NATS JetStream
- Runs investigation pipeline
- Writes reports to S3
- Indexes metadata to PostgreSQL
- Handles retries and DLQ

**Console API:**
- FastAPI endpoints at `/api/v1/*`
- Serves case browsing, chat, action proposals
- Requires Google OIDC authentication

## Kubernetes Deployment

### Manifests

- [`k8s/deployment.yaml`](../../k8s/deployment.yaml): Webhook receiver deployment
- [`k8s/service.yaml`](../../k8s/service.yaml): ClusterIP service `tarka-webhook` on port 8080
- [`k8s/console-ui-deployment.yaml`](../../k8s/console-ui-deployment.yaml): React frontend
- [`k8s/console-ui-service.yaml`](../../k8s/console-ui-service.yaml): UI service
- [`k8s/alertmanager-receiver-snippet.yaml`](../../k8s/alertmanager-receiver-snippet.yaml): Alertmanager config snippet

### NATS JetStream Setup

The agent requires NATS JetStream for the work queue:

```yaml
# nats-jetstream.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: nats
  namespace: tarka
spec:
  serviceName: nats
  replicas: 1
  selector:
    matchLabels:
      app: nats
  template:
    metadata:
      labels:
        app: nats
    spec:
      containers:
      - name: nats
        image: nats:latest
        args:
        - "-js"
        - "-m"
        - "8222"
        ports:
        - containerPort: 4222
          name: client
        - containerPort: 8222
          name: monitoring
```

**Environment Variables:**
```bash
NATS_URL=nats://nats.tarka.svc.cluster.local:4222
JETSTREAM_STREAM=TARKA
JETSTREAM_SUBJECT=tarka.alerts
JETSTREAM_DURABLE=WORKERS
```

### Worker Deployment

Deploy workers separately from the receiver:

```yaml
# worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tarka-worker
  namespace: tarka
spec:
  replicas: 2  # Scale based on load
  template:
    spec:
      containers:
      - name: worker
        image: tarka:latest
        command: ["python", "main.py", "--run-worker"]
        env:
        - name: NATS_URL
          value: "nats://nats.tarka.svc.cluster.local:4222"
        - name: WORKER_CONCURRENCY
          value: "2"
```

## Dead Letter Queue (DLQ)

Workers publish failed/poison jobs to the DLQ for later inspection:

**Configuration:**
- Subject: `JETSTREAM_DLQ_SUBJECT` (default: `tarka.dlq`)
- Stream: `JETSTREAM_DLQ_STREAM` (default: `TARKA_DLQ`)

**Inspecting DLQ:**
```bash
# View DLQ stream info
nats stream info TARKA_DLQ

# View recent DLQ messages
nats stream view TARKA_DLQ --count 10
```

## Smoke Testing (Real Cluster)

Lightweight checklist to validate the agent produces actionable reports against a live cluster.

### Prerequisites

- Kubernetes context set (read-only access sufficient)
- Port-forward or direct access to:
  - Alertmanager
  - Prometheus/VictoriaMetrics (PromQL endpoint)
  - Logs backend (VictoriaLogs)

### Environment Setup

```bash
export ALERTMANAGER_URL="http://localhost:19093"
export PROMETHEUS_URL="http://localhost:18481/select/0/prometheus"
export LOGS_URL="http://localhost:19471"
```

### Smoke Test Steps

**1. List alerts and pick a real alert:**
```bash
python main.py --list-alerts
```

**2. Run investigation by fingerprint (preferred):**
```bash
python main.py --fingerprint <fp_prefix> --time-window 30m
```

**3. Verify the report quality:**

Check that the report:
- **Triage section** exists and is honest about blockers (Scenarios A-D) and scope
- **Enrichment section** provides:
  - Plausible `suspected_*` label
  - Concrete K8s evidence (phase, waiting reason, events, exit code)
  - PromQL-first next steps (plus `kubectl` fallback)
- **Logs section**:
  - If logs exist: `logs=ok` with entries shown
  - If logs don't exist: `logs=empty` (not "unavailable")
  - If backend is broken: `logs=unavailable` + reason/backend/query

## Console UI Access

The repo includes an SRE Console UI (React frontend) that talks to the agent's Console API.

**No Ingress is provided by design.** Use port-forward for access:

```bash
kubectl -n tarka port-forward svc/tarka-ui 3000:80
```

Then open `http://localhost:3000`.

### Authentication

The Console API is gated by **Google SSO (OIDC)**:

```bash
# Required environment variables
export GOOGLE_OAUTH_CLIENT_ID="your-client-id"
export GOOGLE_OAUTH_CLIENT_SECRET="your-client-secret"
export AUTH_SESSION_SECRET="random-secret-32-chars"
export AUTH_PUBLIC_BASE_URL="http://localhost:3000"  # or production URL
export AUTH_ALLOWED_DOMAINS="yourcompany.com"
```

**Local/dev note:**
- For port-forward (HTTP), set `AUTH_PUBLIC_BASE_URL` to `http://localhost:3000`
- Add `http://localhost:3000/api/auth/callback/google` to Google OAuth redirect URIs

## Troubleshooting

### Webhook Not Receiving Alerts

**Problem:** Alertmanager sends webhooks but no investigations are triggered.

**Solution:**
1. Check receiver logs: `kubectl logs -n tarka deployment/tarka-webhook`
2. Verify Alertmanager configuration points to correct Service endpoint
3. Check Service/Endpoints: `kubectl get svc,endpoints -n tarka`
4. Test webhook endpoint: `curl http://tarka-webhook.tarka.svc.cluster.local:8080/health`

### Workers Not Processing Jobs

**Problem:** Jobs enqueued but not processed.

**Solution:**
1. Check worker logs: `kubectl logs -n tarka deployment/tarka-worker`
2. Verify NATS connection: Check `NATS_URL` environment variable
3. Check JetStream stream exists:
   ```bash
   kubectl exec -n tarka nats-0 -- nats stream info TARKA
   ```
4. Check backlog:
   ```bash
   kubectl exec -n tarka nats-0 -- nats stream view TARKA
   ```

### High Memory Usage

**Problem:** Workers consuming too much memory.

**Solution:**
1. Reduce worker concurrency: Set `WORKER_CONCURRENCY=1`
2. Reduce fetch batch size: Set `WORKER_FETCH_BATCH=5`
3. Add resource limits in deployment manifest
4. Scale horizontally (more pods with lower concurrency)

### S3 Write Failures

**Problem:** Reports not appearing in S3.

**Solution:**
1. Verify S3 credentials are configured correctly
2. Check S3 bucket permissions (PutObject, GetObject, HeadObject)
3. Verify `S3_BUCKET` and `S3_PREFIX` environment variables
4. Check worker logs for S3 errors

### Logs Not Appearing in Reports

**Problem:** Reports show `logs=unavailable`.

**Solution:**
1. Verify VictoriaLogs endpoint: `curl $LOGS_URL/select/logsql/query`
2. Check logs provider configuration in worker logs
3. Verify VictoriaLogs has ingested logs for the time window
4. Check namespace/pod label matching in log queries

## Monitoring

### Metrics

TODO: Prometheus metrics for receiver and workers (planned).

### Key Metrics to Track

- **Enqueue rate**: Jobs/second published to JetStream
- **Processing latency**: Time from enqueue to S3 write
- **Error rate**: Failed investigations / DLQ messages
- **Backlog depth**: Pending jobs in JetStream stream
- **Dedupe rate**: Skipped investigations due to existing reports

### Logs

**Receiver logs:**
```bash
kubectl logs -n tarka deployment/tarka-webhook --tail=100 -f
```

**Worker logs:**
```bash
kubectl logs -n tarka deployment/tarka-worker --tail=100 -f
```

**NATS logs:**
```bash
kubectl logs -n tarka statefulset/nats --tail=100 -f
```

## Scaling

### Horizontal Scaling

Scale workers based on backlog depth:

```bash
# Manual scaling
kubectl scale deployment/tarka-worker --replicas=5 -n tarka

# HPA (example)
kubectl autoscale deployment/tarka-worker \
  --min=2 --max=10 \
  --cpu-percent=70 \
  -n tarka
```

### Vertical Scaling

Increase worker concurrency for larger pods:

```yaml
env:
- name: WORKER_CONCURRENCY
  value: "4"  # Default: 2
resources:
  requests:
    memory: "2Gi"
    cpu: "1000m"
  limits:
    memory: "4Gi"
    cpu: "2000m"
```

## Notes

- Logs backend is **VictoriaLogs** (not Loki)
- The agent operates in read-only mode (never mutates cluster state)
- Reports are deduplicated automatically via S3 HEAD-before-PUT
- Workers are stateless and can be scaled horizontally
