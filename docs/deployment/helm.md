# Deploying Tarka via Helm

Step-by-step guide for installing Tarka on any Kubernetes cluster using the official Helm chart.

For the full values reference, see [docs/guides/helm-chart.md](../guides/helm-chart.md).

---

## Prerequisites

- Kubernetes 1.20+
- Helm 3.12+ (OCI registry support required)
- S3-compatible object storage for report persistence (AWS S3, MinIO, GCS via S3 API, etc.)
- Prometheus and Alertmanager reachable from within the cluster

---

## Step 1: Choose your image variant

All images are built on [Chainguard](https://www.chainguard.dev/) hardened bases and run as nonroot.

| Tag suffix | LLM enrichment | GitHub evidence | Typical use |
|------------|:---:|:---:|-------------|
| *(none)* | No | No | Alert triage without AI |
| `-llm` | Yes | No | AI-enriched reports, no GitHub context |
| `-full` | Yes | Yes | Full feature set |

The chart default (`tag: ""`) resolves to the bare tag for the chart's `appVersion`. To use a different variant set `image.tag` explicitly — see [Step 3](#step-3-create-a-values-file).

---

## Step 2: Generate required secrets

The chart requires these secrets at install time. Generate them before creating your values file:

```bash
# Session signing key (256-bit)
openssl rand -hex 32

# Admin password
openssl rand -base64 16 | tr -d '/+='
```

---

## Step 3: Create a values file

Pull the default values as a starting point:

```bash
helm show values oci://ghcr.io/tarkyaio/charts/tarka > my-values.yaml
```

At minimum, set the following. The sections below cover common configurations.

### Minimal (deterministic triage, no LLM)

```yaml
config:
  data:
    PROMETHEUS_URL: "http://prometheus.monitoring.svc:9090"
    ALERTMANAGER_URL: "http://alertmanager.monitoring.svc:9093"
    CLUSTER_NAME: "my-cluster"
    S3_BUCKET: "my-tarka-reports"
    AUTH_PUBLIC_BASE_URL: "https://tarka.mycompany.com"

secrets:
  provider: "static"
  data:
    AUTH_SESSION_SECRET: "<output of openssl rand -hex 32>"
    ADMIN_INITIAL_PASSWORD: "<secure-password>"
```

### With LLM enrichment (Vertex AI)

Use the `-llm` image and add LLM config:

```yaml
image:
  tag: "0.3.2-llm"

config:
  data:
    PROMETHEUS_URL: "http://prometheus.monitoring.svc:9090"
    ALERTMANAGER_URL: "http://alertmanager.monitoring.svc:9093"
    CLUSTER_NAME: "my-cluster"
    S3_BUCKET: "my-tarka-reports"
    AUTH_PUBLIC_BASE_URL: "https://tarka.mycompany.com"
    LLM_ENABLED: "true"
    LLM_PROVIDER: "vertexai"
    LLM_MODEL: "gemini-2.5-flash"
    GOOGLE_CLOUD_PROJECT: "my-gcp-project"
    GOOGLE_CLOUD_LOCATION: "us-central1"

secrets:
  provider: "static"
  data:
    AUTH_SESSION_SECRET: "<openssl rand -hex 32>"
    ADMIN_INITIAL_PASSWORD: "<secure-password>"
    GCP_WIF_CRED_JSON: ""  # mount separately or use Workload Identity
```

For GCP Vertex AI authentication options (Workload Identity vs. key file), see [docs/guides/environment-variables.md](../guides/environment-variables.md#llm-vertex-ai----used-by-case-chat--optional-report-time-enrichment).

### With GitHub evidence (requires `-full` image)

```yaml
image:
  tag: "0.3.2-full"

config:
  data:
    # ... other required values ...
    GITHUB_EVIDENCE_ENABLED: "true"
    CHAT_ALLOW_GITHUB_READ: "true"
    GITHUB_DEFAULT_ORG: "myorg"

secrets:
  provider: "static"
  data:
    # ... other required secrets ...
    GITHUB_APP_ID: "123456"
    GITHUB_APP_INSTALLATION_ID: "78901234"
    GITHUB_APP_PRIVATE_KEY: |
      -----BEGIN RSA PRIVATE KEY-----
      <contents of your .pem file>
      -----END RSA PRIVATE KEY-----
```

For GitHub App setup, see [docs/guides/github-app-setup.md](../guides/github-app-setup.md).

### With OIDC authentication

```yaml
config:
  data:
    # ... other required values ...
    AUTH_PUBLIC_BASE_URL: "https://tarka.mycompany.com"
    AUTH_ALLOWED_DOMAINS: "mycompany.com"

secrets:
  provider: "static"
  data:
    # ... other required secrets ...
    OIDC_DISCOVERY_URL: "https://accounts.google.com/.well-known/openid-configuration"
    OIDC_CLIENT_ID: "123456-abcdef.apps.googleusercontent.com"
    OIDC_CLIENT_SECRET: "GOCSPX-..."
```

For full OIDC provider instructions (Google, Okta, Azure AD, Auth0), see [docs/guides/authentication.md](../guides/authentication.md).

### Using AWS Secrets Manager (ExternalSecrets Operator)

```yaml
secrets:
  provider: "external"
  data:
    AUTH_SESSION_SECRET: "AUTH_SESSION_SECRET"
    ADMIN_INITIAL_PASSWORD: "ADMIN_INITIAL_PASSWORD"
    OIDC_CLIENT_SECRET: "OIDC_CLIENT_SECRET"
  externalSecrets:
    backendType: "SecretsManager"
    region: "us-east-1"
    remoteSecretName: "tarka"
    roleArn: "arn:aws:iam::123456789012:role/eso-tarka-role"
```

Requires the [ExternalSecrets Operator](https://external-secrets.io/) installed in the cluster.

---

## Step 4: Install

```bash
helm install tarka oci://ghcr.io/tarkyaio/charts/tarka \
  --namespace tarka --create-namespace \
  --version 0.3.2 \
  -f my-values.yaml
```

---

## Step 5: Verify

```bash
# All pods should reach Running within ~60s
kubectl -n tarka get pods

# Check webhook logs for startup errors
kubectl -n tarka logs -f deployment/tarka-webhook

# Confirm the health endpoint
kubectl -n tarka port-forward svc/tarka-webhook 8080:8080 &
curl http://localhost:8080/healthz
```

---

## Step 6: Expose the UI

### Port-forward (quick check)

```bash
kubectl -n tarka port-forward svc/tarka-ui 8080:80
# Open http://localhost:8080
```

### Ingress

```yaml
ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: tarka.mycompany.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: tarka-tls
      hosts:
        - tarka.mycompany.com
```

### Gateway API HTTPRoute

```yaml
httpRoute:
  enabled: true
  parentRefs:
    - name: my-gateway
      sectionName: https
  hostnames:
    - tarka.mycompany.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
```

---

## Step 7: Wire up Alertmanager

```yaml
# alertmanager.yml
receivers:
  - name: tarka
    webhook_configs:
      - url: "http://tarka-webhook.tarka.svc:8080/alerts"
        send_resolved: false

route:
  receiver: tarka
  group_by: [alertname, cluster, namespace]
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h
```

---

## Upgrading

```bash
helm upgrade tarka oci://ghcr.io/tarkyaio/charts/tarka \
  --version 0.3.3 \
  -f my-values.yaml \
  -n tarka
```

The webhook and worker deployments have a `checksum/config` annotation, so ConfigMap changes trigger a rolling restart automatically.

---

## Uninstalling

```bash
helm uninstall tarka -n tarka
```

PersistentVolumeClaims from the NATS and PostgreSQL subcharts are retained by default. Delete them manually if no longer needed:

```bash
kubectl -n tarka delete pvc --all
```

---

## Further reading

- [Helm chart values reference](../guides/helm-chart.md) — full list of configurable values
- [Environment variables reference](../guides/environment-variables.md) — all `config.data` keys explained
- [Authentication guide](../guides/authentication.md) — OIDC providers, local users, security
- [GitHub App setup](../guides/github-app-setup.md) — required for `GITHUB_EVIDENCE_ENABLED=true`
