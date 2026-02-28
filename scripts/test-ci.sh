#!/bin/bash
set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Global variables for cleanup
WEBHOOK_PID=""
WORKER_PID=""
DOCKER_STARTED=false

# Global variables for Node.js binaries
NODE_BIN=""
NPM_BIN=""

# Logging helpers
log_phase() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_error() {
    echo -e "${RED}✗ $1${NC}"
}

log_info() {
    echo -e "${YELLOW}→ $1${NC}"
}

# Cleanup function (called by trap and on normal exit)
cleanup() {
    local exit_code=$?

    log_phase "Cleaning up..."

    # Kill background processes
    if [ -n "$WEBHOOK_PID" ]; then
        log_info "Stopping webhook server (PID: $WEBHOOK_PID)"
        kill "$WEBHOOK_PID" 2>/dev/null || true
        wait "$WEBHOOK_PID" 2>/dev/null || true
    fi

    if [ -n "$WORKER_PID" ]; then
        log_info "Stopping worker (PID: $WORKER_PID)"
        kill "$WORKER_PID" 2>/dev/null || true
        wait "$WORKER_PID" 2>/dev/null || true
    fi

    # Stop Docker Compose
    if [ "$DOCKER_STARTED" = true ]; then
        log_info "Stopping Docker Compose services"
        docker compose down -v 2>/dev/null || true
    fi

    # Conditional artifact cleanup
    if [ $exit_code -ne 0 ]; then
        log_error "Tests FAILED - Preserving logs for debugging:"
        echo "  - webhook.log"
        echo "  - worker.log"
        echo "  - investigations/"
        echo "  - .cache/node/ (Node.js cache)"
        echo ""
        echo "Last 100 lines of webhook.log:"
        echo "----------------------------------------"
        tail -100 webhook.log 2>/dev/null || echo "No webhook logs found"
        echo ""
        echo "Last 100 lines of worker.log:"
        echo "----------------------------------------"
        tail -100 worker.log 2>/dev/null || echo "No worker logs found"
        echo ""
        echo "To clean up manually, run: make clean"
    else
        log_success "All tests passed!"
        log_info "Running make clean..."
        make clean >/dev/null 2>&1 || {
            log_info "Falling back to manual cleanup"
            rm -f .env webhook.log worker.log
            rm -rf investigations/ .cache/node/
        }
    fi

    exit $exit_code
}

# Set up trap for cleanup
trap cleanup EXIT INT TERM

# Setup Node.js (auto-download if needed, like GHA)
setup_nodejs() {
    local min_version="18.19.0"
    local target_version="20.19.1"
    local node_cache_dir=".cache/node"

    # Check if system Node.js is suitable (18.19+)
    if command -v node >/dev/null 2>&1; then
        local system_version=$(node --version | sed 's/v//')

        # Compare versions (simple lexicographic comparison works for x.y.z)
        if [ "$(printf '%s\n' "$min_version" "$system_version" | sort -V | head -n1)" = "$min_version" ]; then
            log_info "Using system Node.js v$system_version"
            NODE_BIN="node"
            NPM_BIN="npm"
            return 0
        else
            log_info "System Node.js v$system_version is too old (need 18.19+)"
        fi
    else
        log_info "Node.js not found on system"
    fi

    # Download Node.js 20 to local cache (like GHA does)
    log_info "Setting up Node.js v$target_version (like GHA)..."

    # Detect platform
    local os=$(uname -s | tr '[:upper:]' '[:lower:]')
    local arch=$(uname -m)

    case "$os" in
        darwin) os="darwin" ;;
        linux) os="linux" ;;
        *)
            log_error "Unsupported OS: $os"
            exit 1
            ;;
    esac

    case "$arch" in
        x86_64) arch="x64" ;;
        aarch64|arm64) arch="arm64" ;;
        *)
            log_error "Unsupported architecture: $arch"
            exit 1
            ;;
    esac

    # Use absolute paths to avoid issues with directory changes
    local node_dir="$(pwd)/$node_cache_dir/node-v${target_version}-${os}-${arch}"
    local node_bin="$node_dir/bin/node"
    local npm_bin="$node_dir/bin/npm"

    # Check if already cached
    if [ -f "$node_bin" ]; then
        log_info "Using cached Node.js v$target_version from $node_dir"
        NODE_BIN="$node_bin"
        NPM_BIN="$npm_bin"
        export PATH="$node_dir/bin:$PATH"
        return 0
    fi

    # Download and extract Node.js
    log_info "Downloading Node.js v$target_version for $os-$arch..."
    local abs_cache_dir="$(pwd)/$node_cache_dir"
    mkdir -p "$abs_cache_dir"

    local node_url="https://nodejs.org/dist/v${target_version}/node-v${target_version}-${os}-${arch}.tar.gz"
    local tarball="$abs_cache_dir/node.tar.gz"

    if ! curl -fsSL "$node_url" -o "$tarball"; then
        log_error "Failed to download Node.js from $node_url"
        exit 1
    fi

    log_info "Extracting Node.js..."
    if ! tar -xzf "$tarball" -C "$abs_cache_dir"; then
        log_error "Failed to extract Node.js"
        rm -f "$tarball"
        exit 1
    fi

    rm -f "$tarball"

    if [ ! -f "$node_bin" ]; then
        log_error "Node.js binary not found after extraction: $node_bin"
        exit 1
    fi

    log_info "Node.js v$target_version installed to $node_dir"
    NODE_BIN="$node_bin"
    NPM_BIN="$npm_bin"
    export PATH="$node_dir/bin:$PATH"
}

# Check prerequisites
check_prerequisites() {
    log_phase "Checking Prerequisites"

    # Check Docker
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker is not running. Please start Docker and try again."
        exit 1
    fi
    log_success "Docker is running"

    # Check Poetry
    if ! command -v poetry >/dev/null 2>&1; then
        log_error "Poetry is not installed. Please install Poetry and try again."
        exit 1
    fi
    log_success "Poetry is available"

    # Ensure Python virtualenv and dependencies are installed
    log_info "Ensuring Python dependencies are installed..."
    if poetry install --no-interaction 2>&1; then
        log_success "Python dependencies installed"
    else
        log_error "Failed to install Python dependencies"
        exit 1
    fi

    # Check/setup Node.js (auto-install if needed, like GHA)
    setup_nodejs
    log_success "Node.js is available ($(${NODE_BIN} --version))"
    log_info "Node binary: ${NODE_BIN}"
    log_info "NPM binary: ${NPM_BIN}"
}

# Main execution flow
main() {
    log_phase "Starting Complete Test Suite (CI Mode)"

    # Clear env vars early to prevent shell pollution from affecting tests
    log_info "Clearing environment variables to prevent pollution..."
    unset AUTH_SESSION_SECRET ADMIN_INITIAL_USERNAME ADMIN_INITIAL_PASSWORD
    unset PROMETHEUS_URL ALERTMANAGER_URL LOGS_URL NATS_URL POSTGRES_URL
    unset POSTGRES_DSN POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
    unset LLM_ENABLED LLM_MOCK AWS_EVIDENCE_ENABLED GITHUB_EVIDENCE_ENABLED
    unset CHAT_ALLOW_AWS_READ CHAT_ALLOW_GITHUB_READ CHAT_ALLOW_K8S_EVENTS
    unset LOCAL_STORAGE_DIR OIDC_DISCOVERY_URL OIDC_CLIENT_ID OIDC_CLIENT_SECRET
    unset MEMORY_ENABLED DB_AUTO_MIGRATE

    check_prerequisites

    # Phase 1: Pre-commit hooks
    log_phase "Phase 1: Pre-commit Hooks"
    if poetry run pre-commit run --all-files --show-diff-on-failure; then
        log_success "Pre-commit hooks passed"
    else
        log_error "Pre-commit hooks failed"
        exit 1
    fi

    # Phase 2: Unit tests
    log_phase "Phase 2: Unit Tests"
    if poetry run pytest -m "not integration and not e2e" -vv; then
        log_success "Unit tests passed"
    else
        log_error "Unit tests failed"
        exit 1
    fi

    # Phase 3: Integration tests
    log_phase "Phase 3: Integration Tests"
    if poetry run pytest -m integration -vv; then
        log_success "Integration tests passed"
    else
        log_error "Integration tests failed"
        exit 1
    fi

    # Phase 4: Start Docker services
    log_phase "Phase 4: Starting Docker Compose Services"
    if docker compose up -d --build; then
        DOCKER_STARTED=true
        log_info "Waiting for services to be healthy (60s timeout)..."
    else
        log_error "Failed to start Docker Compose services"
        docker compose logs
        exit 1
    fi

    if timeout 60 bash -c 'until docker compose ps | grep -q "healthy"; do sleep 2; done'; then
        log_success "Docker services are healthy"
    else
        log_error "Docker services failed to become healthy"
        docker compose ps
        docker compose logs
        exit 1
    fi

    # Phase 5: Setup environment
    log_phase "Phase 5: Setting Up Test Environment"

    # Generate .env from scratch (matching docker-compose.yml services)
    if ! cat > .env <<EOF
# Data sources (matching docker-compose.yml)
PROMETHEUS_URL=http://localhost:18481/select/0/prometheus
ALERTMANAGER_URL=http://localhost:19093
LOGS_URL=http://localhost:19471
NATS_URL=nats://localhost:4222

# Postgres (matching docker-compose.yml: DB=tarka, user=postgres)
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=tarka
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
MEMORY_ENABLED=true
DB_AUTO_MIGRATE=true

# Auth
AUTH_SESSION_SECRET=ci-test-secret-$(openssl rand -hex 32)
ADMIN_INITIAL_USERNAME=testadmin
ADMIN_INITIAL_PASSWORD=testpass123

# App
LOCAL_STORAGE_DIR=./investigations
LLM_ENABLED=false
EOF
    then
        log_error "Failed to write test configuration to .env"
        exit 1
    fi

    if ! mkdir -p investigations; then
        log_error "Failed to create investigations directory"
        exit 1
    fi

    log_success "Environment configured (shell vars cleared)"

    # Phase 6: Start webhook server
    log_phase "Phase 6: Starting Webhook Server"
    # Kill any stale process on port 8080 from a previous run
    local stale_pid
    stale_pid=$(lsof -ti :8080 2>/dev/null || true)
    if [ -n "$stale_pid" ]; then
        log_info "Killing stale process on port 8080 (PID: $stale_pid)"
        kill "$stale_pid" 2>/dev/null || true
        sleep 1
    fi
    # Use clean environment to prevent pollution from shell variables
    env -i PATH="$PATH" HOME="$HOME" USER="$USER" bash ./scripts/run-serve.sh > webhook.log 2>&1 &
    WEBHOOK_PID=$!
    log_info "Webhook server starting (PID: $WEBHOOK_PID)"
    log_info "Waiting for webhook to be ready (30s timeout)..."

    if timeout 30 bash -c 'until curl -sf http://localhost:8080/healthz 2>/dev/null | grep -q "ok"; do sleep 1; done'; then
        log_success "Webhook server is ready"
    else
        log_error "Webhook server failed to start"
        tail -50 webhook.log
        exit 1
    fi

    # Phase 7: Start worker
    log_phase "Phase 7: Starting Worker"
    # Use clean environment to prevent pollution from shell variables
    env -i PATH="$PATH" HOME="$HOME" USER="$USER" bash ./scripts/run-worker.sh > worker.log 2>&1 &
    WORKER_PID=$!
    log_info "Worker starting (PID: $WORKER_PID)"
    log_info "Waiting for worker to be ready (15s timeout)..."

    if timeout 15 bash -c 'until grep -q "Worker started" worker.log 2>/dev/null; do sleep 1; done'; then
        log_success "Worker is ready"
    else
        log_error "Worker failed to start"
        tail -50 worker.log
        exit 1
    fi

    # Phase 8: Send test alert
    log_phase "Phase 8: Sending Test Alert"
    # Using KubeJobFailed to test e2e quality check
    if curl -X POST http://localhost:8080/alerts \
        -H "Content-Type: application/json" \
        -d @tests/fixtures/test-alert-kubejobfailed.json \
        -f -s -o /dev/null; then
        log_success "Test alert sent successfully"
    else
        log_error "Failed to send test alert"
        exit 1
    fi

    # Phase 9: Wait for investigation
    log_phase "Phase 9: Waiting for Investigation to Complete"
    log_info "Waiting for investigation report (60s timeout, strict)..."

    if timeout 60 bash -c '
        while true; do
            if ls ./investigations/*.md 2>/dev/null | head -1 >/dev/null; then
                echo "✓ Investigation report created!"
                exit 0
            fi
            if grep -q "Message ACKed" worker.log 2>/dev/null; then
                echo "✓ Worker processed alert successfully"
                exit 0
            fi
            sleep 2
        done
    '; then
        log_success "Investigation completed"
        if ls ./investigations/*.md 2>/dev/null | head -1; then
            log_info "Report location: $(ls ./investigations/*.md 2>/dev/null | head -1)"
        fi
    else
        log_error "Investigation did not complete within 60 seconds"
        log_info "Recent worker logs:"
        tail -30 worker.log
        exit 1
    fi

    # Phase 10: Backend e2e tests
    log_phase "Phase 10: Backend E2E Tests"
    # Source .env to ensure tests use same credentials as server
    set -a
    source .env
    set +a
    if poetry run pytest -m e2e -v; then
        log_success "Backend e2e tests passed"
    else
        log_error "Backend e2e tests failed"
        exit 1
    fi

    # Phase 11: UI e2e tests
    log_phase "Phase 11: UI E2E Tests (Playwright)"
    if [ ! -d ui/node_modules ]; then
        log_info "Installing UI dependencies..."
        (cd ui && ${NPM_BIN} install) || {
            log_error "Failed to install UI dependencies"
            exit 1
        }
    fi

    log_info "Running Playwright tests (chromium)..."
    (cd ui && ${NPM_BIN} run test:e2e -- --project=chromium) || {
        log_error "Playwright tests failed"
        exit 1
    }
    log_success "UI e2e tests passed"

    log_phase "Test Suite Complete"
}

main "$@"
