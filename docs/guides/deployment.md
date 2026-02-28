# Deployment Guide

How to deploy Tarka to Kubernetes for production use.

> **🚀 Quick AWS EKS Deployment**: For one-click AWS EKS deployment with automated setup, see **[DEPLOYMENT.md](../../DEPLOYMENT.md)** - includes configuration validation, secret generation, GCP Workload Identity Federation setup, and comprehensive troubleshooting.

## Architecture Overview

The agent runs as multiple components in Kubernetes:

1. **Webhook Receiver**: Receives alerts from Alertmanager, publishes to queue
2. **NATS JetStream**: Durable message queue for async job processing
3. **Workers**: Consume jobs, run investigations, write to S3
4. **Console UI**: React frontend for case browsing and chat
5. **PostgreSQL**: (Optional) Case memory and metadata indexing

## Prerequisites

- Kubernetes cluster (1.20+)
- S3-compatible object storage (AWS S3, MinIO, etc.)
- (Optional) PostgreSQL database for case memory
- (Optional) Google OAuth credentials for Console UI authentication

## Quick Start

```bash
# Clone repository
git clone https://github.com/your-org/tarka.git
cd tarka

# Create namespace
kubectl create namespace tarka

# Configure secrets (see Configuration section)
kubectl create secret generic tarka-secrets \
  --from-literal=s3-access-key-id="YOUR_KEY" \
  --from-literal=s3-secret-access-key="YOUR_SECRET" \
  -n tarka

# Deploy NATS JetStream
kubectl apply -f k8s/nats-jetstream.yaml -n tarka

# Deploy webhook receiver
kubectl apply -f k8s/deployment.yaml -n tarka
kubectl apply -f k8s/service.yaml -n tarka

# Deploy workers
kubectl apply -f k8s/worker-deployment.yaml -n tarka

# (Optional) Deploy Console UI
kubectl apply -f k8s/console-ui-deployment.yaml -n tarka
kubectl apply -f k8s/console-ui-service.yaml -n tarka
```

## Configuration

### ConfigMap

Create a ConfigMap with environment variables:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tarka-config
  namespace: tarka
data:
  # Required: Prometheus
  PROMETHEUS_URL: "http://victoria-metrics.monitoring.svc.cluster.local:8428/select/0/prometheus"

  # Required: Alertmanager
  ALERTMANAGER_URL: "http://alertmanager.monitoring.svc.cluster.local:9093"

  # Optional: Logs backend
  LOGS_URL: "http://victoria-logs.monitoring.svc.cluster.local:9428"

  # Required: S3 storage
  S3_BUCKET: "tarka-reports"
  S3_PREFIX: "investigations/"
  S3_ENDPOINT_URL: "https://s3.amazonaws.com"  # or MinIO URL

  # Required: NATS JetStream
  NATS_URL: "nats://nats.tarka.svc.cluster.local:4222"
  JETSTREAM_STREAM: "TARKA"
  JETSTREAM_SUBJECT: "tarka.alerts"
  JETSTREAM_DURABLE: "WORKERS"

  # Worker configuration
  WORKER_CONCURRENCY: "2"
  WORKER_FETCH_BATCH: "10"
  JETSTREAM_ACK_WAIT_SECONDS: "1800"  # 30 minutes
  JETSTREAM_MAX_DELIVER: "5"

  # Optional: LLM enrichment
  LLM_ENABLED: "false"
  LLM_PROVIDER: "vertex"
  LLM_MODEL: "gemini-2.5-flash"

  # Optional: PostgreSQL for case memory
  POSTGRES_HOST: "postgres.tarka.svc.cluster.local"
  POSTGRES_PORT: "5432"
  POSTGRES_DB: "tarka"
  POSTGRES_USER: "tarka"

  # Optional: Console authentication
  AUTH_MODE: "oidc"  # or "basic", "both", "disabled"
  AUTH_PUBLIC_BASE_URL: "https://sre-console.yourcompany.com"
  AUTH_ALLOWED_DOMAINS: "yourcompany.com"
```

### Secrets

Create a Secret for sensitive credentials:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tarka-secrets
  namespace: tarka
type: Opaque
stringData:
  # S3 credentials
  S3_ACCESS_KEY_ID: "YOUR_AWS_ACCESS_KEY"
  S3_SECRET_ACCESS_KEY: "YOUR_AWS_SECRET_KEY"

  # PostgreSQL password (if using case memory)
  POSTGRES_PASSWORD: "your-postgres-password"

  # Google OAuth (if using Console UI)
  GOOGLE_OAUTH_CLIENT_ID: "your-client-id.apps.googleusercontent.com"
  GOOGLE_OAUTH_CLIENT_SECRET: "your-client-secret"
  AUTH_SESSION_SECRET: "random-32-char-secret-for-sessions"

  # Vertex AI (if using LLM enrichment)
  GOOGLE_APPLICATION_CREDENTIALS: |
    {
      "type": "service_account",
      "project_id": "your-project",
      ...
    }
```

## Component Deployment

### NATS JetStream

Deploy NATS with JetStream enabled:

```yaml
# k8s/nats-jetstream.yaml
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
        image: nats:2.10-alpine
        args:
        - "-js"
        - "-m"
        - "8222"
        - "-sd"
        - "/data"
        ports:
        - containerPort: 4222
          name: client
        - containerPort: 8222
          name: monitoring
        volumeMounts:
        - name: data
          mountPath: /data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 10Gi
---
apiVersion: v1
kind: Service
metadata:
  name: nats
  namespace: tarka
spec:
  selector:
    app: nats
  ports:
  - name: client
    port: 4222
  - name: monitoring
    port: 8222
```

### Webhook Receiver

The receiver accepts Alertmanager webhooks and enqueues jobs:

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tarka-webhook
  namespace: tarka
spec:
  replicas: 2
  selector:
    matchLabels:
      app: tarka-webhook
  template:
    metadata:
      labels:
        app: tarka-webhook
    spec:
      serviceAccountName: tarka
      containers:
      - name: webhook
        image: tarka:latest
        command: ["python", "main.py", "--serve-webhook"]
        ports:
        - containerPort: 8080
        envFrom:
        - configMapRef:
            name: tarka-config
        - secretRef:
            name: tarka-secrets
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
```

### Worker Pool

Workers consume jobs from NATS and run investigations:

```yaml
# k8s/worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tarka-worker
  namespace: tarka
spec:
  replicas: 3  # Scale based on load
  selector:
    matchLabels:
      app: tarka-worker
  template:
    metadata:
      labels:
        app: tarka-worker
    spec:
      serviceAccountName: tarka
      containers:
      - name: worker
        image: tarka:latest
        command: ["python", "main.py", "--run-worker"]
        envFrom:
        - configMapRef:
            name: tarka-config
        - secretRef:
            name: tarka-secrets
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
```

### ServiceAccount and RBAC

The agent needs read-only Kubernetes access:

```yaml
# k8s/rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tarka
  namespace: tarka
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: tarka-readonly
rules:
- apiGroups: [""]
  resources: ["pods", "pods/log", "events", "services", "endpoints"]
  verbs: ["get", "list"]
- apiGroups: ["apps"]
  resources: ["deployments", "replicasets", "statefulsets", "daemonsets"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: tarka-readonly-binding
subjects:
- kind: ServiceAccount
  name: tarka
  namespace: tarka
roleRef:
  kind: ClusterRole
  name: tarka-readonly
  apiGroup: rbac.authorization.k8s.io
```

## Alertmanager Configuration

Configure Alertmanager to send webhooks to the agent:

```yaml
# alertmanager.yml
receivers:
- name: tarka
  webhook_configs:
  - url: http://tarka-webhook.tarka.svc.cluster.local:8080/alerts
    send_resolved: true

route:
  receiver: tarka
  group_by: ['alertname', 'cluster', 'namespace']
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h
```

## Scaling Considerations

### Horizontal Scaling

**Receivers:**
- Scale based on incoming webhook rate
- Typically 2-3 replicas sufficient
- Stateless, can scale freely

**Workers:**
- Scale based on queue depth and processing latency
- Use HPA based on custom metrics (queue lag)
- Recommended: Start with 3 replicas, scale to 10+ as needed

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

## Storage Requirements

### S3 Bucket

- **Size**: ~100KB per report
- **Retention**: Configure lifecycle policies based on needs
- **Structure**: `s3://bucket/prefix/YYYY-MM-DD/fingerprint-family.json`

### PostgreSQL (Optional)

- **Purpose**: Case memory, metadata indexing, chat history
- **Size**: ~50MB per 1000 cases (without embeddings)
- **Schema**: Auto-migrated on first run

## Monitoring

### Health Checks

**Receiver:**
```bash
curl http://tarka-webhook.tarka.svc.cluster.local:8080/health
```

**NATS:**
```bash
curl http://nats.tarka.svc.cluster.local:8222/healthz
```

### Logs

```bash
# Receiver logs
kubectl logs -n tarka deployment/tarka-webhook -f

# Worker logs
kubectl logs -n tarka deployment/tarka-worker -f

# NATS logs
kubectl logs -n tarka statefulset/nats -f
```

### Metrics

TODO: Prometheus metrics endpoint (planned feature).

## Troubleshooting

See [operations.md](operations.md#troubleshooting) for detailed troubleshooting guide.

## Security Best Practices

1. **Least privilege**: Use read-only ServiceAccount for K8s access
2. **Network policies**: Restrict traffic to necessary services only
3. **Secrets management**: Use external secrets operator (e.g., Sealed Secrets, External Secrets)
4. **Authentication**: Enable Google OIDC for Console UI access
5. **TLS**: Use Ingress with TLS for production Console UI

## Upgrades

Rolling updates are safe for all components:

```bash
# Update receiver
kubectl set image deployment/tarka-webhook webhook=tarka:v1.2.0 -n tarka

# Update workers (no downtime, jobs redelivered)
kubectl set image deployment/tarka-worker worker=tarka:v1.2.0 -n tarka
```

Workers support graceful shutdown - in-progress investigations complete before pod termination.
