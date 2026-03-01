# Incident Report: KubePodCrashLooping

**Alert:** `KubePodCrashLooping`
**Severity:** `warning`
**Target type:** `pod`
**Namespace:** `default`
**Pod:** `my-app-7b4f5c6d8f-xk9m2`
**Container:** `my-app`
**Metric source (scrape metadata):** `job=kube-state-metrics`
**Time Window:** `30m`
**Alert state:** `firing`
**Alert starts_at:** `2026-02-24T10:05:00Z`
**Generated:** 2026-02-24 10:38:12

## Triage

**Summary:** suspected_crashloop_or_probe_failure

### Why

- Container `my-app` in waiting state with reason=CrashLoopBackOff
- Last terminated: exit_code=1 (reason=Error) — application crash, not OOM
- Restart rate (5m max) elevated: 3.20 restarts/5m
- Pod phase=Running but ready=False — container keeps restarting
- 4 Warning events observed in time window (BackOff, Unhealthy)

### To unblock

- Check container logs for the root error leading to exit code 1:
```bash
kubectl -n default logs my-app-7b4f5c6d8f-xk9m2 -c my-app --previous --tail=50
```
- Check pod events for scheduling or image issues:
```bash
kubectl -n default describe pod my-app-7b4f5c6d8f-xk9m2
```
- If the crash is caused by a bad config or missing dependency, roll back the deployment:
```bash
kubectl -n default rollout undo deployment/my-app
```

## Enrichment

**Summary:** suspected_crashloop_exit1

### Why

- Container `my-app` terminated with exit_code=1 (reason=Error) — non-zero, non-OOM exit
- CrashLoopBackOff back-off timer active (container waiting)
- Restart count: 7 in pod lifetime; rate 3.20/5m in window
- No probe-failure events detected — crash is from the application itself, not a health check
- Logs show application error: `RuntimeError: failed to connect to database at db.default.svc:5432`

### On-call next

- `max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_waiting_reason{namespace="default",pod="my-app-7b4f5c6d8f-xk9m2"})`
- `increase(kube_pod_container_status_restarts_total{namespace="default",pod="my-app-7b4f5c6d8f-xk9m2"}[30m])`
- `max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{namespace="default",pod="my-app-7b4f5c6d8f-xk9m2"})`
- Check whether the database service is reachable from the pod network
- If a recent deployment introduced the regression, check rollout history

## Likely causes (ranked)

### Application crash on startup — database unreachable (82/100)

- Container exits with code 1 immediately after start (Error, not OOM)
- Logs contain: `RuntimeError: failed to connect to database at db.default.svc:5432`
- No readiness/liveness probe failures in events — the process itself exits
- Restart back-off pattern consistent with fast crash loop (crash → backoff → restart → crash)

**Next tests:**

- Verify database connectivity from the pod's namespace:
```bash
kubectl -n default run dbcheck --rm -it --image=busybox -- nc -zv db.default.svc 5432
```
- Check if the database pod/service exists and is ready:
```bash
kubectl -n default get endpoints db
```
- Review recent changes to database configuration or secrets:
```bash
kubectl -n default get secret my-app-db-credentials -o yaml
```

### Bad deployment / config regression (45/100)

- No rollout change detected in the investigation window (change correlation inconclusive)
- Possible: a config change (ConfigMap, Secret) was applied without a rollout restart
- Exit code 1 can indicate misconfiguration (missing env var, bad connection string)

**Next tests:**

- Check deployment rollout history for recent changes:
```bash
kubectl -n default rollout history deployment/my-app
```
- Compare current ConfigMap/Secret values against the last known-good version

## Verdict

**Classification:** `actionable`
**Primary driver:** `CrashLoopBackOff`

Pod `my-app-7b4f5c6d8f-xk9m2` in namespace `default` is crash-looping (exit_code=1). Logs point to a database connectivity failure (`db.default.svc:5432`). The container crashes on startup before becoming ready.
- **Alert age:** ~0.6h

## Scores

- **Impact:** 72/100
- **Confidence:** 78/100
- **Noise:** 15/100

## Reason codes

- `restart_rate_elevated`
- `waiting_crashloopbackoff`
- `exit_code_nonzero`
- `pod_not_ready`
- `logs_error_pattern`
- `no_probe_failure`

## On-call next steps

- Check previous container logs for the crash traceback:
```bash
kubectl -n default logs my-app-7b4f5c6d8f-xk9m2 -c my-app --previous --tail=100
```
- Verify database service is healthy:
```bash
kubectl -n default get endpoints db
```
- If the database is down, escalate to the database on-call team
- If the database is healthy but the app cannot reach it, check NetworkPolicy and DNS resolution
- If a recent deployment caused the regression, roll back:
```bash
kubectl -n default rollout undo deployment/my-app
```

## Appendix: Evidence

### Derived features

- **Family:** `crashloop`
- **Evidence quality:** `high`
- **Alert age (hours):** 0.6
- **is_long_running:** False
- **is_recently_started:** True
- **impact_signals_available:** True

### Kubernetes

- **Phase:** Running
- **Node:** ip-10-0-1-42.ec2.internal
- **Pod status:** CrashLoopBackOff — Back-off restarting failed container
- **Not-ready conditions:**
  - Ready=False (reason=ContainersNotReady)
  - ContainersReady=False (reason=ContainersNotReady)
- **Container waiting:**
  - my-app: CrashLoopBackOff — back-off 5m0s restarting failed container my-app
- **Container last terminated:**
  - my-app: Error, exitCode=1
- **Top events:**
  - BackOff x12 (Warning): Back-off restarting failed container my-app
  - Unhealthy x4 (Warning): Readiness probe failed: connection refused
  - Pulled x8 (Normal): Successfully pulled image "registry.example.com/my-app:v1.2.3"
  - Started x8 (Normal): Started container my-app

### Metrics

- **restart_rate_5m_max:** 3.20

### Logs

- **Status:** `ok`
- **Backend:** `victorialogs`
- **Selector:** `{namespace="default",pod="my-app-7b4f5c6d8f-xk9m2"}`
- **Entries:** 37
- **Shown:** 8 (prioritized errors; otherwise tail)

```
2026-02-24T10:34:02Z INFO  my-app Starting application v1.2.3
2026-02-24T10:34:02Z INFO  my-app Loading configuration from /etc/config/app.yaml
2026-02-24T10:34:03Z INFO  my-app Connecting to database...
2026-02-24T10:34:08Z ERROR my-app Failed to connect to database: connection refused
2026-02-24T10:34:08Z ERROR my-app   host=db.default.svc port=5432 timeout=5s
2026-02-24T10:34:08Z FATAL my-app RuntimeError: failed to connect to database at db.default.svc:5432
2026-02-24T10:34:08Z FATAL my-app Shutting down due to unrecoverable error
2026-02-24T10:34:08Z INFO  my-app Process exiting with code 1
```
