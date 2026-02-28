# Deployment Guide

This guide provides comprehensive instructions for deploying Tarka to AWS EKS with GCP Vertex AI integration.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
- [GCP Workload Identity Federation Setup](#gcp-workload-identity-federation-setup)
- [OIDC Configuration](#oidc-configuration)
- [Troubleshooting](#troubleshooting)
- [Security Considerations](#security-considerations)
- [Advanced Usage](#advanced-usage)

---

## Prerequisites

Before deploying, ensure you have the following tools installed and configured:

### Required Tools

1. **AWS CLI** (v2.x or later)
   ```bash
   aws --version
   aws configure  # Configure with appropriate credentials
   ```

2. **kubectl** (version compatible with your EKS cluster)
   ```bash
   kubectl version --client
   ```

3. **Docker** (for building and pushing images)
   ```bash
   docker --version
   ```

4. **openssl** (for secret generation and certificate operations)
   ```bash
   openssl version
   ```

### AWS Infrastructure

1. **EKS Cluster**
   - Running EKS cluster with at least 2 worker nodes
   - IAM OIDC provider (required for IRSA)
     - **Auto-created by deploy.sh** if not present
     - Alternatively, create manually:
       ```bash
       aws iam create-open-id-connect-provider \
         --url $(aws eks describe-cluster --name <CLUSTER_NAME> --region <REGION> --query "cluster.identity.oidc.issuer" --output text) \
         --client-id-list sts.amazonaws.com
       ```

2. **ECR Repositories**
   - Create ECR repositories for agent and UI images:
     ```bash
     aws ecr create-repository --repository-name tarka --region us-east-1
     aws ecr create-repository --repository-name tarka-ui --region us-east-1
     ```

3. **Kubernetes Add-ons**
   - **External Secrets Operator** (required for AWS Secrets Manager integration)
     ```bash
     helm repo add external-secrets https://charts.external-secrets.io
     helm install external-secrets external-secrets/external-secrets \
       -n external-secrets-system --create-namespace
     ```

4. **Observability Stack** (required for data sources)
   - Prometheus (or compatible, e.g., VictoriaMetrics)
   - Alertmanager
   - Optional: VictoriaLogs or similar log aggregation

### GCP Resources

1. **GCP Project** with Vertex AI API enabled
   ```bash
   gcloud services enable aiplatform.googleapis.com
   ```

2. **Workload Identity Federation** configured (see [GCP WIF Setup](#gcp-workload-identity-federation-setup))

---

## Quick Start

### Step 1: Clone Repository

```bash
git clone <repository-url>
cd tarka
```

### Step 2: Configure Deployment

Copy the template and fill in your values:

```bash
cp .env.deploy.template .env.deploy
```

Edit `.env.deploy` with your configuration. At minimum, set:

```bash
# Required
CLUSTER_NAME=your-eks-cluster
IMAGE_NAME=tarka
UI_IMAGE_NAME=tarka-ui
PROMETHEUS_URL=http://prometheus.monitoring.svc:9090
ALERTMANAGER_URL=http://alertmanager.monitoring.svc:9093
S3_BUCKET=your-tarka-reports-bucket
AUTH_PUBLIC_BASE_URL=https://tarka.your-company.com
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GCP_WIF_AUDIENCE=//iam.googleapis.com/projects/123456789/locations/global/workloadIdentityPools/aws-pool/providers/aws-provider
```

See [Configuration Reference](#configuration-reference) for all options.

### Step 3: Set Up GCP Workload Identity Federation

Follow the detailed instructions in [GCP WIF Setup](#gcp-workload-identity-federation-setup) to configure cross-cloud authentication.

### Step 4: Run Deployment

```bash
source .env.deploy
./deploy.sh
```

The script will:
1. ✅ Validate all required configuration
2. ✅ Auto-generate secure secrets (if not provided)
3. ✅ Build and push Docker images
4. ✅ Create/update AWS resources (S3, IAM, Secrets Manager)
5. ✅ Deploy Kubernetes resources
6. ✅ Show deployment summary with credentials

### Step 5: Verify Deployment

```bash
kubectl -n tarka get pods

# Expected output:
# NAME                                      READY   STATUS    RESTARTS   AGE
# nats-0                                    1/1     Running   0          2m
# tarka-webhook-xxx                     1/1     Running   0          2m
# tarka-worker-xxx                      1/1     Running   0          2m
# tarka-console-ui-xxx                  1/1     Running   0          2m
# tarka-postgres-xxx                    1/1     Running   0          2m
```

### Step 6: Configure Alertmanager

Add the webhook to your Alertmanager configuration:

```yaml
# alertmanager.yml
receivers:
  - name: tarka
    webhook_configs:
      - url: 'http://tarka-webhook.tarka.svc:8080/alerts'
        send_resolved: false

route:
  receiver: tarka
  routes:
    - match:
        alertname: CPUThrottlingHigh
      receiver: tarka
    # Add more alert routing rules
```

### Step 7: Access Console UI

```bash
kubectl port-forward -n tarka svc/tarka-console-ui 8080:80
```

Open http://localhost:8080 and log in with the credentials shown in the deployment summary.

---

## Configuration Reference

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `CLUSTER_NAME` | EKS cluster name | `production-cluster` |
| `IMAGE_NAME` | ECR repository for agent image | `tarka` |
| `UI_IMAGE_NAME` | ECR repository for UI image | `tarka-ui` |
| `PROMETHEUS_URL` | Prometheus endpoint | `http://prometheus.monitoring.svc:9090` |
| `ALERTMANAGER_URL` | Alertmanager endpoint | `http://alertmanager.monitoring.svc:9093` |
| `S3_BUCKET` | S3 bucket for reports | `company-tarka-reports` |
| `AUTH_PUBLIC_BASE_URL` | Public URL of deployment | `https://tarka.company.com` |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | `my-gcp-project` |
| `GCP_WIF_AUDIENCE` | Workload Identity Federation audience | See [GCP WIF Setup](#gcp-workload-identity-federation-setup) |

### Optional Variables with Defaults

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `us-east-1` | AWS region |
| `IMAGE_TAG` | `latest` | Docker image tag |
| `S3_PREFIX` | `tarka/reports` | S3 prefix for reports |
| `LOGS_URL` | _(empty)_ | VictoriaLogs endpoint |
| `ADMIN_INITIAL_USERNAME` | `admin` | Initial admin username |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | GCP region for Vertex AI |
| `ENABLE_DEV_POSTGRES` | `1` | Enable in-cluster PostgreSQL |
| `ROLE_NAME` | `tarka-role` | IAM role name |
| `ASM_SECRET_NAME` | `tarka` | AWS Secrets Manager secret name |
| `TIME_WINDOW` | `1h` | Evidence collection time window |

### Auto-Generated Secrets

If not provided, these will be auto-generated:

- `AUTH_SESSION_SECRET`: 64-char hex (256 bits) for session cookie signing
- `POSTGRES_PASSWORD`: 32-char alphanumeric for PostgreSQL
- `ADMIN_INITIAL_PASSWORD`: 16-char alphanumeric for admin user

**Important:** Generated passwords are shown once after deployment. Save them securely!

### OIDC Configuration (Optional)

All three required if using OIDC:

| Variable | Description | Example |
|----------|-------------|---------|
| `OIDC_DISCOVERY_URL` | OIDC discovery endpoint | `https://accounts.google.com/.well-known/openid-configuration` |
| `OIDC_CLIENT_ID` | OAuth2 client ID | `123456-abcdef.apps.googleusercontent.com` |
| `OIDC_CLIENT_SECRET` | OAuth2 client secret | `GOCSPX-...` |
| `AUTH_ALLOWED_DOMAINS` | Restrict to domains (optional) | `company.com,contractor.com` |

---

## GCP Workload Identity Federation Setup

Workload Identity Federation allows the agent running in AWS EKS to authenticate to GCP Vertex AI without storing long-lived credentials.

### Overview

The setup creates a trust relationship between:
- **AWS**: EKS OIDC provider → IAM role → ServiceAccount
- **GCP**: Workload Identity Pool → AWS provider → Service Account

### Step-by-Step Setup

#### 1. Enable Required GCP APIs

```bash
gcloud services enable iam.googleapis.com
gcloud services enable iamcredentials.googleapis.com
gcloud services enable sts.googleapis.com
gcloud services enable aiplatform.googleapis.com
```

#### 2. Create GCP Service Account

```bash
export GCP_PROJECT_ID="your-gcp-project"
export GCP_SA_NAME="tarka-eks"
export GCP_SA_EMAIL="${GCP_SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create ${GCP_SA_NAME} \
  --project=${GCP_PROJECT_ID} \
  --display-name="Tarka (EKS)" \
  --description="Used by Tarka running in AWS EKS for Vertex AI access"
```

#### 3. Grant Vertex AI Permissions

```bash
gcloud projects add-iam-policy-binding ${GCP_PROJECT_ID} \
  --member="serviceAccount:${GCP_SA_EMAIL}" \
  --role="roles/aiplatform.user"
```

#### 4. Create Workload Identity Pool

```bash
export POOL_ID="aws-eks-pool"
export POOL_NAME="AWS EKS Workload Identity Pool"

gcloud iam workload-identity-pools create ${POOL_ID} \
  --project=${GCP_PROJECT_ID} \
  --location="global" \
  --display-name="${POOL_NAME}"
```

#### 5. Create AWS Provider in the Pool

```bash
export PROVIDER_ID="aws-eks-provider"
export AWS_ACCOUNT_ID="123456789012"  # Replace with your AWS account ID

gcloud iam workload-identity-pools providers create-aws ${PROVIDER_ID} \
  --project=${GCP_PROJECT_ID} \
  --location="global" \
  --workload-identity-pool=${POOL_ID} \
  --account-id="${AWS_ACCOUNT_ID}"
```

#### 6. Grant Service Account Access to Pool

Allow the AWS provider to impersonate the GCP service account:

```bash
# Get the pool's full resource name
export WORKLOAD_IDENTITY_POOL_ID=$(gcloud iam workload-identity-pools describe ${POOL_ID} \
  --project=${GCP_PROJECT_ID} \
  --location=global \
  --format="value(name)")

# Allow impersonation from AWS EKS
gcloud iam service-accounts add-iam-policy-binding ${GCP_SA_EMAIL} \
  --project=${GCP_PROJECT_ID} \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${WORKLOAD_IDENTITY_POOL_ID}/attribute.aws_role/arn:aws:sts::${AWS_ACCOUNT_ID}:assumed-role/tarka-role"
```

**Note:** Replace `tarka-role` with your `ROLE_NAME` if different.

#### 7. Get the Audience URL

The audience URL is required for `GCP_WIF_AUDIENCE`:

```bash
export GCP_PROJECT_NUMBER=$(gcloud projects describe ${GCP_PROJECT_ID} --format="value(projectNumber)")

echo "GCP_WIF_AUDIENCE=//iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
```

Copy this value to your `.env.deploy` file.

### Verification

Test the setup after deployment:

```bash
# Check pod can authenticate to GCP
kubectl -n tarka exec -it deploy/tarka-worker -- sh

# Inside pod, test GCP access
curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token

# Should return an access token
```

### Troubleshooting WIF

**Error: "Invalid audience"**
- Verify `GCP_WIF_AUDIENCE` matches the format exactly
- Check project number (not project ID) is used

**Error: "Permission denied on Vertex AI"**
- Verify service account has `roles/aiplatform.user`
- Check IAM policy bindings with:
  ```bash
  gcloud projects get-iam-policy ${GCP_PROJECT_ID} \
    --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:${GCP_SA_EMAIL}"
  ```

**Error: "Principal cannot impersonate service account"**
- Verify workloadIdentityUser binding includes correct AWS role ARN
- Check AWS IAM role name matches deployment

---

## OIDC Configuration

The agent supports SSO via any OIDC-compatible identity provider.

### Google Workspace

1. **Create OAuth 2.0 Client**
   - Go to [GCP Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
   - Create OAuth 2.0 Client ID
   - Application type: Web application
   - Authorized redirect URIs:
     ```
     https://tarka.company.com/auth/callback
     ```

2. **Configure Environment**
   ```bash
   OIDC_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
   OIDC_CLIENT_ID=123456-abcdef.apps.googleusercontent.com
   OIDC_CLIENT_SECRET=GOCSPX-...
   AUTH_ALLOWED_DOMAINS=company.com  # Optional: restrict to your domain
   ```

### Okta

1. **Create Application Integration**
   - Okta Admin Console → Applications → Create App Integration
   - Sign-in method: OIDC
   - Application type: Web Application
   - Redirect URIs:
     ```
     https://tarka.company.com/auth/callback
     ```

2. **Configure Environment**
   ```bash
   OIDC_DISCOVERY_URL=https://dev-123456.okta.com/.well-known/openid-configuration
   OIDC_CLIENT_ID=0oa...
   OIDC_CLIENT_SECRET=...
   ```

### Auth0

1. **Create Application**
   - Auth0 Dashboard → Applications → Create Application
   - Choose: Regular Web Application
   - Allowed Callback URLs:
     ```
     https://tarka.company.com/auth/callback
     ```

2. **Configure Environment**
   ```bash
   OIDC_DISCOVERY_URL=https://your-tenant.auth0.com/.well-known/openid-configuration
   OIDC_CLIENT_ID=...
   OIDC_CLIENT_SECRET=...
   ```

### Local Auth Fallback

If OIDC is not configured, local authentication is used:
- Username: `ADMIN_INITIAL_USERNAME` (default: `admin`)
- Password: `ADMIN_INITIAL_PASSWORD` (auto-generated if not set)

---

## Troubleshooting

### Deployment Fails Validation

**Symptom:** Script exits with "Configuration validation failed"

**Solution:** Check error messages for missing variables. Ensure all required variables in `.env.deploy` are set to non-empty values.

### Image Push Fails

**Symptom:** "denied: Your authorization token has expired"

**Solution:** Re-authenticate to ECR:
```bash
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com
```

### Pods Stuck in Pending

**Symptom:** Pods show `Pending` status

**Solution:**
1. Check node resources: `kubectl describe nodes`
2. Check pod events: `kubectl -n tarka describe pod <pod-name>`
3. Ensure cluster has sufficient capacity

### Pods Crash with "InvalidAudience"

**Symptom:** Pods crash with GCP authentication errors

**Solution:**
1. Verify `GCP_WIF_AUDIENCE` format matches exactly:
   ```
   //iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools/{POOL}/providers/{PROVIDER}
   ```
2. Use project **number**, not project ID
3. Check Workload Identity Pool and provider exist:
   ```bash
   gcloud iam workload-identity-pools list --location=global
   ```

### Secret Not Syncing

**Symptom:** `kubectl -n tarka get secret tarka` shows no data

**Solution:**
1. Check External Secrets Operator is running:
   ```bash
   kubectl -n external-secrets-system get pods
   ```
2. Check ExternalSecret status:
   ```bash
   kubectl -n tarka describe externalsecret tarka
   ```
3. Verify AWS Secrets Manager secret exists:
   ```bash
   aws secretsmanager describe-secret --secret-id tarka --region us-east-1
   ```

### No Alerts Processed

**Symptom:** Webhook receives alerts but no investigations run

**Solution:**
1. Check NATS is running: `kubectl -n tarka logs statefulset/nats`
2. Check worker logs: `kubectl -n tarka logs deploy/tarka-worker`
3. Verify alert name is in `ALERTNAME_ALLOWLIST`
4. Check Prometheus/Alertmanager connectivity from pods:
   ```bash
   kubectl -n tarka exec -it deploy/tarka-worker -- \
     curl ${PROMETHEUS_URL}/api/v1/query?query=up
   ```

---

## Security Considerations

### Secrets Management

- **Never commit secrets to Git**: Use `.env.deploy` (gitignored) for configuration
- **Use AWS Secrets Manager**: All sensitive values stored in ASM, not ConfigMaps
- **Rotate credentials regularly**: Update passwords in ASM and restart pods
- **Least privilege IAM**: IAM role has minimal permissions (S3, Secrets Manager, ECR read-only)

### Network Security

- **Internal services**: Prometheus/Alertmanager should be accessible only within cluster
- **HTTPS for public access**: Use ingress controller with TLS for `AUTH_PUBLIC_BASE_URL`
- **Network policies**: Consider implementing Kubernetes network policies to restrict pod communication

### Authentication

- **Enable OIDC in production**: Local auth is for initial setup only
- **Restrict domains**: Use `AUTH_ALLOWED_DOMAINS` to limit who can authenticate
- **Session management**: `AUTH_SESSION_SECRET` should be strong and unique per environment

### GCP Cross-Cloud Auth

- **Workload Identity Federation**: No long-lived GCP service account keys stored in cluster
- **Scoped permissions**: GCP service account has only `aiplatform.user` role
- **Audit logging**: Enable Cloud Audit Logs in GCP to track Vertex AI API usage

---

## Advanced Usage

### Dry Run Mode

Test configuration without deploying:

```bash
# Set skip flags to bypass all deployment steps
export SKIP_BUILD_PUSH=1
export SKIP_BUCKET_CREATE=1
export SKIP_BUCKET_POLICY=1
export SKIP_ASM_SECRET_CREATE=1
export SKIP_IAM_SETUP=1

./deploy.sh  # Will validate config and show what would be deployed
```

### Incremental Updates

Skip steps that don't need to re-run:

```bash
# Already built images and set up AWS resources
export SKIP_BUILD_PUSH=1
export SKIP_BUCKET_CREATE=1
export SKIP_IAM_SETUP=1

./deploy.sh  # Only updates Kubernetes resources
```

### Custom Docker Targets

Build development image:

```bash
export DOCKER_TARGET=dev
./deploy.sh
```

### External PostgreSQL

Disable in-cluster PostgreSQL to use external database:

```bash
export ENABLE_DEV_POSTGRES=0

# Set PostgreSQL connection details in ConfigMap manually
# Update k8s/configmap.yaml with:
# POSTGRES_HOST: "external-postgres.company.com"
# POSTGRES_PORT: "5432"
# POSTGRES_DB: "tarka"
```

### Using Custom KMS Key

Encrypt AWS Secrets Manager secret with customer-managed KMS key:

```bash
export KMS_KEY_ARN="arn:aws:kms:us-east-1:123456789012:key/abc-123"
./deploy.sh
```

### Multiple Environments

Deploy separate environments using different configurations:

```bash
# Production
source .env.deploy.prod
export CLUSTER_NAME=prod-cluster
export S3_BUCKET=prod-tarka-reports
./deploy.sh

# Staging
source .env.deploy.staging
export CLUSTER_NAME=staging-cluster
export S3_BUCKET=staging-tarka-reports
./deploy.sh
```

---

## Migration from Old deploy.sh

If migrating from a previous deployment with hardcoded values:

1. **Extract current configuration**
   ```bash
   kubectl -n tarka get configmap tarka-config -o yaml > old-config.yaml
   ```

2. **Create .env.deploy with current values**
   ```bash
   cp .env.deploy.template .env.deploy
   # Fill in values from old-config.yaml
   ```

3. **Update AWS Secrets Manager**
   - Old secrets had `REPLACE_ME` placeholders
   - New script merges with existing secrets
   - Manually verify secret values in AWS console:
     ```bash
     aws secretsmanager get-secret-value \
       --secret-id tarka \
       --query SecretString \
       --output text | jq .
     ```

4. **Run new deployment**
   ```bash
   source .env.deploy
   ./deploy.sh  # Will update in place
   ```

5. **Verify pods restart**
   ```bash
   kubectl -n tarka get pods -w
   ```

---

## Support

For issues or questions:
- Check [Troubleshooting](#troubleshooting) section
- Review pod logs: `kubectl -n tarka logs <pod-name>`
- File issue at: https://github.com/your-org/tarka/issues

---

## Appendix: Full Example Configuration

```bash
# .env.deploy (production example)

# AWS Infrastructure
CLUSTER_NAME=production-eks
AWS_REGION=us-east-1

# Container Images
IMAGE_NAME=tarka
UI_IMAGE_NAME=tarka-ui
IMAGE_TAG=v1.0.0

# Data Sources
PROMETHEUS_URL=http://vmselect-victoria-metrics.monitoring.svc:8481/select/0/prometheus
ALERTMANAGER_URL=http://vmalertmanager-victoria-metrics.monitoring.svc:9093
LOGS_URL=http://victoria-logs-cluster-vlselect.logs.svc:9471

# Storage
S3_BUCKET=company-tarka-reports-prod
S3_PREFIX=investigations

# Authentication
AUTH_PUBLIC_BASE_URL=https://tarka.company.com
ADMIN_INITIAL_USERNAME=admin
# ADMIN_INITIAL_PASSWORD will be auto-generated

# OIDC (Google Workspace)
OIDC_DISCOVERY_URL=https://accounts.google.com/.well-known/openid-configuration
OIDC_CLIENT_ID=123456-abcdef.apps.googleusercontent.com
OIDC_CLIENT_SECRET=GOCSPX-abc123xyz
AUTH_ALLOWED_DOMAINS=company.com

# GCP Vertex AI
GOOGLE_CLOUD_PROJECT=company-ml-production
GOOGLE_CLOUD_LOCATION=us-central1
GCP_WIF_AUDIENCE=//iam.googleapis.com/projects/987654321/locations/global/workloadIdentityPools/aws-eks-pool/providers/aws-eks-provider

# PostgreSQL (in-cluster)
ENABLE_DEV_POSTGRES=1

# Alert Filtering
TIME_WINDOW=2h
ALERTNAME_ALLOWLIST=CPUThrottlingHigh,KubernetesContainerOomKiller,Http5xxRateHigh

# LangSmith Tracing (optional)
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=tarka-prod
LANGSMITH_TAGS=production,k8s
```
