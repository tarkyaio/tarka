## Enrichment

**Summary:** suspected_cpu_limit_too_low

### Why

- CPU throttling p95: 30.0%
- Top throttled container (inferred): app
- Top container throttling p95: 30.0%
- CPU usage p95 cores: 0.800
- CPU limit cores: 1.000
- CPU near limit: yes (p95 >= 80% of limit)

### On-call next

- `100 * sum by(container,pod,namespace) (increase(container_cpu_cfs_throttled_periods_total{namespace="ns1",pod="p1",container="app",image!=""}[5m])) / clamp_min(sum by(container,pod,namespace) (increase(container_cpu_cfs_periods_total{namespace="ns1",pod="p1",container="app",image!=""}[5m])), 1)`
- `sum by(container,pod,namespace) (rate(container_cpu_usage_seconds_total{namespace="ns1",pod="p1",container="app",image!=""}[5m]))`
- `max by(container,pod,namespace) (kube_pod_container_resource_limits{namespace="ns1",pod="p1",container="app",resource="cpu"})`
- `kubectl -n ns1 describe pod p1`
