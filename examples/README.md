# Examples

Sample outputs from Tarka investigations. Use these to understand what the agent produces without running the full stack.

## Reports (`reports/`)

Pre-crafted investigation outputs based on realistic alert scenarios:

| Scenario | Alert | Report | JSON |
|----------|-------|--------|------|
| Pod CrashLoopBackOff | `KubePodCrashLooping` | [report.md](reports/pod-crashloop/report.md) | [investigation.json](reports/pod-crashloop/investigation.json) |

Each report pair includes:
- **report.md** — the rendered markdown report (what on-call engineers see)
- **investigation.json** — the structured JSON analysis (what integrations consume)

## Screenshots (`screenshots/`)

UI captures from the web console. See [screenshots/README.md](screenshots/README.md) for instructions on capturing your own.

## Generate your own

Run a local investigation against your Prometheus/Alertmanager:

```bash
# List firing alerts
poetry run python main.py --list-alerts

# Investigate a specific alert (by index)
poetry run python main.py --alert 0

# Dump structured JSON
poetry run python main.py --alert 0 --dump-json analysis
```

Or send a test alert to the local webhook stack:

```bash
make dev-up && make dev-serve
make dev-send-alert
```
