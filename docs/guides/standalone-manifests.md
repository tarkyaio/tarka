# Standalone Manifest Deployment

Deploy Tarka to Kubernetes using the raw manifests in `deploy/manifests/`. This approach is suitable when you want full control over each resource or cannot use Helm.

For the recommended Helm-based deployment, see [helm-chart.md](helm-chart.md).

## Prerequisites

- Kubernetes cluster (1.20+)
- S3-compatible object storage (AWS S3, MinIO, etc.)
- `kubectl` configured for your cluster
- (Optional) PostgreSQL database for case memory
- (Optional) Google OAuth credentials for Console UI authentication

## Quick Start

```bash
# Clone repository
git clone https://github.com/tarkyaio/tarka.git
cd tarka

# Create namespace
kubectl create namespace tarka

# Apply RBAC and ServiceAccount
kubectl apply -f deploy/manifests/namespace.yaml
kubectl apply -f deploy/manifests/rbac.yaml
kubectl apply -f deploy/manifests/serviceAccount.yaml

# Create ConfigMap (edit values first)
kubectl apply -f deploy/manifests/configMap.yaml

# Create Secret (edit values first)
kubectl apply -f deploy/manifests/secret.yaml

# Deploy NATS JetStream
kubectl apply -f deploy/manifests/natsJetstream.yaml

# Deploy webhook receiver
kubectl apply -f deploy/manifests/deployment.yaml
kubectl apply -f deploy/manifests/service.yaml

# Deploy workers
kubectl apply -f deploy/manifests/workerDeployment.yaml

# (Optional) Deploy Console UI
kubectl apply -f deploy/manifests/uiDeployment.yaml
kubectl apply -f deploy/manifests/uiService.yaml

# (Optional) Deploy dev PostgreSQL
kubectl apply -f deploy/manifests/postgresDev.yaml
```

> The manifests contain `REPLACE_ME_*` placeholders. The `deploy.sh` script handles substitution automatically. If deploying manually, replace these values before applying.

## Configuration

### ConfigMap

Create a ConfigMap with environment variables (`deploy/manifests/configMap.yaml`):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tarka-config
  namespace: tarka
data:
  # Required: Prometheus
  PROMETHEUS_URL: "http://prometheus.monitoring.svc:9090"

  # Required: Alertmanager
  ALERTMANAGER_URL: "http://alertmanager.monitoring.svc:9093"

  # Optional: Logs backend
  LOGS_URL: "http://victoria-logs.monitoring.svc:9428"

  # Required: S3 storage
  S3_BUCKET: "tarka-reports"
  S3_PREFIX: "tarka/reports"

  # Required: NATS JetStream
  NATS_URL: "nats://nats.tarka.svc:4222"
  JETSTREAM_STREAM: "TARKA"
  JETSTREAM_SUBJECT: "tarka.alerts"

  # Worker configuration
  WORKER_CONCURRENCY: "2"
  WORKER_FETCH_BATCH: "10"
  JETSTREAM_ACK_WAIT_SECONDS: "1800"
  JETSTREAM_MAX_DELIVER: "5"

  # Optional: LLM enrichment
  LLM_ENABLED: "false"
  LLM_PROVIDER: "vertexai"
  LLM_MODEL: "gemini-2.5-flash"

  # Optional: PostgreSQL for case memory
  POSTGRES_HOST: ""
  POSTGRES_PORT: "5432"
  POSTGRES_DB: "tarka"
  POSTGRES_USER: "tarka"

  # Optional: Console authentication
  AUTH_PUBLIC_BASE_URL: "https://tarka.yourcompany.com"
  AUTH_ALLOWED_DOMAINS: "yourcompany.com"
```

See `deploy/manifests/configMap.yaml` for the full list of 80+ configuration keys.

### Secrets

Create a Secret for sensitive credentials (`deploy/manifests/secret.yaml`):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tarka
  namespace: tarka
type: Opaque
stringData:
  POSTGRES_PASSWORD: "your-postgres-password"
  GOOGLE_OAUTH_CLIENT_ID: "your-client-id.apps.googleusercontent.com"
  GOOGLE_OAUTH_CLIENT_SECRET: "your-client-secret"
  AUTH_SESSION_SECRET: "random-64-char-hex-secret"
  ADMIN_INITIAL_USERNAME: "admin"
  ADMIN_INITIAL_PASSWORD: "your-admin-password"
```

See `deploy/manifests/secret.yaml` for the full list of secret keys.

## Component Details

### NATS JetStream

Deployed as a StatefulSet with persistent storage:

```bash
kubectl apply -f deploy/manifests/natsJetstream.yaml
```

- Single replica with JetStream enabled
- 10Gi persistent volume for message durability
- Ports: 4222 (client), 8222 (monitoring)

### Webhook Receiver

Receives Alertmanager webhooks and enqueues jobs:

```bash
kubectl apply -f deploy/manifests/deployment.yaml
kubectl apply -f deploy/manifests/service.yaml
```

- Command: `python main.py --serve-webhook --host 0.0.0.0 --port 8080`
- Health endpoint: `/healthz` on port 8080
- Resources: 50m/512Mi requests, 500m/1Gi limits

### Worker Pool

Consumes jobs from NATS and runs investigations:

```bash
kubectl apply -f deploy/manifests/workerDeployment.yaml
```

- Command: `python main.py --run-worker`
- Resources: 100m/512Mi requests, 1000m/1Gi limits
- Supports graceful shutdown (in-progress investigations complete before pod termination)

### Console UI

React frontend for case browsing and chat:

```bash
kubectl apply -f deploy/manifests/uiDeployment.yaml
kubectl apply -f deploy/manifests/uiService.yaml
```

- Separate container image (`tarka-ui`)
- Resources: 25m/64Mi requests, 150m/256Mi limits
- Served on port 80

### ServiceAccount and RBAC

The agent needs read-only Kubernetes access:

```bash
kubectl apply -f deploy/manifests/rbac.yaml
kubectl apply -f deploy/manifests/serviceAccount.yaml
```

Grants read-only access to pods, events, serviceaccounts, replicasets, deployments, statefulsets, daemonsets, and jobs.

For EKS, update the ServiceAccount annotation with your IRSA role ARN:

```yaml
annotations:
  eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/tarka-role
```

### PostgreSQL (Dev)

Optional in-cluster PostgreSQL with pgvector support:

```bash
kubectl apply -f deploy/manifests/postgresDev.yaml
```

- Image: `pgvector/pgvector:pg16`
- 10Gi persistent volume
- For production, use RDS or a managed Postgres service instead

## Automated Deployment with deploy.sh

The `deploy.sh` script automates the full deployment including AWS infrastructure setup:

```bash
# Set required variables
export CLUSTER_NAME=my-cluster
export IMAGE_NAME=tarka
export UI_IMAGE_NAME=tarka-ui
export PROMETHEUS_URL=http://prometheus.monitoring.svc:9090
export ALERTMANAGER_URL=http://alertmanager.monitoring.svc:9093
export AUTH_PUBLIC_BASE_URL=https://tarka.yourcompany.com
export GOOGLE_CLOUD_PROJECT=my-gcp-project
export GCP_WIF_AUDIENCE="//iam.googleapis.com/projects/..."

# Run deployment
./deploy.sh
```

The script handles: Docker image builds, ECR push, S3 bucket creation, IAM role setup, AWS Secrets Manager sync, and K8s resource deployment with all placeholders substituted.

See [DEPLOYMENT.md](../../DEPLOYMENT.md) for full details.

## Alertmanager Configuration

Configure Alertmanager to send webhooks to the agent:

```yaml
# alertmanager.yml
receivers:
  - name: tarka
    webhook_configs:
      - url: http://tarka-webhook.tarka.svc:8080/alerts
        send_resolved: true

route:
  receiver: tarka
  group_by: ['alertname', 'cluster', 'namespace']
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h
```

## Scaling

### Horizontal Scaling

**Webhook receivers** are stateless and can scale freely. 2-3 replicas is typically sufficient.

**Workers** should scale based on queue depth and processing latency. Start with 1-2 replicas and scale as needed.

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: tarka-worker-hpa
  namespace: tarka
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: tarka-worker
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Vertical Scaling

Increase worker concurrency for better resource utilization:

```yaml
env:
  - name: WORKER_CONCURRENCY
    value: "4"  # 2-4 per worker pod
resources:
  requests:
    memory: "2Gi"  # ~500Mi per concurrent investigation
    cpu: "1000m"
```

## Troubleshooting

See [operations.md](operations.md#troubleshooting) for a detailed troubleshooting guide.
