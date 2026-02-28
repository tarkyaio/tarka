## Enrichment

**Summary:** suspected_rollout_regression

### Why

- HTTP metric query used: sum(rate(http_requests_total{status=~"5.."}[5m]))
- HTTP 5xx rate: p95=0.5 max=1.0
- Series returned: 1
- Recent change detected within window (possible rollout regression).

### On-call next

- `sum(rate(http_requests_total{status=~"5.."}[5m]))`
- `topk(10, sum by (namespace, service) (rate(http_requests_total{status=~"5.."}[5m])))`
- `topk(10, sum by (namespace, service) (rate(http_server_requests_seconds_count{status=~"5.."}[5m])))`
- `kubectl -n ns1 describe pod p1`
