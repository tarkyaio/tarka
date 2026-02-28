# Planned Enhancements

Future work and backlog items for Tarka.

---

## High Priority

### VPA Integration

**Goal**: Automatic resource recommendations based on historical usage patterns.

**Scope**:
- Integrate with Kubernetes Vertical Pod Autoscaler (VPA)
- Use VPA recommendations in capacity analysis
- Include VPA-suggested limits/requests in reports
- Flag pods that would benefit from VPA

**Status**: Design phase

---

### Enhanced Deduplication Logic

**Goal**: More intelligent deduplication to reduce redundant investigations.

**Current State**: S3 HEAD-before-PUT dedupes by `(identity + family + 4h bucket)`

**Improvements Needed**:
- Configurable deduplication window (not just 4h)
- Dedupe by evidence similarity (not just identity)
- Handle label churn better (workload-level dedupe for flapping pods)
- Expose dedupe metrics (skipped count, reason codes)

**Status**: Scoping

---

### S3 HEAD Optimization

**Goal**: Reduce S3 API calls to lower costs and improve performance.

**Current State**: HEAD-before-PUT on every investigation

**Improvements**:
- Local cache of recent S3 keys (in-memory, TTL-based)
- Batch HEAD requests for multiple keys
- Periodic cache refresh via S3 LIST
- Metrics on HEAD hit rate

**Status**: Design phase

---

## Medium Priority

### Leadership Dashboard

**Goal**: Executive-level view of incident trends and resolution metrics.

**Features**:
- Incident count by severity over time
- Mean time to detection (MTTD)
- Mean time to resolution (MTTR)
- Top alert families and affected services
- SLO burn rate vs incident correlation
- Cost of incidents (estimated downtime)

**Status**: Requirements gathering

---

### Scoring Refinements

**Goal**: Improve accuracy and calibration of impact/confidence/noise scores.

**Improvements Needed**:
- Recalibrate confidence when LLM enrichment is used
- Improve noise detection for low-cardinality alerts
- Add "urgency" score (separate from impact)
- Better classification rules (actionable vs informational)
- A/B testing framework for scoring changes

**Status**: Ongoing refinement

---

### Alert Rule Quality Analyzer

**Goal**: Detect and report on poorly configured alert rules.

**Detection Categories**:
- Missing critical labels (namespace, pod, etc.)
- Too broad selectors (high cardinality)
- Flapping alerts (repeated fire/resolve cycles)
- Never-firing alerts (stale rules)
- Redundant alerts (multiple rules for same condition)

**Output**: Periodic report to alert rule owners

**Status**: Design phase

---

### Prometheus Metadata Enrichment

**Goal**: Use Prometheus metadata to improve target identification.

**Features**:
- Query `up` metric for target labels
- Extract job/instance → K8s pod/service mapping
- Enrich target identity when alert labels are insufficient
- Use scrape metadata to detect stale targets

**Status**: Design phase

---

### ArgoCD Integration

**Goal**: Provide deployment context during incident investigations.

**Features**:
- Detect ArgoCD-managed applications from K8s workloads
- Collect recent sync history (deployments, rollbacks)
- Show sync status (synced, out-of-sync, sync-failed)
- Correlate incidents with recent ArgoCD deployments
- Add ArgoCD context to reports (similar to GitHub section)

**Chat Tools**:
- `argocd.app_status` - Get application sync status
- `argocd.recent_syncs` - Query recent deployment history
- `argocd.diff` - Show what changed in last sync

**Discovery Methods**:
1. K8s workload labels: `app.kubernetes.io/instance` (ArgoCD app name)
2. K8s workload annotations: `argocd.argoproj.io/tracking-id`
3. Service catalog mapping
4. Naming convention (if workload name matches ArgoCD app name)

**Example Report Section**:
```markdown
### ArgoCD / Deployments

**Application:** `my-service` (healthy, synced)

**Recent Syncs**:
- ✅ Sync #123: success (15m ago) - Updated image to v1.2.3
- ✅ Sync #122: success (2h ago) - Increased replicas to 5
- ❌ Sync #121: failed (3h ago) - Failed: manifest validation error
```

**Dependencies**:
- ArgoCD API access (requires API token or K8s service account)
- Policy configuration: `CHAT_ALLOW_ARGOCD_READ=1`

**Status**: Planned (Phase 2)

---

## Lower Priority

### Multi-Cluster Support Improvements

**Current State**: Cluster identity from alert labels

**Improvements**:
- Explicit cluster routing configuration
- Per-cluster Prometheus/K8s/logs endpoints
- Cross-cluster correlation (same service, different clusters)
- Cluster-wide impact assessment

**Status**: Backlog

---

### Alert Grouping Improvements

**Goal**: Better grouping of related alerts into single investigation.

**Current State**: One investigation per alert instance

**Improvements**:
- Group by common root cause (e.g., all pods affected by network partition)
- Aggregate evidence across grouped alerts
- Single report for alert group
- Configurable grouping rules

**Status**: Research

---

### Custom Playbook UI

**Goal**: Web UI for defining custom playbooks without code changes.

**Features**:
- Visual playbook builder
- PromQL query templates
- K8s query templates
- Evidence mapping to fields
- Test playbook against historical alerts

**Status**: Idea phase

---

### Action Execution Framework

**Current State**: Actions are suggest-only

**Future State**:
- Actions can be executed with approval
- Approval workflow (Slack, PagerDuty, etc.)
- Action audit log
- Rollback capability
- RBAC for action execution

**Requirements**:
- Strong guardrails
- Dry-run mode
- Rate limiting
- Namespace/cluster scoping

**Status**: Design phase (high risk, needs careful planning)

---

### Postmortem Generation

**Goal**: Auto-generate postmortem draft from investigation + resolution notes.

**Features**:
- Timeline extraction from case history
- Root cause summary from RCA insights
- Action items from resolution notes
- Export to Markdown/Notion/Confluence

**Status**: Idea phase

---

### SLO Integration

**Goal**: Correlate incidents with SLO burn rate and error budget.

**Features**:
- Query SLO metrics during investigation
- Show error budget consumption
- Flag incidents that burned significant budget
- Prioritize investigations based on SLO impact

**Status**: Requirements gathering

---

### Alert Fatigue Metrics

**Goal**: Quantify and reduce alert noise.

**Metrics**:
- Alerts per on-call shift
- False positive rate (alerts marked as noise)
- Mean time to acknowledge
- Alerts without action taken
- Recommend alert suppression or tuning

**Status**: Backlog

---

### Observability Pipeline Health

**Goal**: Monitor health of observability infrastructure itself.

**Detection**:
- Prometheus scrape failures
- VictoriaLogs ingestion lag
- NATS queue depth
- S3 write latency
- PostgreSQL connection pool saturation

**Alerts**: Meta-alerts for observability issues

**Status**: Partial (some metrics exposed)

---

## Research / Experimental

### ML-Based Anomaly Detection

**Goal**: Use ML to detect anomalies beyond threshold-based alerts.

**Approach**:
- Train on historical metric data
- Detect outliers in multi-dimensional space
- Generate synthetic alerts for detected anomalies
- Feedback loop from on-call marking true/false positives

**Status**: Research

---

### Cost Attribution

**Goal**: Estimate cost of incidents (downtime, lost revenue, engineering time).

**Inputs**:
- Time to resolution
- Blast radius (affected users, requests)
- Service tier (gold/silver/bronze)
- Engineering hourly rate

**Output**: Estimated incident cost in report

**Status**: Idea phase

---

## Contributing Ideas

Have a feature request or idea? Please:
1. Check if it's already listed here
2. Open a GitHub issue with label `enhancement`
3. Describe the use case and expected benefit
4. Provide example scenarios if possible

We prioritize features that:
- Reduce mean time to resolution (MTTR)
- Improve on-call experience
- Scale to large clusters
- Minimize false positives
