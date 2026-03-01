-include .env
export

.PHONY: help
.PHONY: install test test-integration test-all test-ui test-ui-e2e test-ci clean coverage
.PHONY: lint format run
.PHONY: dev-up dev-down dev-restart dev-logs dev-serve dev-worker dev-ui dev-test dev-send-alert dev-clean

help:
	@echo "Available targets:"
	@echo "  make install           - Install dependencies with Poetry"
	@echo "  make test              - Run unit tests (excludes integration)"
	@echo "  make test-integration  - Run integration tests (requires NATS)"
	@echo "  make test-all          - Run all Python tests"
	@echo "  make test-ui           - Run UI unit tests (Vitest)"
	@echo "  make test-ui-e2e       - Run UI e2e tests (Playwright, requires Node 20+)"
	@echo "  make test-ci           - Run ALL tests like in CI (pre-commit + pytest + playwright)"
	@echo "  make coverage          - Run tests with coverage report"
	@echo "  make clean             - Remove cache files and coverage data"
	@echo "  make run               - Run the agent (list alerts)"
	@echo "  make lint              - Run linting (if configured)"
	@echo "  make format            - Format code with Black and isort"
	@echo "  make format-check      - Check code formatting without changes"
	@echo "  make pre-commit        - Run all pre-commit hooks"
	@echo ""
	@echo "Local Development:"
	@echo "  make dev-up            - Start local services (PostgreSQL, NATS, mocks)"
	@echo "  make dev-down          - Stop and remove local services"
	@echo "  make dev-restart       - Restart local services"
	@echo "  make dev-logs          - Show logs from all services"
	@echo "  make dev-serve         - Start webhook server (requires dev-up first)"
	@echo "  make dev-worker        - Start worker (requires dev-up first)"
	@echo "  make dev-ui            - Start UI dev server (requires dev-serve first)"
	@echo "  make dev-test          - Run e2e tests against local environment"
	@echo "  make dev-send-alert    - Send test alert to webhook"
	@echo "  make dev-clean         - Clean all development artifacts"

install:
	poetry install

test:
	rm -f .coverage*
	rm -rf .pytest_cache
	poetry run pytest -m "not integration and not e2e" -vv

test-integration:
	rm -f .coverage*
	rm -rf .pytest_cache
	poetry run pytest -m integration -vv

test-all:
	rm -f .coverage*
	rm -rf .pytest_cache
	poetry run pytest -vv

coverage:
	rm -f .coverage*
	rm -rf .pytest_cache htmlcov/
	poetry run pytest --cov=agent --cov-report=html --cov-report=term-missing -vv
	@echo "Coverage report generated in htmlcov/index.html"

clean: ## Clean all test artifacts and caches
	@echo "Cleaning Python artifacts..."
	@rm -f .coverage*
	@rm -rf .pytest_cache htmlcov/ .mypy_cache/ .ruff_cache/
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "Cleaning test-ci artifacts..."
	@rm -rf .cache/node/
	@rm -f .env webhook.log worker.log
	@rm -rf investigations/
	@echo "✓ Cleanup complete"

run:
	poetry run python main.py --list-alerts

lint:
	@echo "Linting not configured yet. Consider adding ruff or pylint."

format:
	poetry run black .
	poetry run isort .

pre-commit:
	poetry run pre-commit run --all-files --show-diff-on-failure

format-check:
	poetry run black --check .
	poetry run isort --check .

# ==============================================================================
# UI Tests
# ==============================================================================

test-ui: ## Run UI unit tests (Vitest)
	@echo "Running UI unit tests..."
	@if [ ! -d ui/node_modules ]; then \
		echo "⚠ Installing UI dependencies first..."; \
		cd ui && npm install; \
		echo ""; \
	fi
	cd ui && npm test

test-ui-e2e: ## Run UI e2e tests (Playwright) - requires Node.js 20+
	@echo "Running UI e2e tests (Playwright)..."
	@if [ ! -d ui/node_modules ]; then \
		echo "⚠ Installing UI dependencies first..."; \
		cd ui && npm install; \
		echo ""; \
	fi
	@echo "Note: Using Node.js 20.19.4 via nvm (if available)"
	@if command -v nvm >/dev/null 2>&1; then \
		cd ui && bash -c "source ~/.nvm/nvm.sh && nvm use 20.19.4 && npm run test:e2e"; \
	else \
		echo "⚠ nvm not found, using system Node.js"; \
		cd ui && npm run test:e2e; \
	fi

test-ci: clean ## Run ALL tests like in CI (requires Docker, fully automated)
	@echo "=========================================="
	@echo "Running Complete Test Suite (CI-like)"
	@echo "=========================================="
	@echo ""
	@echo "This will:"
	@echo "  1. Clean all artifacts (make clean)"
	@echo "  2. Auto-setup Node.js 20 (downloads if needed)"
	@echo "  3. Run pre-commit hooks"
	@echo "  4. Run Python unit tests"
	@echo "  5. Run Python integration tests"
	@echo "  6. Start Docker Compose services"
	@echo "  7. Start webhook server and worker"
	@echo "  8. Send test alert and verify investigation"
	@echo "  9. Run backend e2e tests"
	@echo "  10. Run UI e2e tests (Playwright)"
	@echo "  11. Clean up all services and artifacts"
	@echo ""
	@echo "Requirements: Docker running (Node.js auto-installed)"
	@echo ""
	@bash scripts/test-ci.sh

# ==============================================================================
# Local Development Targets
# ==============================================================================

dev-up: ## Start local development environment (PostgreSQL, NATS, mock services)
	@echo "Starting local development environment..."
	@docker compose up -d --build
	@echo "Waiting for services to be healthy..."
	@sleep 5
	@echo ""
	@echo "✓ Services started successfully!"
	@echo ""
	@echo "Available services:"
	@echo "  - PostgreSQL:        localhost:5432 (empty, migrations run on app start)"
	@echo "  - NATS JetStream:    localhost:4222"
	@echo "  - Mock Prometheus:   http://localhost:18481"
	@echo "  - Mock Alertmanager: http://localhost:19093"
	@echo "  - Mock VictoriaLogs: http://localhost:19471"
	@echo ""
	@echo "⚠️  Database migrations will run automatically when you start the webhook server."
	@echo ""
	@echo "Next steps:"
	@echo "  1. Copy .env.example to .env (if not done): cp .env.example .env"
	@echo "  2. Start webhook server: make dev-serve (runs migrations + starts API)"
	@echo "  3. Start UI: make dev-ui (in another terminal)"
	@echo "  4. Start worker: make dev-worker (optional, in another terminal)"
	@echo "  5. Open browser: http://localhost:5173"
	@echo ""
	@echo "Note: Mock services return empty data. To use real services,"
	@echo "      port-forward and update .env with real URLs."

dev-down: ## Stop and remove local development environment (fresh start next time)
	@echo "Stopping local development environment..."
	@docker compose down -v
	@echo "✓ All services stopped and volumes removed."

dev-restart: dev-down dev-up ## Restart local development environment

dev-logs: ## Show logs from all services
	@docker compose logs -f

dev-serve: ## Start the webhook server (requires dev-up first)
	@if [ ! -f .env ]; then \
		echo "⚠ Warning: .env file not found. Copying from .env.example..."; \
		cp .env.example .env; \
		echo "✓ Created .env file. Please review and update if needed."; \
		echo ""; \
	fi
	@echo "Starting webhook server on http://localhost:8080"
	@echo ""
	@echo "This will:"
	@echo "  1. Run database migrations (creates tables, admin user)"
	@echo "  2. Start API server"
	@echo ""
	@echo "Login: admin / admin123 (or credentials from .env)"
	@echo ""
	@./scripts/run-serve.sh

dev-worker: ## Start the worker (requires dev-up first)
	@if [ ! -f .env ]; then \
		echo "⚠ Warning: .env file not found. Copying from .env.example..."; \
		cp .env.example .env; \
		echo "✓ Created .env file. Please review and update if needed."; \
		echo ""; \
	fi
	@echo "Starting worker (NATS consumer)..."
	@./scripts/run-worker.sh

dev-ui: ## Start the UI dev server (requires dev-serve first)
	@echo "Starting UI development server..."
	@echo ""
	@echo "This will:"
	@echo "  1. Start Vite dev server on http://localhost:5173"
	@echo "  2. Proxy /api/* requests to backend (localhost:8080)"
	@echo "  3. Enable hot module reloading"
	@echo ""
	@if [ ! -d ui/node_modules ]; then \
		echo "⚠ Installing UI dependencies first..."; \
		cd ui && npm install; \
		echo ""; \
	fi
	@echo "UI will be available at: http://localhost:5173"
	@echo ""
	cd ui && npm run dev

dev-test: ## Run e2e tests against local environment
	@echo "Running e2e tests..."
	@if [ ! -f .env.test ]; then \
		echo "⚠ Warning: .env.test not found. Copying from .env.example..."; \
		cp .env.example .env.test; \
	fi
	@# Start services if not running
	@docker compose ps | grep -q postgres || make dev-up
	@echo ""
	@echo "Running backend e2e tests..."
	@poetry run pytest tests/test_e2e_*.py -v --tb=short
	@echo ""
	@echo "✓ E2E tests completed!"

dev-send-alert: ## Send a test alert to webhook (requires dev-up and dev-serve)
	@PICK=$$(( RANDOM % 3 )); \
	if [ $$PICK -eq 0 ]; then \
		echo "Sending PodCPUThrottling alert..."; \
		curl -s -X POST http://localhost:8080/alerts \
			-H "Content-Type: application/json" \
			-d '{"version":"4","status":"firing","receiver":"webhook","groupLabels":{"alertname":"PodCPUThrottling"},"commonLabels":{"alertname":"PodCPUThrottling","severity":"warning","namespace":"default","pod":"test-pod-12345","cluster":"test-cluster"},"commonAnnotations":{"summary":"CPU throttling detected"},"externalURL":"http://alertmanager:9093","alerts":[{"status":"firing","labels":{"alertname":"PodCPUThrottling","severity":"warning","namespace":"default","pod":"test-pod-12345","container":"app","cluster":"test-cluster"},"annotations":{"summary":"CPU throttling detected"},"startsAt":"2024-01-15T10:00:00Z","endsAt":"0001-01-01T00:00:00Z","generatorURL":"http://prometheus:9090/graph","fingerprint":"fp-throttle-'"$$(date +%s)"'"}]}' \
			| python -m json.tool; \
		echo ""; \
		echo "✓ PodCPUThrottling alert sent!"; \
	elif [ $$PICK -eq 1 ]; then \
		echo "Sending KubePodCrashLooping alert..."; \
		curl -s -X POST http://localhost:8080/alerts \
			-H "Content-Type: application/json" \
			-d '{"version":"4","status":"firing","receiver":"webhook","groupLabels":{"alertname":"KubePodCrashLooping"},"commonLabels":{"alertname":"KubePodCrashLooping","severity":"warning","namespace":"default","pod":"my-app-7b4f5c6d8f-xk9m2","container":"my-app","cluster":"prod-cluster"},"commonAnnotations":{"summary":"Pod is crash looping","description":"Pod default/my-app-7b4f5c6d8f-xk9m2 (my-app) is in waiting state (reason: CrashLoopBackOff)"},"externalURL":"http://alertmanager:9093","alerts":[{"status":"firing","labels":{"alertname":"KubePodCrashLooping","severity":"warning","namespace":"default","pod":"my-app-7b4f5c6d8f-xk9m2","container":"my-app","job":"kube-state-metrics","cluster":"prod-cluster"},"annotations":{"summary":"Pod is crash looping","description":"Pod default/my-app-7b4f5c6d8f-xk9m2 (my-app) is in waiting state (reason: CrashLoopBackOff)"},"startsAt":"2026-02-24T10:05:00Z","endsAt":"0001-01-01T00:00:00Z","generatorURL":"http://prometheus:9090/graph?g0.expr=rate(kube_pod_container_status_restarts_total[10m])*60*5>0","fingerprint":"fp-crashloop-'"$$(date +%s)"'"}]}' \
			| python -m json.tool; \
		echo ""; \
		echo "✓ KubePodCrashLooping alert sent!"; \
	else \
		echo "Sending KubeJobFailed alert..."; \
		curl -s -X POST http://localhost:8080/alerts \
			-H "Content-Type: application/json" \
			-d '{"version":"4","status":"firing","receiver":"webhook","groupLabels":{"alertname":"KubeJobFailed"},"commonLabels":{"alertname":"KubeJobFailed","severity":"warning","namespace":"default","job_name":"data-import-daily","cluster":"prod-cluster"},"commonAnnotations":{"summary":"Job data-import-daily has failed"},"externalURL":"http://alertmanager:9093","alerts":[{"status":"firing","labels":{"alertname":"KubeJobFailed","severity":"warning","namespace":"default","job_name":"data-import-daily","job":"kube-state-metrics","cluster":"prod-cluster"},"annotations":{"summary":"Job data-import-daily has failed"},"startsAt":"2026-02-24T10:05:00Z","endsAt":"0001-01-01T00:00:00Z","generatorURL":"http://prometheus:9090/graph","fingerprint":"fp-jobfailed-'"$$(date +%s)"'"}]}' \
			| python -m json.tool; \
		echo ""; \
		echo "✓ KubeJobFailed alert sent!"; \
	fi

dev-clean: dev-down clean ## Stop dev environment and clean all artifacts
	@echo "✓ Development environment cleaned"
