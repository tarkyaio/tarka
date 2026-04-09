# Tarka Helm Chart

Helm chart for [Tarka](https://github.com/tarkyaio/tarka), an AI-powered Kubernetes alert triage system that converts Prometheus/Alertmanager alerts into actionable investigation reports.

## Install

```bash
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka \
  --namespace tarka --create-namespace \
  --set config.data.PROMETHEUS_URL=http://prometheus.monitoring.svc:9090 \
  --set config.data.ALERTMANAGER_URL=http://alertmanager.monitoring.svc:9093 \
  --set config.data.S3_BUCKET=tarka-reports \
  --set config.data.CLUSTER_NAME=my-cluster
```

Pin a version:

```bash
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka --version 0.2.0
```

## Components

| Component | Description | Default |
|-----------|-------------|---------|
| **Webhook** | API server and Alertmanager receiver | Enabled |
| **Worker** | Async investigation processor | Enabled |
| **Console UI** | React frontend for case browsing and chat | Enabled |
| **NATS** | JetStream message queue (subchart) | Enabled |
| **PostgreSQL** | Case memory and metadata (subchart) | Disabled |

## Values

### Images

| Key | Default | Description |
|-----|---------|-------------|
| `image.repository` | `ghcr.io/tarkyaio/tarka` | Agent image (webhook + worker) |
| `image.tag` | `""` | Defaults to `appVersion` |
| `image.pullPolicy` | `IfNotPresent` | |
| `ui.image.repository` | `ghcr.io/tarkyaio/tarka-ui` | Console UI image |
| `ui.image.tag` | `""` | Defaults to `appVersion` |

### Webhook

| Key | Default | Description |
|-----|---------|-------------|
| `webhook.replicaCount` | `1` | |
| `webhook.consoleAuthMode` | `"oidc"` | Auth mode: `oidc`, `basic`, `both` |
| `webhook.resources.requests.cpu` | `50m` | |
| `webhook.resources.requests.memory` | `512Mi` | |
| `webhook.resources.limits.cpu` | `500m` | |
| `webhook.resources.limits.memory` | `1Gi` | |
| `webhook.service.type` | `ClusterIP` | |
| `webhook.service.port` | `8080` | |
| `webhook.autoscaling.enabled` | `false` | Enable HPA |
| `webhook.extraEnv` | `[]` | Extra env vars |
| `webhook.extraContainers` | `[]` | Sidecar containers |
| `webhook.extraInitContainers` | `[]` | Init containers |
| `webhook.extraVolumes` | `[]` | Extra volumes |
| `webhook.extraVolumeMounts` | `[]` | Extra volume mounts |

### Worker

| Key | Default | Description |
|-----|---------|-------------|
| `worker.replicaCount` | `1` | |
| `worker.resources.requests.cpu` | `100m` | |
| `worker.resources.requests.memory` | `512Mi` | |
| `worker.resources.limits.cpu` | `1000m` | |
| `worker.resources.limits.memory` | `1Gi` | |
| `worker.env.WORKER_CONCURRENCY` | `"2"` | Concurrent investigations per pod |
| `worker.env.JETSTREAM_MAX_DELIVER` | `"5"` | Max redelivery attempts |
| `worker.extraContainers` | `[]` | Sidecar containers |
| `worker.extraInitContainers` | `[]` | Init containers |

### Console UI

| Key | Default | Description |
|-----|---------|-------------|
| `ui.enabled` | `true` | Deploy the UI |
| `ui.replicaCount` | `1` | |
| `ui.resources.requests.cpu` | `25m` | |
| `ui.resources.requests.memory` | `64Mi` | |
| `ui.service.type` | `ClusterIP` | |
| `ui.service.port` | `80` | |

### ServiceAccount

| Key | Default | Description |
|-----|---------|-------------|
| `serviceAccount.create` | `true` | |
| `serviceAccount.provider` | `""` | `eks-irsa`, `eks-pod-identity`, or `""` |
| `serviceAccount.eksRoleArn` | `""` | IAM role ARN (for `eks-irsa`) |
| `serviceAccount.annotations` | `{}` | Generic annotations escape hatch |

### RBAC

| Key | Default | Description |
|-----|---------|-------------|
| `rbac.create` | `true` | Create read-only ClusterRole + binding |

### Application Config

All keys under `config.data` are rendered into a ConfigMap and injected via `envFrom`. See `values.yaml` for the full list of 80+ keys.

| Key | Default | Description |
|-----|---------|-------------|
| `config.existingConfigMap` | `""` | Use an existing ConfigMap instead |
| `config.data.PROMETHEUS_URL` | `""` | **Required** |
| `config.data.ALERTMANAGER_URL` | `""` | **Required** |
| `config.data.S3_BUCKET` | `""` | **Required** |
| `config.data.CLUSTER_NAME` | `""` | **Required** |
| `config.data.LLM_ENABLED` | `"false"` | Enable LLM enrichment |
| `config.data.LLM_PROVIDER` | `"vertexai"` | `vertexai` or `anthropic` |
| `config.data.CHAT_ENABLED` | `"true"` | Enable chat tools |
| `config.data.AWS_EVIDENCE_ENABLED` | `"false"` | AWS infra context |
| `config.data.GITHUB_EVIDENCE_ENABLED` | `"false"` | GitHub code context |

### Secrets

| Key | Default | Description |
|-----|---------|-------------|
| `secrets.provider` | `"static"` | `static` or `external` |
| `secrets.existingSecret` | `""` | Skip creation, reference existing Secret |
| `secrets.data.*` | `""` | Secret values (static) or remote refs (external) |
| `secrets.externalSecrets.backendType` | `"SecretsManager"` | `SecretsManager` or `ParameterStore` |
| `secrets.externalSecrets.region` | `"us-east-1"` | AWS region |
| `secrets.externalSecrets.remoteSecretName` | `"tarka"` | ASM secret name |
| `secrets.externalSecrets.roleArn` | `""` | IAM role for ESO |
| `secrets.externalSecrets.refreshInterval` | `"1h"` | Sync interval |

The `secrets.data` map serves both providers. For `static`, values are literal secret content. For `external`, values are remote reference locations (ASM JSON properties or SSM parameter paths).

### Subcharts

| Key | Default | Description |
|-----|---------|-------------|
| `nats.enabled` | `true` | Deploy NATS JetStream subchart |
| `postgres.enabled` | `false` | Deploy PostgreSQL subchart |

When a subchart is enabled, the chart auto-configures `NATS_URL` / `POSTGRES_HOST` to point to the in-cluster service. When disabled, set these in `config.data` to point to your external instances.

NATS subchart: [nats-io/k8s](https://github.com/nats-io/k8s)
PostgreSQL subchart: [cloudpirates/postgres](https://artifacthub.io/packages/helm/cloudpirates-postgres/postgres)

### Network Policies

| Key | Default | Description |
|-----|---------|-------------|
| `networkPolicy.provider` | `""` | `calico`, `cilium`, `istio`, or `""` (disabled) |
| `networkPolicy.extraIngress` | `[]` | Global extra ingress rules |
| `networkPolicy.extraEgress` | `[]` | Global extra egress rules |
| `networkPolicy.webhook.extraIngress` | `[]` | Webhook-only ingress rules |
| `networkPolicy.istio.mtls` | `STRICT` | Istio mTLS mode |
| `networkPolicy.cilium.dnsEgress` | `[]` | Extra FQDN patterns for Cilium |

### Ingress

| Key | Default | Description |
|-----|---------|-------------|
| `ingress.enabled` | `false` | |
| `ingress.className` | `""` | |
| `ingress.hosts` | `[]` | |
| `ingress.tls` | `[]` | |

Gateway API `httpRoute` is also supported with the same pattern.

### Extensibility

| Key | Default | Description |
|-----|---------|-------------|
| `extraManifests` | `[]` | Arbitrary YAML resources rendered alongside the chart |
| `serviceCatalog.enabled` | `false` | Mount service catalog files |
| `serviceCatalog.files` | `{}` | Map of filename to content |

## Examples

### Minimal (in-cluster NATS, static secrets)

```yaml
config:
  data:
    PROMETHEUS_URL: "http://prometheus.monitoring.svc:9090"
    ALERTMANAGER_URL: "http://alertmanager.monitoring.svc:9093"
    S3_BUCKET: "tarka-reports"
    CLUSTER_NAME: "prod-us-east-1"

secrets:
  data:
    AUTH_SESSION_SECRET: "change-me-64-char-hex"
    ADMIN_INITIAL_PASSWORD: "change-me"
```

### AWS EKS with ExternalSecrets

```yaml
serviceAccount:
  provider: "eks-irsa"
  eksRoleArn: "arn:aws:iam::123456789012:role/tarka"

config:
  data:
    PROMETHEUS_URL: "http://prometheus.monitoring.svc:9090"
    ALERTMANAGER_URL: "http://alertmanager.monitoring.svc:9093"
    S3_BUCKET: "tarka-prod"
    CLUSTER_NAME: "prod-us-east-1"
    LLM_ENABLED: "true"
    AWS_EVIDENCE_ENABLED: "true"

secrets:
  provider: "external"
  data:
    AUTH_SESSION_SECRET: "AUTH_SESSION_SECRET"
    ADMIN_INITIAL_PASSWORD: "ADMIN_INITIAL_PASSWORD"
    POSTGRES_PASSWORD: "POSTGRES_PASSWORD"
    ANTHROPIC_API_KEY: "ANTHROPIC_API_KEY"
    SLACK_BOT_TOKEN: "SLACK_BOT_TOKEN"
    SLACK_APP_TOKEN: "SLACK_APP_TOKEN"
  externalSecrets:
    backendType: "SecretsManager"
    region: "us-east-1"
    remoteSecretName: "tarka"

postgres:
  enabled: true

networkPolicy:
  provider: "calico"

ingress:
  enabled: true
  className: "alb"
  hosts:
    - host: tarka.example.com
      paths:
        - path: /
          pathType: Prefix
```

### External NATS and RDS

```yaml
nats:
  enabled: false

postgres:
  enabled: false

config:
  data:
    NATS_URL: "nats://nats.shared-infra.svc:4222"
    POSTGRES_HOST: "tarka.cluster-abc123.us-east-1.rds.amazonaws.com"
    PROMETHEUS_URL: "http://prometheus.monitoring.svc:9090"
    ALERTMANAGER_URL: "http://alertmanager.monitoring.svc:9093"
    S3_BUCKET: "tarka-reports"
    CLUSTER_NAME: "prod"

secrets:
  data:
    POSTGRES_DSN: "postgresql://tarka:password@tarka.cluster-abc123.us-east-1.rds.amazonaws.com:5432/tarka"
```

## Alertmanager Configuration

```yaml
receivers:
  - name: tarka
    webhook_configs:
      - url: http://<release>-tarka-webhook.<namespace>.svc:8080/alerts
        send_resolved: true

route:
  receiver: tarka
  group_by: ['alertname', 'cluster', 'namespace']
```

## License

See [LICENSE](../../LICENSE) in the repository root.
