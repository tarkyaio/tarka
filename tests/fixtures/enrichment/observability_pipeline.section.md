## Enrichment

**Summary:** suspected_alerting_rules_error

### Why

- Alert: AlertingRulesError
- Component labels: job=vmalert instance=vmalert-0

### On-call next

- `up{job="vmalert",instance="vmalert-0"}`
- `topk(20, count by (alertname) (ALERTS{alertstate="firing"}))`
- If many observability-related alerts are firing at once, treat as a platform incident and verify the metrics/logs pipeline health with the observability on-call.
- Check the affected component logs (vmalert/prometheus/agent) for rule evaluation or ingestion errors.
