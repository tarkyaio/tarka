## Enrichment

**Summary:** suspected_container_near_limit

### Why

- Pod status: phase=Running | reason=Pressure
- Memory p95 bytes: 900
- Memory limit bytes: 1000
- Memory near limit: yes (p95 >= 90% of limit)
- Restart spike: restart_rate_5m_max=0.00
- Warnings queried: 0

### On-call next

- `quantile_over_time(0.95, sum by (namespace, pod, container) (container_memory_working_set_bytes{namespace="ns1",pod="p1",container="app",container!="POD",image!=""})[30m])`
- `max by (namespace, pod, container) (kube_pod_container_resource_limits{namespace="ns1",pod="p1",container="app",resource="memory"})`
- `increase(kube_pod_container_status_restarts_total{namespace="ns1",pod="p1",container="app"}[30m])`
- `kubectl -n ns1 describe pod p1`
