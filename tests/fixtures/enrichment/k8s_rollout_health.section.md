## Enrichment

**Summary:** suspected_rollout_stuck

### Why

- Workload: Deployment/api
- Rollout: Deployment/api ready=2/5 updated=2 unavailable=3
- Condition: Progressing status=False reason=ProgressDeadlineExceeded message=ReplicaSet "api-abc" has timed out progressing.

### On-call next

- `kube_deployment_status_replicas{namespace="ns1",deployment="api"}`
- `kube_deployment_status_replicas_available{namespace="ns1",deployment="api"}`
- `kube_deployment_status_replicas_unavailable{namespace="ns1",deployment="api"}`
- `kube_deployment_status_observed_generation{namespace="ns1",deployment="api"}`
- `kubectl -n ns1 rollout status deployment/api`
