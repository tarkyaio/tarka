#!/usr/bin/env bash
set -euo pipefail

# sync-secrets.sh — Sync K8s Secret from AWS Secrets Manager (on-demand).
#
# Usage:
#   ASM_SECRET_NAME=tarka AWS_REGION=us-east-1 ./scripts/sync-secrets.sh
#   ./scripts/sync-secrets.sh --restart   # also rolling-restart deployments
#
# Environment variables:
#   ASM_SECRET_NAME  AWS Secrets Manager secret name  (default: tarka)
#   AWS_REGION       AWS region                       (default: us-east-1)
#   NAMESPACE        Kubernetes namespace              (default: tarka)

export AWS_PAGER=""

ASM_SECRET_NAME="${ASM_SECRET_NAME:-tarka}"
AWS_REGION="${AWS_REGION:-us-east-1}"
NAMESPACE="${NAMESPACE:-tarka}"

RESTART=0
for arg in "$@"; do
    case "${arg}" in
        --restart) RESTART=1 ;;
        *) echo "Unknown argument: ${arg}"; exit 1 ;;
    esac
done

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}i${NC} $*"; }
log_success() { echo -e "${GREEN}+${NC} $*"; }
log_error()   { echo -e "${RED}x${NC} $*" >&2; }

# Fetch secret JSON from ASM
log_info "Fetching secret from AWS Secrets Manager (${ASM_SECRET_NAME}, ${AWS_REGION})..."
SECRET_JSON="$(aws secretsmanager get-secret-value \
    --secret-id "${ASM_SECRET_NAME}" \
    --region "${AWS_REGION}" \
    --query 'SecretString' \
    --output text)"

if [[ -z "${SECRET_JSON}" ]]; then
    log_error "Empty secret returned from ASM"
    exit 1
fi

# Generate and apply K8s Secret
log_info "Applying K8s Secret in namespace ${NAMESPACE}..."
python3 -c "
import json, sys, base64
data = json.loads(sys.stdin.read())
secret = {
    'apiVersion': 'v1',
    'kind': 'Secret',
    'metadata': {
        'name': 'tarka',
        'namespace': '${NAMESPACE}',
        'labels': {'app.kubernetes.io/managed-by': 'deploy-script'}
    },
    'type': 'Opaque',
    'data': {
        k: base64.b64encode(v.encode('utf-8')).decode('ascii')
        for k, v in sorted(data.items()) if v
    }
}
print(json.dumps(secret))
" <<< "${SECRET_JSON}" | kubectl apply -f -

log_success "K8s Secret synced"

# Optional: rolling restart deployments that consume the secret
if [[ "${RESTART}" == "1" ]]; then
    log_info "Rolling restart of deployments in ${NAMESPACE}..."
    for deploy in tarka-webhook tarka-worker; do
        if kubectl -n "${NAMESPACE}" get deploy "${deploy}" >/dev/null 2>&1; then
            kubectl -n "${NAMESPACE}" rollout restart deploy/"${deploy}" >/dev/null
            log_success "Restarted ${deploy}"
        fi
    done
fi
