#!/usr/bin/env bash
# Validates LLM configuration before deployment

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}ℹ${NC} $*"; }
log_success() { echo -e "${GREEN}✓${NC} $*"; }
log_warning() { echo -e "${YELLOW}⚠${NC} $*"; }
log_error() { echo -e "${RED}✗${NC} $*"; }

# Load environment (if .env.deploy exists)
if [[ -f .env.deploy ]]; then
  source .env.deploy
  log_info "Loaded configuration from .env.deploy"
else
  log_warning "No .env.deploy file found - using environment variables only"
fi

# Default values
LLM_ENABLED="${LLM_ENABLED:-false}"
LLM_PROVIDER="${LLM_PROVIDER:-vertexai}"
LLM_MODEL="${LLM_MODEL:-gemini-2.5-flash}"
GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

echo ""
echo "=================================="
echo "LLM Configuration Validation"
echo "=================================="
echo ""

# Check if LLM is enabled
if [[ "${LLM_ENABLED}" != "true" ]]; then
  log_info "LLM_ENABLED=false - LLM enrichment disabled"
  log_info "Agent will use deterministic analysis only (no AI-powered features)"
  log_info "Docker image will be built without LLM SDKs (smaller image)"
  echo ""
  log_success "Configuration valid for deterministic mode"
  exit 0
fi

log_info "LLM_ENABLED=true - validating provider configuration..."
echo ""

# Validate provider
case "${LLM_PROVIDER}" in
  vertexai|vertex|gcp_vertexai)
    log_info "Provider: Vertex AI (Google Cloud Gemini)"
    log_info "Model: ${LLM_MODEL}"
    echo ""

    # Check required variables
    if [[ -z "${GOOGLE_CLOUD_PROJECT}" ]]; then
      log_error "GOOGLE_CLOUD_PROJECT not set (required for Vertex AI)"
      echo "  Set: export GOOGLE_CLOUD_PROJECT=your-gcp-project"
      exit 1
    fi

    log_success "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}"

    if [[ -z "${GOOGLE_CLOUD_LOCATION}" ]]; then
      log_warning "GOOGLE_CLOUD_LOCATION not set (defaulting to us-central1)"
    else
      log_success "GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION}"
    fi

    # Check if gcloud is available (for local testing)
    if command -v gcloud &> /dev/null; then
      log_info "Testing ADC credentials..."
      if gcloud auth application-default print-access-token &> /dev/null; then
        log_success "Application Default Credentials (ADC) configured"
      else
        log_warning "ADC not configured locally (OK if deploying with Workload Identity)"
        log_info "To test locally: gcloud auth application-default login"
      fi
    fi

    echo ""
    log_success "Vertex AI configuration valid"
    log_info "Docker image will be built with: poetry install -E vertex"
    echo ""
    log_info "Authentication: Workload Identity Federation (in-cluster)"
    log_info "Make sure GCP_WIF_AUDIENCE and GCP_WIF_CRED_JSON are configured"
    ;;

  anthropic)
    log_info "Provider: Anthropic Claude"
    log_info "Model: ${LLM_MODEL}"
    log_info "Extended thinking: Always enabled (1024 tokens)"
    echo ""

    # Check required variables
    if [[ -z "${ANTHROPIC_API_KEY}" ]]; then
      log_error "ANTHROPIC_API_KEY not set (required for Anthropic)"
      echo "  Get your API key from: https://console.anthropic.com/"
      echo "  Set: export ANTHROPIC_API_KEY=sk-ant-api03-..."
      exit 1
    fi

    # Validate API key format
    if [[ ! "${ANTHROPIC_API_KEY}" =~ ^sk-ant- ]]; then
      log_warning "ANTHROPIC_API_KEY doesn't start with 'sk-ant-' - may be invalid"
    else
      log_success "ANTHROPIC_API_KEY format looks valid"
    fi

    # Check if key is too short
    if [[ ${#ANTHROPIC_API_KEY} -lt 20 ]]; then
      log_error "ANTHROPIC_API_KEY too short - probably invalid"
      exit 1
    fi

    log_success "ANTHROPIC_API_KEY set (${#ANTHROPIC_API_KEY} characters)"

    echo ""
    log_success "Anthropic configuration valid"
    log_info "Docker image will be built with: poetry install -E anthropic"
    echo ""
    log_info "Authentication: API key (will be stored in AWS Secrets Manager)"
    ;;

  *)
    log_error "Unknown LLM_PROVIDER: ${LLM_PROVIDER}"
    echo "  Valid options: vertexai, anthropic"
    exit 1
    ;;
esac

# Check model name
echo ""
log_info "Validating model name..."
case "${LLM_PROVIDER}" in
  vertexai|vertex|gcp_vertexai)
    if [[ "${LLM_MODEL}" =~ ^gemini ]]; then
      log_success "Model name looks valid for Vertex AI: ${LLM_MODEL}"
    else
      log_warning "Model name doesn't start with 'gemini' - may be invalid"
      log_info "Common models: gemini-2.5-flash, gemini-2.0-pro"
    fi
    ;;
  anthropic)
    if [[ "${LLM_MODEL}" =~ ^claude ]]; then
      log_success "Model name looks valid for Anthropic: ${LLM_MODEL}"
    else
      log_warning "Model name doesn't start with 'claude' - may be invalid"
      log_info "Common models: claude-3-5-sonnet-20241022, claude-3-opus-20240229"
    fi
    ;;
esac

# Summary
echo ""
echo "=================================="
echo "Validation Summary"
echo "=================================="
echo ""
log_success "LLM configuration is valid!"
echo ""
echo "  Provider: ${LLM_PROVIDER}"
echo "  Model: ${LLM_MODEL}"
echo "  Poetry extras: $(case ${LLM_PROVIDER} in vertexai|vertex|gcp_vertexai) echo vertex;; anthropic) echo anthropic;; esac)"
echo ""
log_info "Next steps:"
echo "  1. Apply deploy.sh.patch to add LLM support to deploy.sh"
echo "  2. Run: ./deploy.sh"
echo "  3. Verify: kubectl logs -n tarka -l app=tarka-worker --tail=20"
echo ""
