# Alert Family Specifications

This document defines enrichment rules for each alert family. These specs ensure consistent, deterministic triage across all investigations.

## Table of Contents

1. [Meta](#meta)
2. [Pod Not Healthy](#pod-not-healthy)
3. [CPU Throttling](#cpu-throttling)
4. [OOM Killed](#oom-killed)
5. [HTTP 5xx](#http-5xx)
6. [Memory Pressure](#memory-pressure)
7. [Target Down](#target-down)
8. [K8s Rollout Health](#k8s-rollout-health)
9. [Observability Pipeline](#observability-pipeline)

---

## Meta

**Family**: `meta`

**Description**: Meta-alerts or inhibitor alerts that don't represent real incidents but control alerting behavior.

**Canonical Label Format**:
```
scope=meta impact=informational discriminator=<alert_type>
```

**Enrichment Rules**:
- Label as informational
- Don't run diagnostics (no real failure)
- Include alert purpose in why bullets
- Next steps: Verify inhibitor rules are working as intended

**Example Alerts**:
- Watchdog (always-firing heartbeat)
- Inhibitor rules
- Alertmanager health checks

---

## Pod Not Healthy

**Family**: `pod_not_healthy`

**Alert Names**: `KubernetesPodNotHealthy*`, `PodNotReady`, `PodCrashLooping`

**Description**: Pod lifecycle and health issues - covers CrashLoopBackOff, ImagePullBackOff, OOMKilled, termination, not ready, etc.

**Canonical Label Format**:
```
scope=pod impact=unavailable discriminator=<K8s_reason>
```

**Discriminators** (priority order):
1. `CrashLoopBackOff (OOMKilled)` - Container OOM killed repeatedly
2. `CrashLoopBackOff (Error)` - Container exiting with error code
3. `ImagePullBackOff` - Cannot pull container image
4. `ImagePullBackOff (ImageNotFound)` - Image doesn't exist
5. `ImagePullBackOff (RegistryUnavailable)` - Registry unreachable
6. `CreateContainerConfigError` - Bad ConfigMap/Secret reference
7. `Pending (Unschedulable)` - Cannot schedule to node
8. `NotReady` - Readiness probe failing
9. `Terminating (stuck)` - Pod stuck in Terminating state

**Evidence Requirements**:
- K8s pod phase (`Running`, `Pending`, `Failed`, `Unknown`)
- Container waiting reason (if applicable)
- Container last terminated reason + exit code
- Restart count
- Recent events (last 10)
- OOM kill detection from container status

**Next Steps** (priority order):
1. If OOMKilled: Check memory usage vs limit
   ```promql
   container_memory_usage_bytes{pod="<pod>"} / container_spec_memory_limit_bytes{pod="<pod>"}
   ```
2. If ImagePull error: Verify image exists and registry access
3. If CrashLoop: Check logs for exit reason
   ```bash
   kubectl logs <pod> -n <namespace> --previous
   ```
4. If ConfigError: Verify ConfigMap/Secret exists
   ```bash
   kubectl get configmap,secret -n <namespace>
   ```

**Workload Freshness Gate**: 1 hour (reduces rollout noise)

---

## CPU Throttling

**Family**: `cpu_throttling`

**Alert Names**: `CPUThrottlingHigh`, `KubernetesCPUThrottling`

**Description**: Container CPU usage hitting CPU limit, causing throttling and performance degradation.

**Canonical Label Format**:
```
scope=<pod|workload> impact=degraded discriminator=cpu_throttling_<severity>
```

**Discriminators**:
1. `cpu_throttling_critical` - >50% throttled time
2. `cpu_throttling_high` - 25-50% throttled time
3. `cpu_throttling_moderate` - 10-25% throttled time

**Evidence Requirements**:
- CPU throttle percentage (from `container_cpu_cfs_throttled_seconds_total`)
- CPU limit (from `container_spec_cpu_quota`)
- CPU usage (from `container_cpu_usage_seconds_total`)
- Top throttled containers (if scope > 1)

**Next Steps**:
1. Check throttle rate over time
   ```promql
   rate(container_cpu_cfs_throttled_seconds_total{pod="<pod>"}[5m])
   ```
2. Check CPU usage vs limit
   ```promql
   rate(container_cpu_usage_seconds_total{pod="<pod>"}[5m]) / container_spec_cpu_quota{pod="<pod>"} * 100000
   ```
3. If consistently throttled: Consider increasing CPU limit
   ```bash
   kubectl set resources deployment/<deployment> --limits=cpu=2000m
   ```

---

## OOM Killed

**Family**: `oom_killed`

**Alert Names**: `KubernetesContainerOomKiller`, `ContainerOOMKilled`

**Description**: Container killed by Linux OOM killer due to memory limit exceeded.

**Canonical Label Format**:
```
scope=<pod|workload> impact=unavailable discriminator=oom_killed_<frequency>
```

**Discriminators**:
1. `oom_killed_recurring` - Multiple OOM kills (>3 in window)
2. `oom_killed_recent` - Single OOM kill in last 10 minutes
3. `oom_killed_historical` - OOM kill older than 10 minutes

**Evidence Requirements**:
- OOM kill count in time window
- Memory limit (from `container_spec_memory_limit_bytes`)
- Memory usage before kill (from `container_memory_working_set_bytes`)
- Last exit code (should be 137 = SIGKILL)
- Restart count

**Next Steps**:
1. Check memory usage trend before kill
   ```promql
   container_memory_working_set_bytes{pod="<pod>"} / container_spec_memory_limit_bytes{pod="<pod>"}
   ```
2. Review logs for memory allocation patterns
   ```bash
   kubectl logs <pod> -n <namespace> --previous --tail=50
   ```
3. If memory limit is very low (<512Mi): Increase limit
   ```bash
   kubectl set resources deployment/<deployment> --limits=memory=1Gi
   ```
4. If memory usage is genuinely high: Investigate memory leak

**Workload Freshness Gate**: 1 hour (deduped per workload + container)

---

## HTTP 5xx

**Family**: `http_5xx`

**Alert Names**: `HTTP5xxRateHigh`, `ServiceErrorRate`

**Description**: Elevated HTTP 5xx error rate indicating application errors or backend issues.

**Canonical Label Format**:
```
scope=<service|workload> impact=degraded discriminator=http_5xx_<rate>
```

**Discriminators**:
1. `http_5xx_critical` - >10% error rate
2. `http_5xx_high` - 5-10% error rate
3. `http_5xx_elevated` - 1-5% error rate

**Evidence Requirements**:
- Current 5xx rate (from `http_requests_total{status=~"5.."}`)
- Total request rate
- Top 5xx status codes (500, 502, 503, 504)
- Recent changes (rollout events)

**Next Steps**:
1. Check 5xx rate by status code
   ```promql
   sum by (status) (rate(http_requests_total{status=~"5..", service="<service>"}[5m]))
   ```
2. Check backend dependency health
   ```promql
   up{job="<backend>"}
   ```
3. Review logs for error patterns
   ```bash
   kubectl logs deployment/<deployment> -n <namespace> --tail=100 | grep -i error
   ```
4. If 502/503: Check if backend pods are ready
   ```bash
   kubectl get pods -n <namespace> -l app=<backend>
   ```

---

## Memory Pressure

**Family**: `memory_pressure`

**Alert Names**: `MemoryPressure`, `NodeMemoryPressure`, `PodMemoryUsageHigh`

**Description**: High memory usage approaching limit, may lead to OOMKill if not addressed.

**Canonical Label Format**:
```
scope=<pod|node|workload> impact=risk discriminator=memory_<severity>
```

**Discriminators**:
1. `memory_critical` - >95% of limit
2. `memory_high` - 85-95% of limit
3. `memory_elevated` - 75-85% of limit

**Evidence Requirements**:
- Memory usage (from `container_memory_working_set_bytes`)
- Memory limit
- Memory usage trend (increasing/stable/decreasing)
- Top memory consumers (if node-level)

**Next Steps**:
1. Check memory usage trend
   ```promql
   container_memory_working_set_bytes{pod="<pod>"} / container_spec_memory_limit_bytes{pod="<pod>"}
   ```
2. Check for memory leak indicators
   ```promql
   deriv(container_memory_working_set_bytes{pod="<pod>"}[30m])
   ```
3. If approaching limit: Consider increasing memory limit
4. If sustained high usage: Investigate memory leak or optimize application

---

## Target Down

**Family**: `target_down`

**Alert Names**: `TargetDown`, `ServiceDown`, `EndpointDown`

**Description**: Prometheus cannot scrape metrics from target (service/pod unreachable).

**Canonical Label Format**:
```
scope=<service|instance> impact=unknown discriminator=target_down_<reason>
```

**Discriminators**:
1. `target_down_pods_unavailable` - No healthy pods
2. `target_down_network` - Network unreachable
3. `target_down_metrics_endpoint` - Metrics endpoint not responding
4. `target_down_unknown` - Reason unclear

**Evidence Requirements**:
- Target job and instance labels
- Last scrape error (if available)
- Pod health status (if pod target)
- Recent changes

**Next Steps**:
1. Check if target pods are running
   ```bash
   kubectl get pods -n <namespace> -l job=<job>
   ```
2. Check service/endpoint status
   ```bash
   kubectl get svc,endpoints -n <namespace> <service>
   ```
3. Check Prometheus target status in UI
4. If pods exist but not scraped: Verify metrics port and path

---

## K8s Rollout Health

**Family**: `k8s_rollout_health`

**Alert Names**: `DeploymentReplicasUnavailable`, `StatefulSetReplicasNotReady`

**Description**: Kubernetes rollout stuck or unhealthy (replicas not becoming ready).

**Canonical Label Format**:
```
scope=workload impact=degraded discriminator=rollout_<reason>
```

**Discriminators**:
1. `rollout_stuck_no_progress` - No new pods becoming ready
2. `rollout_image_pull_error` - New pods can't pull image
3. `rollout_crash_loop` - New pods crashing repeatedly
4. `rollout_insufficient_capacity` - Not enough node resources

**Evidence Requirements**:
- Deployment/StatefulSet status (desired vs ready replicas)
- ReplicaSet status and age
- Pod conditions for new pods
- Rollout events

**Next Steps**:
1. Check rollout status
   ```bash
   kubectl rollout status deployment/<deployment> -n <namespace>
   ```
2. Check ReplicaSet status
   ```bash
   kubectl get rs -n <namespace> -l app=<app>
   ```
3. If stuck: Check new pod status
   ```bash
   kubectl get pods -n <namespace> -l app=<app> --sort-by=.metadata.creationTimestamp
   ```
4. If image pull error: Fix image tag or registry access
5. If crash loop: Check logs of new pods

---

## Observability Pipeline

**Family**: `observability_pipeline`

**Alert Names**: `LogIngestionLag`, `MetricScrapeFailing`, `TraceSamplingHigh`

**Description**: Issues with observability infrastructure (logs, metrics, traces ingestion/processing).

**Canonical Label Format**:
```
scope=<component> impact=observability discriminator=<pipeline_issue>
```

**Discriminators**:
1. `log_ingestion_lag` - Logs delayed in reaching backend
2. `metric_scrape_failing` - Prometheus scrape failures
3. `trace_sampling_high` - High trace drop rate
4. `observability_backend_down` - Loki/Victoria/Tempo unreachable

**Evidence Requirements**:
- Backend component status
- Lag/delay metrics
- Error rate from observability components
- Recent changes to observability infrastructure

**Next Steps**:
1. Check backend component health
   ```bash
   kubectl get pods -n observability
   ```
2. Check ingestion rate vs capacity
3. Check for resource limits on observability components
4. If backend down: Restart or scale up components
5. If lag high: Check for burst traffic or increase capacity

---

## General Notes

- All families follow **base triage contract**: label + why + next
- Evidence collection is **best-effort**: partial evidence still produces useful report
- When evidence is missing, report says **"unknown"** and shows how to unblock
- **Blocked scenarios (A-D)** take precedence over family enrichment
- Family enrichment is **additive**: never contradicts base triage
- **PromQL-first** next steps: works without cluster credentials
