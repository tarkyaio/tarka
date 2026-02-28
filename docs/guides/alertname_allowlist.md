# ALERTNAME_ALLOWLIST Decision Framework for New Organization

## Context

You're deploying Tarka in a new organization with a different set of alerts than the default configuration. You need to understand:
1. What alerts the agent can effectively investigate
2. Which alerts have specialized playbooks vs baseline coverage
3. How to build an appropriate ALERTNAME_ALLOWLIST for your organization's alert portfolio

The agent has **3 tiers of investigation capability**:
- **Tier 1**: 6 specialized playbooks with deep, tailored investigation (CPU throttling, OOM, HTTP 5xx, pod health, memory pressure)
- **Tier 2**: Baseline pod/non-pod investigation (works for ANY alert with proper labels)
- **Tier 3**: Universal diagnostic modules (run after evidence collection to detect failure modes)

**Key insight**: The agent can investigate **any** alert (even unrecognized ones), but ALERTNAME_ALLOWLIST controls which alerts consume resources and generate reports.

---

## Understanding Agent Capabilities

### Specialized Playbooks (Highest Value)

These alerts have dedicated playbooks with deep investigation logic:

| Alert Names | Playbook | What It Investigates |
|-------------|----------|---------------------|
| CPUThrottlingHigh, KubePodCPUThrottling, CPUThrottling, ContainerCpuThrottled | `cpu_throttling` | Throttle %, CPU usage/limits, per-container breakdown |
| KubernetesContainerOomKiller | `oom_killer` | Memory usage/limits, OOM kill count, exit codes |
| KubernetesPodNotHealthy, KubernetesPodNotHealthyCritical | `pod_not_healthy` | K8s conditions, events, ImagePull diagnostics, CrashLoops |
| Http5xxRateHigh, Http5xxRateWarning | `http_5xx` | 5xx rates by status code, request volumes |
| MemoryPressure | `memory_pressure` | Memory usage trends, proximity to limits |

**Files**: `agent/playbooks/*.py`

### Baseline Coverage (Works for Any Alert)

**Pod baseline** (requires `namespace` + `pod` labels):
- K8s pod info, conditions, events
- Owner chain (ReplicaSet → Deployment)
- Recent logs (best-effort)
- CPU/memory usage and limits
- Restart signals

**Non-pod baseline** (service/cluster/node alerts):
- Prometheus scope and blast radius
- Workload inference from labels
- Rollout status (if identifiable)
- Change correlation

**Files**:
- `agent/playbooks/baseline_pod.py`
- `agent/playbooks/baseline_nonpod.py`

### Alert Requirements

**Essential for pod-scoped alerts**: `namespace` and `pod` labels (without these, investigation is limited)

**Helpful but optional**: `container`, `cluster`, `team`, `environment`, `service`, `instance`

---

## Decision Framework

### Step 1: Audit Your Current Alerts

Extract all unique firing alert names:

```bash
# From Alertmanager
curl -s http://alertmanager:9093/api/v2/alerts | \
  jq -r '.[] | select(.status.state == "firing") | .labels.alertname' | \
  sort | uniq -c | sort -rn

# From Prometheus
curl -s 'http://prometheus:9090/api/v1/query?query=count%20by%20(alertname)%20(ALERTS{alertstate=%22firing%22})' | \
  jq -r '.data.result[] | "\(.value[1]) \(.metric.alertname)"'
```

### Step 2: Classify Alerts by Capability Tier

Create a spreadsheet with these columns:

| Alert Name | Fires/Month | Has namespace+pod | Matches Specialized Playbook | Tier | Include? |
|------------|-------------|-------------------|------------------------------|------|----------|
| CPUThrottlingHigh | 45 | Yes | cpu_throttling | 1 | YES |
| PodCrashLooping | 30 | Yes | baseline_pod | 2 | LATER |
| ServiceDown | 8 | Partial | baseline_nonpod | 2 | MAYBE |
| Watchdog | Always | No | N/A | Exclude | NO |

**Mapping guide**:
- Check if alert name matches specialized playbooks: `agent/playbooks/__init__.py` (lines 55-66)
- Verify labels: `kubectl get prometheusrules -A -o yaml | grep -A 20 "alert: YourAlert"`

### Step 3: Validate Label Quality

For high-priority alerts, verify they have required labels:

```promql
# Check what labels are available
label_names(ALERTS{alertname="YourAlert"})

# Verify namespace+pod are present
count by (has_required_labels) (
  label_replace(
    ALERTS{alertname="YourAlert"},
    "has_required_labels",
    "yes",
    "namespace|pod",
    ".+"
  )
)
```

If labels are missing, **fix the alert rule** before adding to allowlist.

---

## Recommended Allowlist Strategy

### Strategy A: Conservative Rollout (Recommended)

**Phase 1 - Week 1-2: Specialized Playbooks Only**

```bash
ALERTNAME_ALLOWLIST="CPUThrottlingHigh,KubePodCPUThrottling,ContainerCpuThrottled,KubernetesContainerOomKiller,Http5xxRateHigh,Http5xxRateWarning,KubernetesPodNotHealthy,KubernetesPodNotHealthyCritical,MemoryPressure"
```

**Success criteria**:
- Reports provide value 80%+ of the time
- On-call uses reports during 50%+ of incidents
- No significant false positives

**Phase 2 - Week 3-4: Add High-Value Baseline Alerts**

Add pod-scoped alerts that fire during real incidents:
```bash
ALERTNAME_ALLOWLIST="(phase 1 alerts),PodCrashLooping,ContainerRestartingFrequently,DeploymentReplicasUnavailable"
```

**Phase 3 - Month 2+: Expand Based on Learnings**

Monitor filtered alerts and add those that fired during actual incidents.

### Strategy B: Permissive (For Mature Alert Portfolios)

If your alerts have high quality and consistent labels:

```bash
ALERTNAME_ALLOWLIST=""  # No filtering - investigate all alerts
```

**When to use**: Mature alerting with low noise, complete labels, budget for higher S3/compute costs

### Strategy C: Custom by Team

**Platform Team (K8s operators)**:
```bash
ALERTNAME_ALLOWLIST="KubernetesPodNotHealthy,KubernetesPodNotHealthyCritical,DeploymentReplicasUnavailable,StatefulSetReplicasNotReady,KubernetesContainerOomKiller"
```

**Application Team (HTTP services)**:
```bash
ALERTNAME_ALLOWLIST="Http5xxRateHigh,Http5xxRateWarning,HighRequestLatency,CPUThrottlingHigh,MemoryPressure,KubernetesContainerOomKiller"
```

---

## Monitoring and Iteration

### Track Allowlist Effectiveness

**Webhook stats** (check logs):
```bash
kubectl logs -n tarka deploy/tarka-webhook | grep "stats"
```

Example output:
```json
{
  "received": 100,
  "enqueued": 60,
  "skipped_allowlist": 15  // Alerts filtered by allowlist
}
```

**Key metrics**:
- **Filter rate**: `skipped_allowlist / received`
  - Too high (>50%): Missing potentially valuable alerts
  - Too low (<5%): Consider tightening allowlist
- **Report reuse rate**: How often on-calls open reports
- **Time to triage**: Compare before/after agent deployment

### View Filtered Alerts

```bash
# See what's being filtered
kubectl logs -n tarka deploy/tarka-webhook | grep "skipped_allowlist" | tail -20
```

### Expansion Decision Tree

Every 2 weeks, review filtered alerts:

```
Alert fired during real incident?
├─ No → Keep excluded
└─ Yes → Has namespace+pod labels?
    ├─ No → Fix alert rule labels first, then add
    └─ Yes → Matches specialized playbook?
        ├─ Yes → Add immediately (high value)
        └─ No → Fires often (>5/month)?
            ├─ Yes → Add to allowlist
            └─ No → Add only if critical
```

---

## Common Issues and Solutions

### Issue 1: Alert Fires But No Report Generated

**Cause**: Alert name not in allowlist (case-sensitive exact match required)

**Solution**:
```bash
# Verify exact match
echo $ALERTNAME_ALLOWLIST | tr ',' '\n' | grep "^YourAlert$"

# Test locally (bypasses allowlist)
poetry run python main.py --list-alerts
poetry run python main.py --alert 0
```

### Issue 2: Reports Show "Blocked Scenario A: Target Identity Missing"

**Cause**: Alert missing `namespace` or `pod` labels

**Solution**: Fix alert rule to include labels:
```yaml
- alert: YourAlert
  expr: your_metric > threshold
  labels:
    namespace: "{{ $labels.namespace }}"
    pod: "{{ $labels.pod }}"
```

### Issue 3: Too Many Low-Value Reports

**Cause**: Allowlist includes noisy alerts

**Solution**:
1. Identify noisy alerts from webhook stats
2. Remove from allowlist
3. Fix upstream alerting rules (adjust thresholds)

### Issue 4: Missing Coverage for Critical Incidents

**Cause**: Important alerts not in allowlist

**Solution**:
1. Review incident timeline: What alerts fired?
2. Check if they're in allowlist
3. Add missing alerts to next deployment

---

## Alert Quality Criteria

### Good Candidates for Allowlist

✅ Matches specialized playbook (Tier 1)
✅ Has complete labels (namespace, pod, container)
✅ Fires during real incidents requiring investigation
✅ Currently involves manual context gathering
✅ Not auto-remediated

### Poor Candidates (Exclude)

❌ Meta-alerts (Watchdog, InfoInhibitor)
❌ Missing stable labels
❌ Duplicate coverage (3 alerts for same failure)
❌ Auto-remediated before human intervention
❌ Test/synthetic alerts

---

## Quick Reference

### Default Allowlist (deploy.sh line 47)
```
CPUThrottlingHigh,KubePodCPUThrottling,CPUThrottling,ContainerCpuThrottled,KubernetesContainerOomKiller,Http5xxRateHigh,Http5xxRateWarning,KubernetesPodNotHealthy,KubernetesPodNotHealthyCritical,RowsRejectedOnIngestion,MemoryPressure
```

### How Allowlist Works

**File**: `agent/api/webhook.py` (lines 128-132, 274-277)

- **Empty string** (`""`) = No filtering, all alerts processed
- **Set** = Only exact matches processed (case-sensitive, no wildcards)
- **Applied**: Early in webhook ingestion (before JetStream enqueue)
- **Impact**: Filtered alerts never investigated, no reports generated

### Alert Routing Logic

**File**: `agent/pipeline/pipeline.py` (lines 233-235)

1. Check for specialized playbook match (exact name)
2. Pattern matching (substring in alertname)
3. Fallback:
   - Pod-scoped alerts → `baseline_pod`
   - Non-pod alerts → `baseline_nonpod`

---

## Implementation Steps

1. **Audit current alerts** (Step 1 above)
2. **Create classification spreadsheet** (Step 2 above)
3. **Start with conservative allowlist** (Strategy A, Phase 1)
4. **Deploy and monitor** for 1-2 weeks
5. **Review filtered alerts** and webhook stats
6. **Expand allowlist** based on learnings (Phase 2)
7. **Iterate** every 2 weeks using expansion decision tree

---

## Critical Files Reference

- **Allowlist parsing**: `agent/api/webhook.py` (lines 128-132)
- **Filtering logic**: `agent/api/webhook.py` (lines 274-277)
- **Playbook registry**: `agent/playbooks/__init__.py` (lines 55-66)
- **Family detection**: `agent/pipeline/families.py`
- **Default allowlist**: `deploy.sh` (line 47)
- **ConfigMap**: `k8s/configmap.yaml` (line 23)

---

## Verification

After implementing your allowlist:

1. **Check webhook is receiving alerts**:
   ```bash
   kubectl logs -n tarka deploy/tarka-webhook | grep "received"
   ```

2. **Verify filtering is working**:
   ```bash
   kubectl logs -n tarka deploy/tarka-webhook | grep "skipped_allowlist"
   ```

3. **Test investigation locally** (bypasses allowlist):
   ```bash
   poetry run python main.py --list-alerts
   poetry run python main.py --alert 0  # Investigate first alert
   ```

4. **Monitor report generation**:
   ```bash
   aws s3 ls s3://your-bucket/tarka/reports/ --recursive | tail -20
   ```

5. **Check worker is processing**:
   ```bash
   kubectl logs -n tarka deploy/tarka-worker | grep "Investigation complete"
   ```
