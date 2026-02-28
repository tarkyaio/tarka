# Quickstart Guide

Get your first investigation running in 5 minutes.

## Prerequisites

- Python 3.10+
- Access to a Prometheus-compatible endpoint
- (Optional) Kubernetes cluster access
- (Optional) VictoriaLogs endpoint

## Installation

```bash
# Clone the repository
git clone https://github.com/your-org/tarka.git
cd tarka

# Install Poetry if not already installed
pip install poetry

# Install dependencies
poetry install
```

## Configuration

Set required environment variables:

```bash
# Required: Prometheus endpoint
export PROMETHEUS_URL="http://localhost:18481/select/0/prometheus"

# Optional: Alertmanager endpoint
export ALERTMANAGER_URL="http://localhost:9093"

# Optional: Kubernetes context (uses kubeconfig)
# Automatic if running in-cluster

# Optional: Logs backend
export LOGS_URL="http://localhost:19471"
```

## Your First Investigation

### List Active Alerts

```bash
python main.py --list-alerts
```

This shows all firing alerts from Alertmanager with:
- Index number (for investigation)
- Alert name
- Labels (pod, namespace, severity, etc.)
- State (firing)
- Fingerprint

### Investigate by Index

```bash
python main.py --alert 0
```

This runs the investigation pipeline and produces a Markdown report.

### Investigate by Fingerprint

```bash
python main.py --fingerprint abc123def456
```

Use the fingerprint from `--list-alerts` output.

### Specify Time Window

```bash
python main.py --alert 0 --time-window 2h
```

Supported formats: `1h`, `30m`, `2h30m`, etc.

## Understanding the Output

Every investigation report has three core sections:

### 1. Label (One-liner)
```
scope=pod impact=unavailable discriminator=CrashLoopBackOff (OOMKilled)
```
- **scope**: What's affected (pod, workload, service, node, cluster)
- **impact**: What's broken (unavailable, degraded, throttled, etc.)
- **discriminator**: Key evidence differentiator

### 2. Why (Evidence-backed bullets)
```
- Pod my-app-7d5f6b8c9-xyz in namespace prod is in CrashLoopBackOff
- Container main was OOMKilled 15 times in the last 1h
- Memory limit is 512Mi; usage spiked to 490Mi before kill
- Last exit code: 137 (SIGKILL from OOM killer)
```

6-10 bullets explaining what's wrong. If evidence is missing, the report says "unknown" and shows how to find it.

### 3. Next (Copy/paste actions)
```
# Check current pod status
kubectl get pod my-app-7d5f6b8c9-xyz -n prod -o yaml

# View recent logs
kubectl logs my-app-7d5f6b8c9-xyz -n prod --tail=100

# Check memory usage over time
promql: rate(container_memory_usage_bytes{pod="my-app-7d5f6b8c9-xyz"}[5m])
```

3-7 commands you can copy/paste (PromQL first, then kubectl).

## Blocked Scenarios

When evidence is unavailable, the agent follows explicit scenarios:

- **Scenario A**: Target identity missing (can't determine which pod/workload is affected)
- **Scenario B**: Kubernetes context unavailable (no pod info from K8s API)
- **Scenario C**: Logs missing (log backend unavailable or no logs found)
- **Scenario D**: Prometheus scope unavailable (can't compute scope/blast radius)

The report explicitly states what's unknown and how to unblock.

## Next Steps

- **Deploy to Kubernetes**: See [deployment.md](deployment.md)
- **Understand triage philosophy**: See [../acceptance/triage-methodology.md](../acceptance/triage-methodology.md)
- **Add custom playbooks**: See [extending-playbooks.md](extending-playbooks.md)
- **Enable LLM enrichment**: Set `LLM_ENABLED=true` and configure Vertex AI credentials
