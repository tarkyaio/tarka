# Testing Guide

How to run tests for Tarka.

## Test Structure

The test suite is organized with pytest and uses markers to separate unit tests from integration tests:

- **Unit tests**: Fast, use mocked dependencies, no external services required
- **Integration tests**: Require real external services (NATS JetStream), marked with `@pytest.mark.integration`

## Running Tests

### Unit Tests Only (Default)

```bash
# Run all unit tests (excludes integration tests)
pytest -m "not integration"
```

This is the default for local development and CI.

### Integration Tests

```bash
# Run integration tests (requires NATS JetStream)
pytest -m integration
```

**Prerequisites for integration tests:**
- NATS server running locally (`nats-server -js`)
- Port 4222 available

### All Tests

```bash
# Run everything (unit + integration)
pytest
```

### Specific Test File

```bash
# Run a specific test file
pytest tests/test_pipeline.py

# Run a specific test function
pytest tests/test_pipeline.py::test_investigation_pipeline
```

### With Coverage

```bash
# Run with coverage report
pytest --cov=agent --cov-report=term-missing

# Generate HTML coverage report
pytest --cov=agent --cov-report=html
open htmlcov/index.html
```

## Test Fixtures

Test fixtures are located in `tests/fixtures/`:

- **alert_payloads/**: Sample Alertmanager alert payloads (JSON)
- **prometheus_responses/**: Mock Prometheus API responses
- **k8s_manifests/**: Sample Kubernetes object manifests (pods, deployments, etc.)
- **investigations/**: Golden investigation outputs for acceptance tests

## Adding New Tests

### Unit Test Example

```python
import pytest
from agent.core.models import Investigation, AlertInstance
from agent.pipeline.pipeline import run_investigation

def test_cpu_throttling_investigation(mock_prom_client, mock_k8s_client):
    """Test CPU throttling investigation pipeline."""
    alert = AlertInstance(
        labels={"alertname": "CPUThrottlingHigh", "pod": "my-app-123"},
        annotations={"summary": "CPU throttling detected"},
        state="firing"
    )

    investigation = run_investigation(alert=alert, time_window="1h")

    assert investigation.analysis.decision is not None
    assert "throttling" in investigation.analysis.decision.label.lower()
```

### Using Fixtures

```python
@pytest.fixture
def sample_alert():
    """Load a sample alert from fixtures."""
    import json
    with open("tests/fixtures/alert_payloads/cpu_throttling.json") as f:
        return json.load(f)

def test_with_fixture(sample_alert):
    # Test using fixture
    pass
```

### Integration Test Example

```python
import pytest

@pytest.mark.integration
def test_nats_jetstream_worker():
    """Test NATS JetStream worker integration."""
    # Requires NATS server running
    from agent.api.worker_jetstream import run_worker_forever
    # ... test implementation
```

## Test Coverage Expectations

Target coverage by module:

- **agent/pipeline/**: >90% (core investigation logic)
- **agent/playbooks/**: >85% (evidence collection)
- **agent/diagnostics/**: >90% (failure mode detection)
- **agent/core/**: >95% (data models)
- **agent/providers/**: >70% (external service clients)

## Mocking External Services

### Prometheus

```python
from unittest.mock import Mock, patch

@patch('agent.providers.prom_provider.PrometheusClient')
def test_with_mock_prom(mock_prom_class):
    mock_client = Mock()
    mock_client.query_instant.return_value = [
        {"metric": {"pod": "my-app"}, "value": [1234, "0.5"]}
    ]
    mock_prom_class.return_value = mock_client
    # ... test implementation
```

### Kubernetes

```python
@patch('agent.providers.k8s_provider.KubernetesClient')
def test_with_mock_k8s(mock_k8s_class):
    mock_client = Mock()
    mock_client.get_pod.return_value = {
        "metadata": {"name": "my-app-123"},
        "status": {"phase": "Running"}
    }
    mock_k8s_class.return_value = mock_client
    # ... test implementation
```

## Continuous Integration

Tests run automatically on:
- Pull requests (unit tests only)
- Main branch (unit + integration tests)

CI configuration: `.github/workflows/test.yml` (if using GitHub Actions)

## Troubleshooting

### Integration Tests Fail

**Problem**: `connection refused` errors

**Solution**: Ensure NATS server is running:
```bash
# Install NATS server
go install github.com/nats-io/nats-server/v2@latest

# Run with JetStream enabled
nats-server -js
```

### Slow Tests

**Problem**: Tests taking too long

**Solution**: Run unit tests only during development:
```bash
pytest -m "not integration" -x  # Stop on first failure
```

### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'agent'`

**Solution**: Install with Poetry (includes development mode):
```bash
poetry install
```

## Best Practices

1. **Mock external dependencies** in unit tests (Prometheus, K8s, logs)
2. **Use fixtures** for common test data
3. **Test deterministic logic** thoroughly (pipeline, diagnostics, scoring)
4. **Keep tests fast** (<5 seconds for unit tests)
5. **Name tests descriptively**: `test_<feature>_<condition>_<expected_result>`
6. **One assertion focus** per test when possible
