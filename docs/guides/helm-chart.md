# Helm Chart Deployment

Deploy Tarka to Kubernetes using the official Helm chart, published as an OCI artifact to GitHub Container Registry.

For raw manifest deployment without Helm, see [standalone-manifests.md](standalone-manifests.md).

## Prerequisites

- Kubernetes cluster (1.20+)
- Helm 3.12+ (OCI registry support)
- S3-compatible object storage (AWS S3, MinIO, etc.)

## Quick Start

```bash
# Install with minimal configuration
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka \
  --namespace tarka --create-namespace \
  --set config.data.PROMETHEUS_URL=http://prometheus.monitoring.svc:9090 \
  --set config.data.ALERTMANAGER_URL=http://alertmanager.monitoring.svc:9093 \
  --set config.data.S3_BUCKET=tarka-reports \
  --set config.data.CLUSTER_NAME=my-cluster
```

To pin a specific version:

```bash
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka --version 0.3.1
```

## Values Overview

The chart is configured through `values.yaml`. Create a custom values file for your environment:

```bash
helm show values oci://ghcr.io/tarkyaio/charts/tarka > my-values.yaml
# Edit my-values.yaml, then:
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka -f my-values.yaml -n tarka --create-namespace
```

### Images

The chart defaults to GHCR-hosted images. The tag defaults to `Chart.appVersion`.

```yaml
image:
  repository: "ghcr.io/tarkyaio/tarka"
  tag: ""              # defaults to appVersion
  pullPolicy: IfNotPresent

ui:
  image:
    repository: "ghcr.io/tarkyaio/tarka-ui"
    tag: ""
```

To use a private registry:

```yaml
image:
  repository: "123456789.dkr.ecr.us-east-1.amazonaws.com/tarka"
  tag: "0.3.1"
imagePullSecrets:
  - name: ecr-credentials
```

### Components

The chart deploys three components, each independently configurable:

```yaml
webhook:
  replicaCount: 2
  resources:
    requests: { cpu: "50m", memory: "512Mi" }
    limits:   { cpu: "500m", memory: "1Gi" }

worker:
  replicaCount: 2
  resources:
    requests: { cpu: "100m", memory: "512Mi" }
    limits:   { cpu: "1000m", memory: "1Gi" }

ui:
  enabled: true
  replicaCount: 1
  resources:
    requests: { cpu: "25m", memory: "64Mi" }
    limits:   { cpu: "150m", memory: "256Mi" }
```

### Application Configuration

All application config keys live under `config.data`. These map directly to the environment variables the application reads:

```yaml
config:
  data:
    # Required
    PROMETHEUS_URL: "http://prometheus.monitoring.svc:9090"
    ALERTMANAGER_URL: "http://alertmanager.monitoring.svc:9093"
    S3_BUCKET: "tarka-reports"
    CLUSTER_NAME: "my-cluster"

    # Optional: LLM enrichment
    LLM_ENABLED: "true"
    LLM_PROVIDER: "vertexai"
    LLM_MODEL: "gemini-2.5-flash"

    # Optional: Slack integration
    SLACK_DEFAULT_CHANNEL: "#sre-alerts"

    # Optional: Console auth
    AUTH_PUBLIC_BASE_URL: "https://tarka.yourcompany.com"
    AUTH_ALLOWED_DOMAINS: "yourcompany.com"
```

To use an existing ConfigMap instead of having the chart create one:

```yaml
config:
  existingConfigMap: "my-tarka-config"
```

### Secret Management

The chart supports two providers plus an escape hatch for pre-existing secrets. Both providers use the same `secrets.data` map; the meaning of each value depends on the provider.

#### Static (default)

Values in `secrets.data` are literal secret content. The chart creates a Kubernetes Secret:

```yaml
secrets:
  provider: "static"
  data:
    AUTH_SESSION_SECRET: "your-64-char-hex-secret"
    ADMIN_INITIAL_PASSWORD: "your-admin-password"
    POSTGRES_PASSWORD: "your-db-password"
    ANTHROPIC_API_KEY: "sk-ant-..."
```

Empty values are omitted from the rendered Secret.

#### External (ExternalSecrets Operator)

Values in `secrets.data` are remote reference locations. The chart creates `SecretStore` and `ExternalSecret` CRDs that pull secrets from AWS Secrets Manager or SSM Parameter Store via the [ExternalSecrets Operator](https://external-secrets.io/).

**SecretsManager** (values are JSON property names within the ASM secret):

```yaml
secrets:
  provider: "external"
  data:
    AUTH_SESSION_SECRET: "AUTH_SESSION_SECRET"
    ADMIN_INITIAL_PASSWORD: "ADMIN_INITIAL_PASSWORD"
    POSTGRES_PASSWORD: "POSTGRES_PASSWORD"
    ANTHROPIC_API_KEY: "ANTHROPIC_API_KEY"
  externalSecrets:
    backendType: "SecretsManager"
    region: "us-east-1"
    remoteSecretName: "tarka"     # ASM secret name (used as remoteRef.key)
    roleArn: ""                   # optional: IAM role for ESO to assume
    refreshInterval: "1h"
```

**ParameterStore** (values are full SSM parameter paths):

```yaml
secrets:
  provider: "external"
  data:
    AUTH_SESSION_SECRET: "/tarka/AUTH_SESSION_SECRET"
    ADMIN_INITIAL_PASSWORD: "/tarka/ADMIN_INITIAL_PASSWORD"
    POSTGRES_PASSWORD: "/tarka/POSTGRES_PASSWORD"
  externalSecrets:
    backendType: "ParameterStore"
    region: "us-east-1"
```

Only keys with non-empty values are included in the ExternalSecret. The ExternalSecrets Operator must be installed separately.

#### Existing Secret (escape hatch)

To skip all Secret/ExternalSecret creation and reference a pre-existing Secret:

```yaml
secrets:
  existingSecret: "my-tarka-secrets"
```

The Secret must contain keys matching what the application expects (e.g., `POSTGRES_PASSWORD`, `AUTH_SESSION_SECRET`). All keys are referenced as `optional: true`, so missing keys will not prevent startup.

### ServiceAccount and Cloud Identity

```yaml
serviceAccount:
  create: true
  provider: "eks-irsa"
  eksRoleArn: "arn:aws:iam::123456789012:role/tarka-role"
```

Supported providers:

| Provider | Annotation | Notes |
|----------|-----------|-------|
| `eks-irsa` | `eks.amazonaws.com/role-arn` | Standard EKS IAM Roles for Service Accounts |
| `eks-pod-identity` | (none) | Association is created outside the cluster via AWS API |
| `""` (default) | (none) | Use `annotations` for custom identity setups |

For other identity systems (GKE WIF, Azure Workload Identity, etc.), use the generic escape hatch:

```yaml
serviceAccount:
  annotations:
    iam.gke.io/gcp-service-account: tarka@my-project.iam.gserviceaccount.com
```

### RBAC

The chart creates a ClusterRole with read-only access to pods, events, deployments, statefulsets, daemonsets, and jobs. Disable if you manage RBAC externally:

```yaml
rbac:
  create: false
```

### NATS (Subchart)

NATS JetStream is deployed as a subchart (enabled by default). When enabled, the chart auto-configures `NATS_URL` to point to the in-cluster service.

```yaml
nats:
  enabled: true
  # Subchart values: https://github.com/nats-io/k8s
  config:
    jetstream:
      enabled: true
      fileStore:
        pvc:
          size: 10Gi
```

To use an external NATS instance instead:

```yaml
nats:
  enabled: false

config:
  data:
    NATS_URL: "nats://external-nats.example.com:4222"
```

### PostgreSQL (Subchart)

PostgreSQL is deployed as a subchart (disabled by default). When enabled, the chart auto-configures `POSTGRES_HOST`.

```yaml
postgres:
  enabled: true
  # Subchart values: https://artifacthub.io/packages/helm/cloudpirates-postgres/postgres
  auth:
    username: tarka
    database: tarka
```

To use an external database (e.g., RDS):

```yaml
postgres:
  enabled: false

config:
  data:
    POSTGRES_HOST: "my-rds.cluster-abc123.us-east-1.rds.amazonaws.com"

secrets:
  provider: "static"
  data:
    POSTGRES_DSN: "postgresql://tarka:password@my-rds.cluster-abc123.us-east-1.rds.amazonaws.com:5432/tarka"
```

### Ingress

```yaml
ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: tarka.yourcompany.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: tarka-tls
      hosts:
        - tarka.yourcompany.com
```

Gateway API HTTPRoute is also supported:

```yaml
httpRoute:
  enabled: true
  parentRefs:
    - name: my-gateway
      sectionName: https
  hostnames:
    - tarka.yourcompany.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
```

### Network Policies

The chart includes network policies for Calico, Cilium, and Istio, gated by `networkPolicy.provider`:

```yaml
networkPolicy:
  provider: "calico"   # "calico" | "cilium" | "istio" | "" (disabled)
```

**Calico** renders standard Kubernetes `NetworkPolicy` resources. **Cilium** renders `CiliumNetworkPolicy` with DNS-aware FQDN egress rules. **Istio** renders `PeerAuthentication` and `AuthorizationPolicy` resources.

Each component gets a policy:
- **Webhook**: ingress on 8080, egress to DNS/NATS/Postgres/HTTPS
- **Worker**: no ingress, egress to DNS/NATS/Postgres/HTTPS
- **UI**: ingress on 80, egress to webhook only

Add custom rules per component or globally:

```yaml
networkPolicy:
  provider: "calico"
  extraEgress:           # appended to all components
    - ports:
        - port: 9090
          protocol: TCP
  webhook:
    extraIngress:        # webhook-only
      - from:
          - namespaceSelector:
              matchLabels:
                name: monitoring
```

Cilium-specific DNS egress patterns:

```yaml
networkPolicy:
  provider: "cilium"
  cilium:
    dnsEgress:
      - "*.custom-api.internal"
```

Istio-specific settings:

```yaml
networkPolicy:
  provider: "istio"
  istio:
    mtls: STRICT
    allowNamespaces:
      - monitoring
      - ingress-nginx
```

### Service Catalog

Mount service catalog files as a ConfigMap:

```bash
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka \
  --set serviceCatalog.enabled=true \
  --set-file 'serviceCatalog.files.service-catalog\.yaml=config/service-catalog.yaml' \
  --set-file 'serviceCatalog.files.third-party-catalog\.yaml=config/third-party-catalog.yaml'
```

### Extensibility

#### Extra containers and init containers

Add sidecars or init containers to any component:

```yaml
webhook:
  extraContainers:
    - name: otel-collector
      image: otel/opentelemetry-collector:latest
      ports:
        - containerPort: 4317
  extraInitContainers:
    - name: wait-for-nats
      image: busybox
      command: ['sh', '-c', 'until nc -z nats 4222; do sleep 1; done']
```

The same fields are available on `worker` and `ui`.

#### Extra volumes

```yaml
webhook:
  extraVolumes:
    - name: gcp-wif-cred
      secret:
        secretName: tarka
        items:
          - key: GCP_WIF_CRED_JSON
            path: wif-cred.json
  extraVolumeMounts:
    - name: gcp-wif-cred
      mountPath: /var/run/gcp
      readOnly: true
```

#### Extra manifests

Render arbitrary Kubernetes resources alongside the chart:

```yaml
extraManifests:
  - apiVersion: monitoring.coreos.com/v1
    kind: PodMonitor
    metadata:
      name: tarka-webhook
    spec:
      selector:
        matchLabels:
          app.kubernetes.io/component: webhook
      podMetricsEndpoints:
        - port: http
```

## Upgrading

```bash
helm upgrade tarka oci://ghcr.io/tarkyaio/charts/tarka \
  --version 0.3.1 \
  -f my-values.yaml \
  -n tarka
```

The webhook and worker deployments include a `checksum/config` annotation, so ConfigMap changes automatically trigger a rolling restart.

## Uninstalling

```bash
helm uninstall tarka -n tarka
```

This removes all chart-managed resources. PersistentVolumeClaims from subcharts (NATS, PostgreSQL) are retained by default.

## Alertmanager Configuration

Configure Alertmanager to send webhooks to Tarka. The service name depends on your Helm release name:

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

## Troubleshooting

See [operations.md](operations.md#troubleshooting) for a detailed troubleshooting guide.
