#!/usr/bin/env bash
set -euo pipefail

# Disable AWS CLI pager to prevent script from hanging on user input
export AWS_PAGER=""

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="${ROOT_DIR}/k8s"

# Debug: Show raw environment variable values before applying defaults
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Debug: Raw environment variables (before defaults)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "LLM_ENABLED=${LLM_ENABLED:-<not set>}"
echo "LLM_PROVIDER=${LLM_PROVIDER:-<not set>}"
echo "LLM_MODEL=${LLM_MODEL:-<not set>}"
echo "LLM_INCLUDE_LOGS=${LLM_INCLUDE_LOGS:-<not set>}"
echo "LLM_REDACT_INFRASTRUCTURE=${LLM_REDACT_INFRASTRUCTURE:-<not set>}"
echo "AWS_EVIDENCE_ENABLED=${AWS_EVIDENCE_ENABLED:-<not set>}"
echo "GITHUB_EVIDENCE_ENABLED=${GITHUB_EVIDENCE_ENABLED:-<not set>}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Required configuration variables (no defaults)
CLUSTER_NAME="${CLUSTER_NAME:-}"
IMAGE_NAME="${IMAGE_NAME:-}"
UI_IMAGE_NAME="${UI_IMAGE_NAME:-}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PROMETHEUS_URL="${PROMETHEUS_URL:-}"
ALERTMANAGER_URL="${ALERTMANAGER_URL:-}"
S3_BUCKET="${S3_BUCKET:-}"
AUTH_PUBLIC_BASE_URL="${AUTH_PUBLIC_BASE_URL:-}"
GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
GCP_WIF_AUDIENCE="${GCP_WIF_AUDIENCE:-}"

# Optional configuration with defaults
AWS_REGION="${AWS_REGION:-us-east-1}"
ROLE_NAME="${ROLE_NAME:-tarka-role}"
S3_PREFIX="${S3_PREFIX:-tarka/reports}"
LOGS_URL="${LOGS_URL:-}"
TIME_WINDOW="${TIME_WINDOW:-1h}"
GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
ADMIN_INITIAL_USERNAME="${ADMIN_INITIAL_USERNAME:-admin}"
AUTH_ALLOWED_DOMAINS="${AUTH_ALLOWED_DOMAINS:-}"

# OIDC Configuration (all optional, but if any is set, all three required)
OIDC_DISCOVERY_URL="${OIDC_DISCOVERY_URL:-}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-}"
OIDC_CLIENT_SECRET="${OIDC_CLIENT_SECRET:-}"

# Auto-generated secrets (will be generated if not provided)
AUTH_SESSION_SECRET="${AUTH_SESSION_SECRET:-}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
ADMIN_INITIAL_PASSWORD="${ADMIN_INITIAL_PASSWORD:-}"
GENERATED_ADMIN_PASSWORD="${GENERATED_ADMIN_PASSWORD:-0}"
# Queue configuration (required; receiver is enqueue-only)
NATS_URL="${NATS_URL:-nats://nats.tarka.svc:4222}"
JETSTREAM_STREAM="${JETSTREAM_STREAM:-TARKA}"
JETSTREAM_SUBJECT="${JETSTREAM_SUBJECT:-tarka.alerts}"
# Example of the allowlist (ALERTNAME_ALLOWLIST) being set to a comma-separated list of allowed alert names:
ALERTNAME_ALLOWLIST="${ALERTNAME_ALLOWLIST:-CPUThrottlingHigh,KubePodCPUThrottling,CPUThrottling,ContainerCpuThrottled,KubernetesContainerOomKiller,Http5xxRateHigh,Http5xxRateWarning,KubernetesPodNotHealthy,KubernetesPodNotHealthyCritical,RowsRejectedOnIngestion,MemoryPressure,podOomKilled,jobOomKilled,podRestarted,KubeJobFailed,dispatcherJobFailed}"

# Optional: dev Postgres (in-cluster) for experimenting with memory/indexing.
ENABLE_DEV_POSTGRES="${ENABLE_DEV_POSTGRES:-1}"

# External Secrets / AWS Secrets Manager (agent secret bundle)
ASM_SECRET_NAME="${ASM_SECRET_NAME:-tarka}"
# If your Secrets Manager secret uses a customer-managed KMS key, set this to enable kms:Decrypt in the IRSA policy
# and to use that key when creating the secret.
KMS_KEY_ARN="${KMS_KEY_ARN:-}"

SKIP_BUILD_PUSH="${SKIP_BUILD_PUSH:-0}"
SKIP_BUCKET_CREATE="${SKIP_BUCKET_CREATE:-0}"
SKIP_BUCKET_POLICY="${SKIP_BUCKET_POLICY:-0}"
SKIP_ASM_SECRET_CREATE="${SKIP_ASM_SECRET_CREATE:-0}"
SKIP_IAM_SETUP="${SKIP_IAM_SETUP:-0}"
SKIP_KUBECONFIG_UPDATE="${SKIP_KUBECONFIG_UPDATE:-0}"
DOCKER_TARGET="${DOCKER_TARGET:-final}"

# LangSmith (optional; env-gated tracing)
LANGSMITH_TRACING="${LANGSMITH_TRACING:-false}"
LANGSMITH_PROJECT="${LANGSMITH_PROJECT:-tarka}"
LANGSMITH_TAGS="${LANGSMITH_TAGS:-}"
LANGSMITH_RUN_NAME_PREFIX="${LANGSMITH_RUN_NAME_PREFIX:-}"
LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-}"

# GCP WIF credentials JSON (optional; can be provided later in AWS Secrets Manager)
GCP_WIF_CRED_JSON="${GCP_WIF_CRED_JSON:-}"


# LLM Configuration (optional; AI-enriched investigations)
LLM_ENABLED="${LLM_ENABLED:-false}"
LLM_PROVIDER="${LLM_PROVIDER:-vertexai}"
LLM_MODEL="${LLM_MODEL:-gemini-2.5-flash}"
LLM_TEMPERATURE="${LLM_TEMPERATURE:-0.2}"
LLM_MAX_OUTPUT_TOKENS="${LLM_MAX_OUTPUT_TOKENS:-4096}"
LLM_MOCK="${LLM_MOCK:-0}"
LLM_INCLUDE_LOGS="${LLM_INCLUDE_LOGS:-false}"
LLM_REDACT_INFRASTRUCTURE="${LLM_REDACT_INFRASTRUCTURE:-true}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

# AWS Evidence Collection (optional; EC2, EBS, ELB, RDS, ECR, networking, CloudTrail)
AWS_EVIDENCE_ENABLED="${AWS_EVIDENCE_ENABLED:-true}"
AWS_CLOUDTRAIL_LOOKBACK_MINUTES="${AWS_CLOUDTRAIL_LOOKBACK_MINUTES:-30}"
AWS_CLOUDTRAIL_MAX_EVENTS="${AWS_CLOUDTRAIL_MAX_EVENTS:-50}"
CHAT_ALLOW_AWS_READ="${CHAT_ALLOW_AWS_READ:-true}"
CHAT_AWS_REGION_ALLOWLIST="${CHAT_AWS_REGION_ALLOWLIST:-us-east-1}"

# GitHub Evidence Collection (optional; commits, workflows, docs)
GITHUB_EVIDENCE_ENABLED="${GITHUB_EVIDENCE_ENABLED:-true}"
CHAT_ALLOW_GITHUB_READ="${CHAT_ALLOW_GITHUB_READ:-true}"
CHAT_GITHUB_REPO_ALLOWLIST="${CHAT_GITHUB_REPO_ALLOWLIST:-}"
GITHUB_DEFAULT_ORG="${GITHUB_DEFAULT_ORG:-}"
GITHUB_APP_ID="${GITHUB_APP_ID:-}"
GITHUB_APP_PRIVATE_KEY="${GITHUB_APP_PRIVATE_KEY:-}"
GITHUB_APP_INSTALLATION_ID="${GITHUB_APP_INSTALLATION_ID:-}"

# K8s Events Tool (enabled by default, uses existing RBAC)
CHAT_ALLOW_K8S_EVENTS="${CHAT_ALLOW_K8S_EVENTS:-true}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1"
    exit 1
  }
}

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}ℹ${NC} $*"
}

log_success() {
    echo -e "${GREEN}✓${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $*"
}

log_error() {
    echo -e "${RED}✗${NC} $*" >&2
}

determine_poetry_extras() {
  # Determine which Poetry extras to install based on LLM_PROVIDER
  if [[ "${LLM_ENABLED}" != "true" ]]; then
    echo ""
    return
  fi

  case "${LLM_PROVIDER}" in
    vertexai|vertex|gcp_vertexai)
      echo "vertex"
      ;;
    anthropic)
      echo "anthropic"
      ;;
    *)
      log_warning "Unknown LLM_PROVIDER: ${LLM_PROVIDER}. Skipping LLM SDK installation."
      echo ""
      ;;
  esac
}

log_section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}▶${NC} $*"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

validate_all_config() {
    local has_errors=0
    local error_messages=()

    # Group 1: AWS Infrastructure
    if [[ -z "${CLUSTER_NAME:-}" ]]; then
        error_messages+=("  ${RED}✗${NC} AWS: CLUSTER_NAME is required (your EKS cluster name)")
        has_errors=1
    fi

    # Group 2: Container Images
    if [[ -z "${IMAGE_NAME:-}" ]] && [[ -z "${IMAGE:-}" ]]; then
        error_messages+=("  ${RED}✗${NC} Images: IMAGE_NAME is required (e.g., 'tarka') or set IMAGE directly")
        has_errors=1
    fi
    if [[ -z "${UI_IMAGE_NAME:-}" ]] && [[ -z "${UI_IMAGE:-}" ]]; then
        error_messages+=("  ${RED}✗${NC} Images: UI_IMAGE_NAME is required (e.g., 'tarka-ui') or set UI_IMAGE directly")
        has_errors=1
    fi

    # Group 3: Data Sources
    if [[ -z "${PROMETHEUS_URL:-}" ]]; then
        error_messages+=("  ${RED}✗${NC} Data Sources: PROMETHEUS_URL is required (e.g., 'http://prometheus.monitoring.svc:9090')")
        has_errors=1
    fi
    if [[ -z "${ALERTMANAGER_URL:-}" ]]; then
        error_messages+=("  ${RED}✗${NC} Data Sources: ALERTMANAGER_URL is required (e.g., 'http://alertmanager.monitoring.svc:9093')")
        has_errors=1
    fi

    # Group 4: Storage
    if [[ -z "${S3_BUCKET:-}" ]]; then
        error_messages+=("  ${RED}✗${NC} Storage: S3_BUCKET is required")
        has_errors=1
    fi

    # Group 5: Authentication
    if [[ -z "${AUTH_PUBLIC_BASE_URL:-}" ]]; then
        error_messages+=("  ${RED}✗${NC} Auth: AUTH_PUBLIC_BASE_URL is required (e.g., 'https://tarka.company.com')")
        has_errors=1
    fi

    # Group 6: OIDC (all three required if any is set)
    local oidc_count=0
    [[ -n "${OIDC_DISCOVERY_URL:-}" ]] && ((oidc_count++))
    [[ -n "${OIDC_CLIENT_ID:-}" ]] && ((oidc_count++))
    [[ -n "${OIDC_CLIENT_SECRET:-}" ]] && ((oidc_count++))

    if [[ ${oidc_count} -gt 0 ]] && [[ ${oidc_count} -lt 3 ]]; then
        error_messages+=("  ${RED}✗${NC} OIDC: If any OIDC variable is set, all three are required:")
        [[ -z "${OIDC_DISCOVERY_URL:-}" ]] && error_messages+=("       - OIDC_DISCOVERY_URL")
        [[ -z "${OIDC_CLIENT_ID:-}" ]] && error_messages+=("       - OIDC_CLIENT_ID")
        [[ -z "${OIDC_CLIENT_SECRET:-}" ]] && error_messages+=("       - OIDC_CLIENT_SECRET")
        has_errors=1
    fi

    # Group 7: GCP Vertex AI
    if [[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]] || [[ "${GOOGLE_CLOUD_PROJECT}" == "blah" ]]; then
        error_messages+=("  ${RED}✗${NC} GCP: GOOGLE_CLOUD_PROJECT is required (your GCP project ID)")
        has_errors=1
    fi
    if [[ -z "${GCP_WIF_AUDIENCE:-}" ]] || [[ "${GCP_WIF_AUDIENCE}" == "blah" ]]; then
        error_messages+=("  ${RED}✗${NC} GCP: GCP_WIF_AUDIENCE must be a real value, not placeholder")
        error_messages+=("       Format: //iam.googleapis.com/projects/{PROJECT_NUMBER}/locations/global/workloadIdentityPools/{POOL}/providers/{PROVIDER}")
        has_errors=1
    elif [[ ! "${GCP_WIF_AUDIENCE}" =~ ^//iam\.googleapis\.com/projects/ ]]; then
        error_messages+=("  ${RED}✗${NC} GCP: GCP_WIF_AUDIENCE has invalid format")
        error_messages+=("       Expected: //iam.googleapis.com/projects/...")
        error_messages+=("       Got: ${GCP_WIF_AUDIENCE}")
        has_errors=1
    fi

    # Print all errors if any found
    if [[ ${has_errors} -eq 1 ]]; then
        echo ""
        log_error "Configuration validation failed:"
        echo ""
        for msg in "${error_messages[@]}"; do
            echo -e "${msg}"
        done
        echo ""
        log_info "See DEPLOYMENT.md for configuration instructions"
        echo ""
        exit 1
    fi
}

load_existing_secrets() {
    # Load existing secrets from AWS Secrets Manager to avoid regenerating them
    if aws secretsmanager describe-secret --secret-id "${ASM_SECRET_NAME}" --region "${AWS_REGION}" --no-cli-pager >/dev/null 2>&1; then
        log_info "Loading existing secrets from AWS Secrets Manager..."

        _existing_secret="$(aws secretsmanager get-secret-value \
            --secret-id "${ASM_SECRET_NAME}" \
            --region "${AWS_REGION}" \
            --no-cli-pager \
            --query 'SecretString' \
            --output text 2>/dev/null || echo '{}')"

        # Extract existing values using python (more reliable than jq)
        if [[ -n "${_existing_secret}" && "${_existing_secret}" != "{}" ]]; then
            AUTH_SESSION_SECRET="${AUTH_SESSION_SECRET:-$(echo "${_existing_secret}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('AUTH_SESSION_SECRET', ''))")}"
            POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(echo "${_existing_secret}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('POSTGRES_PASSWORD', ''))")}"
            ADMIN_INITIAL_PASSWORD="${ADMIN_INITIAL_PASSWORD:-$(echo "${_existing_secret}" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('ADMIN_INITIAL_PASSWORD', ''))")}"

            if [[ -n "${AUTH_SESSION_SECRET}" ]]; then
                log_info "Using existing AUTH_SESSION_SECRET from AWS Secrets Manager"
            fi
            if [[ -n "${POSTGRES_PASSWORD}" ]]; then
                log_info "Using existing POSTGRES_PASSWORD from AWS Secrets Manager"
            fi
            if [[ -n "${ADMIN_INITIAL_PASSWORD}" ]]; then
                log_info "Using existing ADMIN_INITIAL_PASSWORD from AWS Secrets Manager"
            fi
        fi
    fi
}

generate_secrets() {
    log_info "Generating secure secrets (only for missing values)..."

    # AUTH_SESSION_SECRET: 64-char hex (256 bits)
    if [[ -z "${AUTH_SESSION_SECRET:-}" ]]; then
        AUTH_SESSION_SECRET="$(openssl rand -hex 32)"
        log_success "Generated AUTH_SESSION_SECRET (64 chars)"
    fi

    # POSTGRES_PASSWORD: 32-char alphanumeric
    if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
        POSTGRES_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
        log_success "Generated POSTGRES_PASSWORD (32 chars)"
    fi

    # ADMIN_INITIAL_PASSWORD: 16-char if not provided
    if [[ -z "${ADMIN_INITIAL_PASSWORD:-}" ]]; then
        ADMIN_INITIAL_PASSWORD="$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)"
        GENERATED_ADMIN_PASSWORD="1"  # Flag for summary display
        log_success "Generated ADMIN_INITIAL_PASSWORD (16 chars)"
    fi
}

show_deployment_summary() {
    log_section "Deployment Complete"

    echo "Stack deployed:"
    echo "  ✓ Namespace: tarka"
    echo "  ✓ NATS JetStream: statefulset/nats"
    echo "  ✓ Webhook: deployment/tarka-webhook"
    echo "  ✓ Workers: deployment/tarka-worker"
    echo "  ✓ Console UI: deployment/tarka-console-ui"
    [[ "${ENABLE_DEV_POSTGRES}" == "1" ]] && echo "  ✓ PostgreSQL: deployment/tarka-postgres"
    echo ""

    echo "Configuration:"
    echo "  • Cluster: ${CLUSTER_NAME} (${AWS_REGION})"
    echo "  • Images: ${IMAGE_NAME:-${IMAGE}}:${IMAGE_TAG}"
    echo "  • Storage: s3://${S3_BUCKET}/${S3_PREFIX}"
    echo "  • IAM Role: ${ROLE_ARN}"
    echo ""

    echo "Authentication:"
    echo "  • Public URL: ${AUTH_PUBLIC_BASE_URL}"
    echo "  • Admin user: ${ADMIN_INITIAL_USERNAME}"
    [[ -n "${OIDC_DISCOVERY_URL:-}" ]] && echo "  • OIDC: Enabled (${OIDC_DISCOVERY_URL})"
    [[ -z "${OIDC_DISCOVERY_URL:-}" ]] && echo "  • OIDC: Not configured (local auth only)"
    echo ""

    # Show generated admin password if applicable
    if [[ "${GENERATED_ADMIN_PASSWORD:-0}" == "1" ]]; then
        log_warning "═══════════════════════════════════════════"
        log_warning "  SAVE YOUR ADMIN PASSWORD"
        log_warning "═══════════════════════════════════════════"
        log_warning "Username: ${ADMIN_INITIAL_USERNAME}"
        log_warning "Password: ${ADMIN_INITIAL_PASSWORD}"
        log_warning ""
        log_warning "This password will not be shown again!"
        log_warning "═══════════════════════════════════════════"
        echo ""
    fi

    echo "Next steps:"
    echo ""
    echo "  1. Check deployment status:"
    echo "     kubectl -n tarka get deploy,sts,svc,pod"
    echo ""
    echo "  2. View logs:"
    echo "     kubectl -n tarka logs -f deployment/tarka-webhook"
    echo ""
    echo "  3. Configure Alertmanager webhook:"
    echo "     Add to alertmanager.yml receivers:"
    echo "       webhook_configs:"
    echo "         - url: 'http://tarka-webhook.tarka.svc:8080/alerts'"
    echo ""
    echo "  4. Access Console UI (port-forward):"
    echo "     kubectl port-forward -n tarka svc/tarka-console-ui 8080:80"
    echo "     Open: http://localhost:8080"
    [[ "${GENERATED_ADMIN_PASSWORD:-0}" == "1" ]] && echo "     Login: ${ADMIN_INITIAL_USERNAME} / <password-shown-above>"
    echo ""

    log_success "Deployment complete! 🎉"
}

require_cmd kubectl
require_cmd sed
require_cmd aws
require_cmd docker
require_cmd grep
require_cmd openssl

log_section "Validating Configuration"
validate_all_config

log_section "Preparing Deployment"

# Auto-detect AWS context
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --no-cli-pager)"
log_info "Detected AWS Account: ${AWS_ACCOUNT_ID}"
log_info "Region: ${AWS_REGION}"

# Construct image URLs if not explicitly provided
if [[ -z "${IMAGE:-}" ]]; then
    IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${IMAGE_NAME}:${IMAGE_TAG}"
fi

if [[ -z "${UI_IMAGE:-}" ]]; then
    UI_IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${UI_IMAGE_NAME}:${IMAGE_TAG}"
fi

# Construct ROLE_ARN from components
ROLE_ARN="${ROLE_ARN:-arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}}"

log_info "Agent Image: ${IMAGE}"
log_info "UI Image: ${UI_IMAGE}"
log_info "IAM Role: ${ROLE_ARN}"

# Load existing secrets from AWS Secrets Manager (to avoid regenerating)
load_existing_secrets

# Generate secrets if not provided
generate_secrets

if [[ "${SKIP_KUBECONFIG_UPDATE}" != "1" ]]; then
  log_info "Configuring kubectl for cluster ${CLUSTER_NAME}..."
  aws eks update-kubeconfig --name "${CLUSTER_NAME}" --region "${AWS_REGION}" --no-cli-pager >/dev/null
  log_success "kubectl configured"
fi

REGISTRY="${IMAGE%%/*}"

log_section "Configuration Summary"
echo "  Cluster: ${CLUSTER_NAME} (${AWS_REGION})"
echo "  Storage: s3://${S3_BUCKET}/${S3_PREFIX}"
echo "  Data Sources:"
echo "    - Prometheus: ${PROMETHEUS_URL}"
echo "    - Alertmanager: ${ALERTMANAGER_URL}"
[[ -n "${LOGS_URL}" ]] && echo "    - Logs: ${LOGS_URL}"
echo "  Queue: ${NATS_URL}"
echo "  PostgreSQL: $([ "${ENABLE_DEV_POSTGRES}" = "1" ] && echo "Enabled (in-cluster)" || echo "Disabled")"
echo "  Auth: ${AUTH_PUBLIC_BASE_URL}"
[[ -n "${OIDC_DISCOVERY_URL}" ]] && echo "  OIDC: Enabled"
echo ""
echo "  LLM Configuration:"
echo "    - LLM_ENABLED: ${LLM_ENABLED}"
echo "    - LLM_PROVIDER: ${LLM_PROVIDER}"
echo "    - LLM_MODEL: ${LLM_MODEL}"
echo "    - LLM_TEMPERATURE: ${LLM_TEMPERATURE}"
echo "    - LLM_MAX_OUTPUT_TOKENS: ${LLM_MAX_OUTPUT_TOKENS}"
echo "    - LLM_MOCK: ${LLM_MOCK}"
echo "    - LLM_INCLUDE_LOGS: ${LLM_INCLUDE_LOGS}"
echo "    - LLM_REDACT_INFRASTRUCTURE: ${LLM_REDACT_INFRASTRUCTURE}"
echo ""
echo "  Evidence Collection:"
echo "    - AWS_EVIDENCE_ENABLED: ${AWS_EVIDENCE_ENABLED}"
echo "    - GITHUB_EVIDENCE_ENABLED: ${GITHUB_EVIDENCE_ENABLED}"
echo ""
echo "  Chat Tools:"
echo "    - CHAT_ALLOW_AWS_READ: ${CHAT_ALLOW_AWS_READ}"
echo "    - CHAT_ALLOW_GITHUB_READ: ${CHAT_ALLOW_GITHUB_READ}"
echo "    - CHAT_ALLOW_K8S_EVENTS: ${CHAT_ALLOW_K8S_EVENTS}"
echo ""

if [[ "${SKIP_BUILD_PUSH}" != "1" ]]; then
  log_section "Building and Pushing Images"
  log_info "Building agent image..."

  # Determine Poetry extras based on LLM provider
  POETRY_EXTRAS=$(determine_poetry_extras)
  if [[ -n "${POETRY_EXTRAS}" ]]; then
    log_info "Building with Poetry extras: ${POETRY_EXTRAS} (for ${LLM_PROVIDER})"
    docker build \
      --platform=linux/amd64 \
      --target "${DOCKER_TARGET}" \
      --build-arg POETRY_EXTRAS="${POETRY_EXTRAS}" \
      -t "${IMAGE}" \
      .
  else
    if [[ "${LLM_ENABLED}" == "true" ]]; then
      log_warning "LLM enabled but no valid provider configured - building without LLM SDKs"
    else
      log_info "LLM disabled - building without LLM SDKs (deterministic mode only)"
    fi
    docker build \
      --platform=linux/amd64 \
      --target "${DOCKER_TARGET}" \
      -t "${IMAGE}" \
      .
  fi
  log_success "Agent image built"

  log_info "Building UI image..."
  docker build --platform=linux/amd64 -t "${UI_IMAGE}" -f ui/Dockerfile ui/
  log_success "UI image built"

  # If this looks like ECR, login automatically.
  if [[ "${REGISTRY}" == *.amazonaws.com ]]; then
    log_info "Logging in to ECR registry..."
    aws ecr get-login-password --region "${AWS_REGION}" --no-cli-pager | docker login --username AWS --password-stdin "${REGISTRY}" >/dev/null 2>&1
    log_success "ECR login successful"
  fi

  # Ensure ECR repositories exist (idempotent)
  if [[ "${REGISTRY}" == *.amazonaws.com ]]; then
    log_info "Ensuring ECR repositories exist..."

    # Extract repository names from image URLs
    # Format: account.dkr.ecr.region.amazonaws.com/repo-name:tag
    AGENT_REPO_NAME=$(echo "${IMAGE}" | sed -E 's|.*amazonaws.com/([^:]+):.*|\1|')
    UI_REPO_NAME=$(echo "${UI_IMAGE}" | sed -E 's|.*amazonaws.com/([^:]+):.*|\1|')

    # Create agent repository if it doesn't exist
    if aws ecr describe-repositories --repository-names "${AGENT_REPO_NAME}" --region "${AWS_REGION}" --no-cli-pager >/dev/null 2>&1; then
      log_info "ECR repository ${AGENT_REPO_NAME} already exists"
    else
      log_info "Creating ECR repository: ${AGENT_REPO_NAME}"
      aws ecr create-repository \
        --repository-name "${AGENT_REPO_NAME}" \
        --region "${AWS_REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --no-cli-pager >/dev/null
      log_success "ECR repository ${AGENT_REPO_NAME} created"
    fi

    # Create UI repository if it doesn't exist
    if aws ecr describe-repositories --repository-names "${UI_REPO_NAME}" --region "${AWS_REGION}" --no-cli-pager >/dev/null 2>&1; then
      log_info "ECR repository ${UI_REPO_NAME} already exists"
    else
      log_info "Creating ECR repository: ${UI_REPO_NAME}"
      aws ecr create-repository \
        --repository-name "${UI_REPO_NAME}" \
        --region "${AWS_REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --no-cli-pager >/dev/null
      log_success "ECR repository ${UI_REPO_NAME} created"
    fi
  fi

  log_info "Pushing images to registry..."
  docker push "${IMAGE}"
  docker push "${UI_IMAGE}"
  log_success "Images pushed successfully"
fi


log_section "Setting Up AWS Resources"

# Check if S3 bucket exists, create if not
if [[ "${SKIP_BUCKET_CREATE}" != "1" ]]; then
  if aws s3api head-bucket --bucket "${S3_BUCKET}" --no-cli-pager 2>/dev/null; then
    log_info "S3 bucket ${S3_BUCKET} already exists"
  else
    log_info "Creating S3 bucket: ${S3_BUCKET}"
    if [[ "${AWS_REGION}" == "us-east-1" ]]; then
      aws s3api create-bucket --bucket "${S3_BUCKET}" --no-cli-pager
    else
      aws s3api create-bucket --bucket "${S3_BUCKET}" --create-bucket-configuration LocationConstraint="${AWS_REGION}" --no-cli-pager
    fi
    log_success "S3 bucket created"
  fi
fi

if [[ "${SKIP_ASM_SECRET_CREATE}" != "1" ]]; then
  log_info "Creating or updating AWS Secrets Manager secret: ${ASM_SECRET_NAME}"

  # Escape special characters in secret values
  _escape_json() {
    local val="$1"
    val="${val//\\/\\\\}"
    val="${val//\"/\\\"}"
    val="${val//$'\n'/\\n}"
    echo "${val}"
  }

  # Build new secret JSON
  _auth_session="$(_escape_json "${AUTH_SESSION_SECRET}")"
  _pg_pass="$(_escape_json "${POSTGRES_PASSWORD}")"
  _admin_user="$(_escape_json "${ADMIN_INITIAL_USERNAME}")"
  _admin_pass="$(_escape_json "${ADMIN_INITIAL_PASSWORD}")"
  _gcp_wif="$(_escape_json "${GCP_WIF_CRED_JSON}")"
  _oidc_discovery="$(_escape_json "${OIDC_DISCOVERY_URL}")"
  _oidc_id="$(_escape_json "${OIDC_CLIENT_ID}")"
  _oidc_secret="$(_escape_json "${OIDC_CLIENT_SECRET}")"
  _langsmith_key="$(_escape_json "${LANGSMITH_API_KEY}")"
  _anthropic_key="$(_escape_json "${ANTHROPIC_API_KEY}")"
  _llm_enabled="$(_escape_json "${LLM_ENABLED}")"
  _llm_provider="$(_escape_json "${LLM_PROVIDER}")"
  _llm_model="$(_escape_json "${LLM_MODEL}")"
  _llm_temp="$(_escape_json "${LLM_TEMPERATURE}")"
  _llm_tokens="$(_escape_json "${LLM_MAX_OUTPUT_TOKENS}")"
  _github_app_id="$(_escape_json "${GITHUB_APP_ID}")"
  _github_app_key="$(_escape_json "${GITHUB_APP_PRIVATE_KEY}")"
  _github_app_install="$(_escape_json "${GITHUB_APP_INSTALLATION_ID}")"

  _new_secret_json="$(cat <<EOF
{
  "AUTH_SESSION_SECRET": "${_auth_session}",
  "POSTGRES_PASSWORD": "${_pg_pass}",
  "ADMIN_INITIAL_USERNAME": "${_admin_user}",
  "ADMIN_INITIAL_PASSWORD": "${_admin_pass}",
  "GCP_WIF_CRED_JSON": "${_gcp_wif}",
  "OIDC_DISCOVERY_URL": "${_oidc_discovery}",
  "OIDC_CLIENT_ID": "${_oidc_id}",
  "OIDC_CLIENT_SECRET": "${_oidc_secret}",
  "LANGSMITH_API_KEY": "${_langsmith_key}",
  "ANTHROPIC_API_KEY": "${_anthropic_key}",
  "LLM_ENABLED": "${_llm_enabled}",
  "LLM_PROVIDER": "${_llm_provider}",
  "LLM_MODEL": "${_llm_model}",
  "LLM_TEMPERATURE": "${_llm_temp}",
  "LLM_MAX_OUTPUT_TOKENS": "${_llm_tokens}",
  "GITHUB_APP_ID": "${_github_app_id}",
  "GITHUB_APP_PRIVATE_KEY": "${_github_app_key}",
  "GITHUB_APP_INSTALLATION_ID": "${_github_app_install}"
}
EOF
)"

  if aws secretsmanager describe-secret --secret-id "${ASM_SECRET_NAME}" --region "${AWS_REGION}" --no-cli-pager >/dev/null 2>&1; then
    # Secret exists - merge with existing values
    log_info "Secret exists - merging with existing values..."

    # Fetch current secret
    _current_secret="$(aws secretsmanager get-secret-value \
      --secret-id "${ASM_SECRET_NAME}" \
      --region "${AWS_REGION}" \
      --no-cli-pager \
      --query 'SecretString' \
      --output text 2>/dev/null || echo '{}')"

    # Merge: current values take precedence for keys not in new secret
    # New values override current values
    _current_file="$(mktemp)"
    _new_file="$(mktemp)"
    echo "${_current_secret}" > "${_current_file}"
    echo "${_new_secret_json}" > "${_new_file}"

    _merged_secret="$(python3 -c "
import sys, json
with open('${_current_file}', 'r') as f:
    current = json.load(f)
with open('${_new_file}', 'r') as f:
    new = json.load(f)
merged = {**current, **new}
print(json.dumps(merged))
")"

    rm -f "${_current_file}" "${_new_file}"

    aws secretsmanager update-secret \
      --secret-id "${ASM_SECRET_NAME}" \
      --secret-string "${_merged_secret}" \
      --region "${AWS_REGION}" \
      --no-cli-pager >/dev/null
    log_success "Secret updated successfully"
  else
    # Secret doesn't exist - create it
    log_info "Creating new secret..."
    if [[ -n "${KMS_KEY_ARN}" ]]; then
      aws secretsmanager create-secret \
        --name "${ASM_SECRET_NAME}" \
        --secret-string "${_new_secret_json}" \
        --tags "Key=Name,Value=${ASM_SECRET_NAME}" \
        --kms-key-id "${KMS_KEY_ARN}" \
        --region "${AWS_REGION}" \
        --no-cli-pager >/dev/null
    else
      aws secretsmanager create-secret \
        --name "${ASM_SECRET_NAME}" \
        --secret-string "${_new_secret_json}" \
        --tags "Key=Name,Value=${ASM_SECRET_NAME}" \
        --region "${AWS_REGION}" \
        --no-cli-pager >/dev/null
    fi
    log_success "Secret created successfully"
  fi

  [[ -z "${GCP_WIF_CRED_JSON}" ]] && log_warning "GCP_WIF_CRED_JSON not set - update secret manually if needed"
  [[ -z "${OIDC_CLIENT_ID}" ]] && log_info "OIDC not configured - local auth only"
  [[ -z "${GITHUB_APP_ID}" ]] && log_info "GitHub App not configured - GitHub evidence and chat tools disabled"
fi

if [[ "${SKIP_IAM_SETUP}" != "1" ]]; then
  # Check if IAM Role exists, create if not
  if aws iam get-role --role-name "${ROLE_NAME}" --no-cli-pager >/dev/null 2>&1; then
    log_info "IAM role ${ROLE_NAME} already exists"
  else
    log_info "Creating IAM role: ${ROLE_NAME}"

    OIDC_ISSUER_URL="$(aws eks describe-cluster --name "${CLUSTER_NAME}" --region "${AWS_REGION}" --query "cluster.identity.oidc.issuer" --output text --no-cli-pager)"
    if [[ -z "${OIDC_ISSUER_URL}" || "${OIDC_ISSUER_URL}" == "None" ]]; then
      log_error "Could not determine EKS OIDC issuer URL for cluster ${CLUSTER_NAME}"
      exit 1
    fi

    OIDC_PROVIDER_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_ISSUER_URL#https://}"

    # Check if OIDC provider exists, create if not (required for IRSA)
    if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${OIDC_PROVIDER_ARN}" --no-cli-pager >/dev/null 2>&1; then
      log_info "IAM OIDC provider not found, creating it..."

      # Extract hostname from OIDC issuer URL
      OIDC_PROVIDER_HOST="${OIDC_ISSUER_URL#https://}"

      # Get certificate thumbprint
      log_info "Fetching OIDC provider certificate thumbprint..."
      THUMBPRINT=$(echo | openssl s_client -servername "${OIDC_PROVIDER_HOST}" \
        -showcerts -connect "${OIDC_PROVIDER_HOST}:443" 2>/dev/null \
        | openssl x509 -fingerprint -sha1 -noout 2>/dev/null \
        | cut -d= -f2 | tr -d :)

      if [[ -z "${THUMBPRINT}" ]]; then
        log_error "Failed to get certificate thumbprint for ${OIDC_PROVIDER_HOST}"
        log_info "You may need to create the OIDC provider manually:"
        log_info "  aws iam create-open-id-connect-provider \\"
        log_info "    --url ${OIDC_ISSUER_URL} \\"
        log_info "    --client-id-list sts.amazonaws.com"
        exit 1
      fi

      # Create OIDC provider
      aws iam create-open-id-connect-provider \
        --url "${OIDC_ISSUER_URL}" \
        --client-id-list sts.amazonaws.com \
        --thumbprint-list "${THUMBPRINT}" \
        --no-cli-pager >/dev/null

      log_success "IAM OIDC provider created: ${OIDC_PROVIDER_ARN}"
    else
      log_info "IAM OIDC provider already exists"
    fi

    TRUST_POLICY="$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "${OIDC_PROVIDER_ARN}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_ISSUER_URL#https://}:sub": "system:serviceaccount:tarka:tarka",
          "${OIDC_ISSUER_URL#https://}:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
EOF
)"

    TRUST_POLICY_FILE="$(mktemp)"
    echo "${TRUST_POLICY}" > "${TRUST_POLICY_FILE}"

    aws iam create-role \
      --role-name "${ROLE_NAME}" \
      --assume-role-policy-document "file://${TRUST_POLICY_FILE}" \
      --no-cli-pager >/dev/null

    rm -f "${TRUST_POLICY_FILE}"
    log_success "IAM role created"
  fi

  # Ensure the role can write/read the report objects under the prefix (least privilege).
  log_info "Updating IAM role policy..."
  POLICY_FILE="$(mktemp)"
  REPORT_PREFIX="${S3_PREFIX%/}"
  if [[ -z "${REPORT_PREFIX}" ]]; then
    OBJECT_ARN="arn:aws:s3:::${S3_BUCKET}/*"
    PREFIX_FOR_LIST=""
  else
    OBJECT_ARN="arn:aws:s3:::${S3_BUCKET}/${REPORT_PREFIX}/*"
    PREFIX_FOR_LIST="${REPORT_PREFIX}/*"
  fi

  ASM_SECRET_ARN="arn:aws:secretsmanager:${AWS_REGION}:${AWS_ACCOUNT_ID}:secret:${ASM_SECRET_NAME}*"
  KMS_STATEMENT=""
  if [[ -n "${KMS_KEY_ARN}" ]]; then
    KMS_STATEMENT="$(cat <<EOF
    ,
    {
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": ["${KMS_KEY_ARN}"]
    }
EOF
)"
  fi

  # Conditionally add AWS evidence collection permissions (CloudTrail, EC2, ELB, RDS, ECR, networking, S3)
  AWS_EVIDENCE_STATEMENT=""
  if [[ "${AWS_EVIDENCE_ENABLED}" == "true" ]]; then
    AWS_EVIDENCE_STATEMENT="$(cat <<EOF
    ,
    {
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*",
        "elasticloadbalancing:Describe*",
        "rds:Describe*",
        "ecr:DescribeRepositories",
        "ecr:ListImages",
        "ecr:BatchGetImage",
        "cloudtrail:LookupEvents",
        "s3:GetBucketLocation",
        "iam:GetRole",
        "iam:ListAttachedRolePolicies",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "iam:SimulatePrincipalPolicy"
      ],
      "Resource": ["*"]
    }
EOF
)"
  fi

  if [[ -n "${REPORT_PREFIX}" ]]; then
    cat > "${POLICY_FILE}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject","s3:PutObject"],
      "Resource": ["${OBJECT_ARN}"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}"],
      "Condition": {
        "StringLike": {
          "s3:prefix": ["${REPORT_PREFIX}/*"]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],
      "Resource": ["${ASM_SECRET_ARN}"]
    },
    {
      "Effect": "Allow",
      "Action": ["ecr:DescribeImages"],
      "Resource": ["arn:aws:ecr:*:${AWS_ACCOUNT_ID}:repository/*"]
    }
${KMS_STATEMENT}${AWS_EVIDENCE_STATEMENT}
  ]
}
EOF
  else
    cat > "${POLICY_FILE}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject","s3:PutObject"],
      "Resource": ["${OBJECT_ARN}"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}"]
    },
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],
      "Resource": ["${ASM_SECRET_ARN}"]
    },
    {
      "Effect": "Allow",
      "Action": ["ecr:DescribeImages"],
      "Resource": ["arn:aws:ecr:*:${AWS_ACCOUNT_ID}:repository/*"]
    }
${KMS_STATEMENT}${AWS_EVIDENCE_STATEMENT}
  ]
}
EOF
  fi

  aws iam put-role-policy \
    --role-name "${ROLE_NAME}" \
    --policy-name "tarka-s3-report-writer" \
    --policy-document "file://${POLICY_FILE}" \
    --no-cli-pager

  rm -f "${POLICY_FILE}"
  log_success "IAM role policy updated"
fi

# Optional: attach a bucket policy that denies non-agent principals from accessing the report prefix.
# This is intentionally conservative: we only gate the actions the agent needs (GetObject/PutObject/ListBucket).
# We also avoid overwriting an existing bucket policy unless FORCE_BUCKET_POLICY=1.
if [[ "${SKIP_BUCKET_POLICY}" != "1" ]]; then
  log_info "Setting S3 bucket policy (restricted to agent role)..."

  # Wait for IAM role to be fully propagated (eventual consistency)
  log_info "Waiting for IAM role to be available across AWS services..."
  _role_ready=0
  for _i in {1..30}; do
    if aws iam get-role --role-name "${ROLE_NAME}" --no-cli-pager >/dev/null 2>&1; then
      _role_ready=1
      break
    fi
    sleep 2
  done

  if [[ "${_role_ready}" != "1" ]]; then
    log_error "IAM role ${ROLE_NAME} not available after waiting"
    exit 1
  fi

  log_success "IAM role confirmed available"

  REPORT_PREFIX="${S3_PREFIX%/}"
  if [[ -z "${REPORT_PREFIX}" ]]; then
    _obj_arn="arn:aws:s3:::${S3_BUCKET}/*"
    _prefix_for_list="*"
  else
    _obj_arn="arn:aws:s3:::${S3_BUCKET}/${REPORT_PREFIX}/*"
    _prefix_for_list="${REPORT_PREFIX}/*"
  fi

  # Allow both the IAM role ARN and the assumed-role session ARN form.
  _role_name="${ROLE_ARN##*/}"
  _assumed_arn="arn:aws:sts::${AWS_ACCOUNT_ID}:assumed-role/${_role_name}/*"

  _policy_file="$(mktemp)"
  cat > "${_policy_file}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowAgentRoleReadWriteReports",
      "Effect": "Allow",
      "Principal": { "AWS": ["${ROLE_ARN}"] },
      "Action": ["s3:GetObject","s3:PutObject"],
      "Resource": ["${_obj_arn}"]
    },
    {
      "Sid": "AllowAgentRoleListReportsPrefix",
      "Effect": "Allow",
      "Principal": { "AWS": ["${ROLE_ARN}"] },
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}"],
      "Condition": { "StringLike": { "s3:prefix": ["${_prefix_for_list}"] } }
    }
  ]
}
EOF

  # Retry bucket policy application (S3 may take time to recognize the new IAM role)
  _policy_applied=0
  for _retry in {1..5}; do
    if aws s3api put-bucket-policy \
      --bucket "${S3_BUCKET}" \
      --policy "file://${_policy_file}" \
      --region "${AWS_REGION}" \
      --no-cli-pager 2>&1; then
      _policy_applied=1
      break
    else
      if [[ ${_retry} -lt 5 ]]; then
        log_warning "Bucket policy failed (attempt ${_retry}/5), retrying in 5 seconds..."
        sleep 5
      fi
    fi
  done

  rm -f "${_policy_file}"

  if [[ "${_policy_applied}" != "1" ]]; then
    log_error "Failed to apply S3 bucket policy after 5 attempts"
    log_warning "You may need to apply the bucket policy manually later"
    log_warning "This is usually due to IAM propagation delays"
  else
    log_success "S3 bucket policy applied"
  fi
fi

log_section "Deploying Kubernetes Resources"

# Apply k8s manifests
log_info "Applying namespace..."
kubectl apply -f "${K8S_DIR}/namespace.yaml" >/dev/null
log_success "Namespace created"

log_info "Applying RBAC..."
kubectl apply -f "${K8S_DIR}/rbac.yaml" >/dev/null
log_success "RBAC configured"

log_info "Applying ServiceAccount (IRSA)..."
sed "s|arn:aws:iam::123456789012:role/tarka-irsa-role|${ROLE_ARN}|g" \
  "${K8S_DIR}/serviceaccount.yaml" | kubectl apply -f - >/dev/null
log_success "ServiceAccount configured"

log_info "Rendering and applying ConfigMap..."
POSTGRES_HOST_VAL=""
DB_AUTO_MIGRATE_VAL="0"
if [[ "${ENABLE_DEV_POSTGRES}" == "1" ]]; then
  POSTGRES_HOST_VAL="tarka-postgres"
  DB_AUTO_MIGRATE_VAL="1"
fi
_rendered_configmap="$(mktemp)"
sed \
  -e "s|REPLACE_ME_BUCKET|${S3_BUCKET}|g" \
  -e "s|tarka/reports|${S3_PREFIX}|g" \
  -e "s|REPLACE_ME_ALERTMANAGER_URL|${ALERTMANAGER_URL}|g" \
  -e "s|REPLACE_ME_PROMETHEUS_URL|${PROMETHEUS_URL}|g" \
  -e "s|REPLACE_ME_LOGS_URL|${LOGS_URL:-}|g" \
  -e "s|us-east-1|${AWS_REGION}|g" \
  -e "s|REPLACE_ME_CLUSTER_NAME|${CLUSTER_NAME}|g" \
  -e "s|TIME_WINDOW: \"1h\"|TIME_WINDOW: \"${TIME_WINDOW}\"|g" \
  -e "s|ALERTNAME_ALLOWLIST: \"\"|ALERTNAME_ALLOWLIST: \"${ALERTNAME_ALLOWLIST}\"|g" \
  -e "s|POSTGRES_HOST: \"\"|POSTGRES_HOST: \"${POSTGRES_HOST_VAL}\"|g" \
  -e "s|DB_AUTO_MIGRATE: \"0\"|DB_AUTO_MIGRATE: \"${DB_AUTO_MIGRATE_VAL}\"|g" \
  -e "s|AUTH_PUBLIC_BASE_URL: \"REPLACE_ME_AUTH_PUBLIC_BASE_URL\"|AUTH_PUBLIC_BASE_URL: \"${AUTH_PUBLIC_BASE_URL}\"|g" \
  -e "s|AUTH_ALLOWED_DOMAINS: \"REPLACE_ME_AUTH_ALLOWED_DOMAINS\"|AUTH_ALLOWED_DOMAINS: \"${AUTH_ALLOWED_DOMAINS:-}\"|g" \
  -e "s|OIDC_DISCOVERY_URL: \"\"|OIDC_DISCOVERY_URL: \"${OIDC_DISCOVERY_URL:-}\"|g" \
  -e "s|NATS_URL: \"nats://nats.tarka.svc:4222\"|NATS_URL: \"${NATS_URL}\"|g" \
  -e "s|JETSTREAM_STREAM: \"TARKA\"|JETSTREAM_STREAM: \"${JETSTREAM_STREAM}\"|g" \
  -e "s|JETSTREAM_SUBJECT: \"tarka.alerts\"|JETSTREAM_SUBJECT: \"${JETSTREAM_SUBJECT}\"|g" \
  -e "s|GOOGLE_CLOUD_PROJECT: \"REPLACE_ME_GOOGLE_CLOUD_PROJECT\"|GOOGLE_CLOUD_PROJECT: \"${GOOGLE_CLOUD_PROJECT}\"|g" \
  -e "s|GOOGLE_CLOUD_LOCATION: \"us-central1\"|GOOGLE_CLOUD_LOCATION: \"${GOOGLE_CLOUD_LOCATION}\"|g" \
  -e "s|LANGSMITH_TRACING: \"REPLACE_ME_LANGSMITH_TRACING\"|LANGSMITH_TRACING: \"${LANGSMITH_TRACING}\"|g" \
  -e "s|LANGSMITH_PROJECT: \"tarka\"|LANGSMITH_PROJECT: \"${LANGSMITH_PROJECT}\"|g" \
  -e "s|LANGSMITH_TAGS: \"\"|LANGSMITH_TAGS: \"${LANGSMITH_TAGS}\"|g" \
  -e "s|LLM_ENABLED: \"false\"|LLM_ENABLED: \"${LLM_ENABLED}\"|g" \
  -e "s|LLM_PROVIDER: \"vertexai\"|LLM_PROVIDER: \"${LLM_PROVIDER}\"|g" \
  -e "s|LLM_MODEL: \"gemini-2.5-flash\"|LLM_MODEL: \"${LLM_MODEL}\"|g" \
  -e "s|LLM_TEMPERATURE: \"0.2\"|LLM_TEMPERATURE: \"${LLM_TEMPERATURE}\"|g" \
  -e "s|LLM_MAX_OUTPUT_TOKENS: \"4096\"|LLM_MAX_OUTPUT_TOKENS: \"${LLM_MAX_OUTPUT_TOKENS}\"|g" \
  -e "s|LLM_MOCK: \"false\"|LLM_MOCK: \"${LLM_MOCK}\"|g" \
  -e "s|LLM_INCLUDE_LOGS: \"false\"|LLM_INCLUDE_LOGS: \"${LLM_INCLUDE_LOGS}\"|g" \
  -e "s|LLM_REDACT_INFRASTRUCTURE: \"true\"|LLM_REDACT_INFRASTRUCTURE: \"${LLM_REDACT_INFRASTRUCTURE}\"|g" \
  -e "s|LANGSMITH_RUN_NAME_PREFIX: \"\"|LANGSMITH_RUN_NAME_PREFIX: \"${LANGSMITH_RUN_NAME_PREFIX}\"|g" \
  -e "s|AWS_EVIDENCE_ENABLED: \"false\"|AWS_EVIDENCE_ENABLED: \"${AWS_EVIDENCE_ENABLED}\"|g" \
  -e "s|AWS_CLOUDTRAIL_LOOKBACK_MINUTES: \"30\"|AWS_CLOUDTRAIL_LOOKBACK_MINUTES: \"${AWS_CLOUDTRAIL_LOOKBACK_MINUTES}\"|g" \
  -e "s|AWS_CLOUDTRAIL_MAX_EVENTS: \"50\"|AWS_CLOUDTRAIL_MAX_EVENTS: \"${AWS_CLOUDTRAIL_MAX_EVENTS}\"|g" \
  -e "s|CHAT_ALLOW_AWS_READ: \"false\"|CHAT_ALLOW_AWS_READ: \"${CHAT_ALLOW_AWS_READ}\"|g" \
  -e "s|CHAT_AWS_REGION_ALLOWLIST: \"\"|CHAT_AWS_REGION_ALLOWLIST: \"${CHAT_AWS_REGION_ALLOWLIST}\"|g" \
  -e "s|GITHUB_EVIDENCE_ENABLED: \"false\"|GITHUB_EVIDENCE_ENABLED: \"${GITHUB_EVIDENCE_ENABLED}\"|g" \
  -e "s|CHAT_ALLOW_GITHUB_READ: \"false\"|CHAT_ALLOW_GITHUB_READ: \"${CHAT_ALLOW_GITHUB_READ}\"|g" \
  -e "s|CHAT_GITHUB_REPO_ALLOWLIST: \"\"|CHAT_GITHUB_REPO_ALLOWLIST: \"${CHAT_GITHUB_REPO_ALLOWLIST}\"|g" \
  -e "s|GITHUB_DEFAULT_ORG: \"\"|GITHUB_DEFAULT_ORG: \"${GITHUB_DEFAULT_ORG}\"|g" \
  -e "s|CHAT_ALLOW_K8S_EVENTS: \"true\"|CHAT_ALLOW_K8S_EVENTS: \"${CHAT_ALLOW_K8S_EVENTS}\"|g" \
  "${K8S_DIR}/configmap.yaml" > "${_rendered_configmap}"

if grep -q 'REPLACE_ME_' "${_rendered_configmap}"; then
  log_error "Rendered ConfigMap still contains placeholder values (REPLACE_ME_*)"
  echo ""
  echo "Missing substitutions:"
  grep 'REPLACE_ME_' "${_rendered_configmap}" || true
  echo ""
  log_info "Fix: set the missing environment variables or update deploy.sh substitutions"
  log_info "Rendered file: ${_rendered_configmap}"
  exit 1
fi

# Debug: Show key ConfigMap values being applied
log_info "ConfigMap values being applied:"
echo "  LLM_ENABLED: $(grep 'LLM_ENABLED:' "${_rendered_configmap}" | head -1 || echo 'NOT FOUND')"
echo "  LLM_PROVIDER: $(grep 'LLM_PROVIDER:' "${_rendered_configmap}" | head -1 || echo 'NOT FOUND')"
echo "  LLM_MODEL: $(grep 'LLM_MODEL:' "${_rendered_configmap}" | head -1 || echo 'NOT FOUND')"
echo "  LLM_INCLUDE_LOGS: $(grep 'LLM_INCLUDE_LOGS:' "${_rendered_configmap}" | head -1 || echo 'NOT FOUND')"
echo "  LLM_REDACT_INFRASTRUCTURE: $(grep 'LLM_REDACT_INFRASTRUCTURE:' "${_rendered_configmap}" | head -1 || echo 'NOT FOUND')"
echo "  AWS_EVIDENCE_ENABLED: $(grep 'AWS_EVIDENCE_ENABLED:' "${_rendered_configmap}" | head -1 || echo 'NOT FOUND')"
echo "  GITHUB_EVIDENCE_ENABLED: $(grep 'GITHUB_EVIDENCE_ENABLED:' "${_rendered_configmap}" | head -1 || echo 'NOT FOUND')"
echo ""

kubectl apply -f "${_rendered_configmap}" >/dev/null
rm -f "${_rendered_configmap}"
log_success "ConfigMap applied"

log_info "Applying service catalog ConfigMap..."
kubectl create configmap tarka-service-catalog \
  --from-file=service-catalog.yaml="${ROOT_DIR}/config/service-catalog.yaml" \
  --from-file=third-party-catalog.yaml="${ROOT_DIR}/config/third-party-catalog.yaml" \
  -n tarka --dry-run=client -o yaml | kubectl apply -f - >/dev/null
log_success "Service catalog ConfigMap applied"

log_info "Applying External Secrets (AWS Secrets Manager -> K8s Secret)..."

# Detect External Secrets API version (v1 or v1beta1)
ESO_API_VERSION=$(kubectl api-resources --api-group=external-secrets.io 2>/dev/null | grep "^secretstores" | awk '{print $3}')

if [[ -z "${ESO_API_VERSION}" ]]; then
  log_error "External Secrets Operator not found. Please install it first:"
  log_info "  helm repo add external-secrets https://charts.external-secrets.io"
  log_info "  helm install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace"
  exit 1
fi

log_info "Using External Secrets API version: ${ESO_API_VERSION}"

# Apply SecretStore with detected API version
sed -e "s|us-east-1|${AWS_REGION}|g" \
    -e "s|external-secrets.io/v1beta1|${ESO_API_VERSION}|g" \
    "${K8S_DIR}/secretstore.yaml" | kubectl apply -f - >/dev/null

# Apply ExternalSecret with detected API version
sed -e "s|key: tarka|key: ${ASM_SECRET_NAME}|g" \
    -e "s|external-secrets.io/v1beta1|${ESO_API_VERSION}|g" \
    "${K8S_DIR}/externalsecret.yaml" | kubectl apply -f - >/dev/null

log_success "External Secrets configured"

if [[ "${ENABLE_DEV_POSTGRES}" == "1" ]]; then
  log_info "Waiting for secret sync (POSTGRES_PASSWORD)..."
  _have_pg_pw="0"
  for _i in {1..30}; do
    if kubectl -n tarka get secret tarka -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | grep -q '.'; then
      _have_pg_pw="1"
      break
    fi
    sleep 2
  done
  if [[ "${_have_pg_pw}" != "1" ]]; then
    log_warning "POSTGRES_PASSWORD not found in secret yet"
    log_info "Ensure AWS secret ${ASM_SECRET_NAME} includes POSTGRES_PASSWORD"
  else
    log_success "Secret synced"
  fi

  log_info "Deploying PostgreSQL (in-cluster)..."
  kubectl apply -f "${K8S_DIR}/postgres-dev.yaml" >/dev/null

  log_info "Waiting for PostgreSQL to be ready..."
  kubectl -n tarka rollout status deploy/tarka-postgres --timeout=180s >/dev/null 2>&1
  log_success "PostgreSQL ready"
fi

log_info "Deploying NATS JetStream..."
kubectl apply -f "${K8S_DIR}/nats-jetstream.yaml" >/dev/null
log_info "Waiting for NATS to be ready..."
kubectl -n tarka rollout status statefulset/nats --timeout=180s >/dev/null 2>&1
log_success "NATS ready"

log_info "Applying webhook service..."
kubectl apply -f "${K8S_DIR}/service.yaml" >/dev/null
log_success "Service created"

log_info "Rendering and applying webhook deployment..."
_rendered_webhook_deploy="$(mktemp)"
sed \
  -e "s|REPLACE_ME_GCP_WIF_AUDIENCE|${GCP_WIF_AUDIENCE}|g" \
  -e "s|REPLACE_ME_IMAGE|${IMAGE}|g" \
  "${K8S_DIR}/deployment.yaml" > "${_rendered_webhook_deploy}"
if grep -q 'REPLACE_ME_' "${_rendered_webhook_deploy}"; then
  log_error "Rendered webhook deployment contains placeholder values (REPLACE_ME_*)"
  echo ""
  echo "Missing substitutions:"
  grep 'REPLACE_ME_' "${_rendered_webhook_deploy}" || true
  echo ""
  log_info "Rendered file: ${_rendered_webhook_deploy}"
  exit 1
fi
kubectl apply -f "${_rendered_webhook_deploy}" >/dev/null
rm -f "${_rendered_webhook_deploy}"
log_success "Webhook deployment applied"

log_info "Rendering and applying worker deployment..."
_rendered_worker_deploy="$(mktemp)"
sed \
  -e "s|REPLACE_ME_GCP_WIF_AUDIENCE|${GCP_WIF_AUDIENCE}|g" \
  -e "s|REPLACE_ME_IMAGE|${IMAGE}|g" \
  "${K8S_DIR}/worker-deployment.yaml" > "${_rendered_worker_deploy}"
if grep -q 'REPLACE_ME_' "${_rendered_worker_deploy}"; then
  log_error "Rendered worker deployment contains placeholder values (REPLACE_ME_*)"
  echo ""
  echo "Missing substitutions:"
  grep 'REPLACE_ME_' "${_rendered_worker_deploy}" || true
  echo ""
  log_info "Rendered file: ${_rendered_worker_deploy}"
  exit 1
fi
kubectl apply -f "${_rendered_worker_deploy}" >/dev/null
rm -f "${_rendered_worker_deploy}"
log_info "Waiting for workers to be ready..."
kubectl -n tarka rollout status deploy/tarka-worker --timeout=180s >/dev/null 2>&1
log_success "Workers ready"

log_info "Deploying Console UI..."
kubectl apply -f "${K8S_DIR}/console-ui-service.yaml" >/dev/null
sed "s|REPLACE_ME_UI_IMAGE|${UI_IMAGE}|g" \
  "${K8S_DIR}/console-ui-deployment.yaml" | kubectl apply -f - >/dev/null
log_success "Console UI deployed"

show_deployment_summary
