## Enrichment

**Summary:** suspected_image_pull_backoff

### Why

- Pod status: phase=Pending | ready=False | reason=Unschedulable | message=0/3 nodes are available: Insufficient cpu.
- Container waiting: app reason=ImagePullBackOff message=pull failed
- Event: type=Warning | reason=FailedScheduling | count=12 | msg=0/3 nodes are available: Insufficient cpu.

### On-call next

- `max by (cluster, namespace, pod, phase) (kube_pod_status_phase{namespace="ns1",pod="p1"})`
- `max by (cluster, namespace, pod, condition) (kube_pod_status_ready{namespace="ns1",pod="p1"})`
- `increase(kube_pod_container_status_restarts_total{namespace="ns1",pod="p1"}[30m])`
- `max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{namespace="ns1",pod="p1"})`
- `kubectl -n ns1 describe pod p1`
