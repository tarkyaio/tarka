# Incident Report Acceptance Checklist (On-call First)

This is a **living** acceptance checklist for what “good enough” looks like for an on-call engineer.

- **Goal**: A base report that is actionable **without leaving the report**, then **family-specific enrichment** (e.g. `pod_not_healthy`).
- **Non-goal**: Picking root cause by guesswork. If evidence is missing, we must say “unknown” and give the fastest way to learn it.

## Base report checklist (family-agnostic, “good enough”)

The base report is acceptable only if an on-call can answer in **<60 seconds**:
**what broke, where, how bad, is it real, what do I do next**.

## Investigation-driven base triage contract (`analysis.decision`)

To keep rendering simple and deterministic, the **base triage** should be fully representable from the `Investigation` model:
`investigation.analysis.decision` is the **single source of truth** for the base triage block.

This contract is intended to be populated for **all alerts**, including “unknown/generic” families.

### `decision.label` (required, 1 line)

Short triage headline for scanning:

- Format: `"<ScopeLabel> • Impact=<impact_label> • Discriminator=<discriminator_label>"`

Examples:
- `Broad • Impact=unknown • Discriminator=blocked_no_target_identity`
- `Small • Impact=unknown • Discriminator=blocked_no_k8s_context`
- `Small • Impact=unknown • Discriminator=logs_missing`
- `Scope=unknown • Impact=unknown • Discriminator=blocked_prometheus_unavailable`

### `decision.why` (required, 6–10 bullets)

These are “trust + context” facts. Each bullet should either be **evidence-backed** from the investigation or explicitly **unknown/unavailable**.

Minimum expected bullets (family-agnostic):
- **Scope:** `Scope: firing_instances=<n>, active_instances=<n> (selector=<...> | prom_status=<...>)`
- **Target (best-effort):** `Target: type=<...> cluster=<...> namespace=<...> pod=<...> service=<...> instance=<...>`
- **Impact:** `Impact: unknown (missing: logs,http_metrics)` OR `Impact: low (no impact signals observed)`
- **Primary discriminator:** a single best discriminator if present, else `Primary discriminator: missing`
- **Evidence status:** `Evidence: k8s=<ok/unavailable>, metrics=<ok/unavailable>, logs=<ok/unavailable>, changes=<yes/no>`
- **Contradictions:** only if present (e.g., `severity label mismatch`, `signal contradicts state`)

### `decision.next` (required, 3–7 actions)

Fast discriminators, **copy/paste friendly**. The first item must resolve the highest-impact unknown.

- Must include at least one action that answers **“is this expected?”** (policy/runbook/known maintenance vs incident).
- If scope is **widespread**, include one **stop-the-bleeding** option or explicitly say **none recommended** with why.

## ScopeLabel thresholds (base, family-agnostic)

Scope labels should be computed from Prometheus ALERTS meta-signals when available:
prefer `firing_instances`; fallback to `active_instances`. If neither is available, scope is `unknown`.

- **Single-instance**: \(n = 1\)
- **Small**: \(2 \le n \le 5\)
- **Multi-instance**: \(6 \le n \le 20\)
- **Broad**: \(21 \le n \le 49\)
- **Widespread**: \(50 \le n \le 100\)
- **Massive**: \(n \ge 101\)
- **Scope=unknown**: Prometheus noise unavailable OR counts missing/non-numeric

Notes:
- If the Prometheus selector is extremely broad (e.g., missing namespace/cluster matchers), treat scope as **lower confidence** and say so.
- “Widespread/Massive” should trigger the base report’s **containment hook** (even if the suggestion is “no containment recommended”).

## Default “first-command” library (base, by missing-condition)

These are safe defaults for on-call speed. The report should substitute placeholders from investigation data when available.

**Guiding rule:** Prefer **PromQL-first** (works without cluster credentials). Include `kubectl` commands only as an optional fallback when the responder has access.

### Condition: Prometheus scope unavailable (`noise.prometheus.status != ok`)

- First command (verify from Prometheus UI/CLI):
  - `count(ALERTS{alertstate="firing"})`
- If you have alertname:
  - `count(ALERTS{alertname="<alertname>",alertstate="firing"})`

### Condition: Target identity weak/missing (no namespace/pod/service/instance)

- First command (find how it’s firing; identify stable dimensions):
  - `count by (cluster, namespace) (ALERTS{alertname="<alertname>",alertstate="firing"})`
- If pod labels exist on the alert series:
  - `topk(20, count by (cluster, namespace, pod) (ALERTS{alertname="<alertname>",alertstate="firing"}))`
- Optional follow-up (capture missing labels for fixing the alert rule):
  - MetricsQL/VictoriaMetrics: `label_names(ALERTS{alertname="<alertname>",alertstate="firing"})`
  - Otherwise: inspect the label set from `ALERTS{...}` output above and note what’s missing.

### Condition: K8s context missing/unavailable (pod target exists but no pod_info/conditions/events)

- First command (PromQL; kube-state-metrics):
  - `max by (cluster, namespace, pod) (kube_pod_status_phase{namespace="<namespace>",pod="<pod>"})`
- Next discriminators (PromQL; if container label exists):
  - `max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_waiting_reason{namespace="<namespace>",pod="<pod>"})`
  - `max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{namespace="<namespace>",pod="<pod>"})`
  - `max by (cluster, namespace, pod) (kube_pod_container_status_restarts_total{namespace="<namespace>",pod="<pod>"})`
- Optional fallback (if you have cluster access):
  - `kubectl -n <namespace> get pod <pod> -o wide`
  - `kubectl -n <namespace> describe pod <pod>`
  - `kubectl -n <namespace> get events --sort-by=.lastTimestamp | tail -n 50`

### Condition: Logs missing (unavailable or empty)

- First command (PromQL-first; find a discriminator without logs):
  - `max by (cluster, namespace, pod) (kube_pod_status_phase{namespace="<namespace>",pod="<pod>"})`
- Next discriminators (PromQL):
  - `max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_waiting_reason{namespace="<namespace>",pod="<pod>"})`
  - `max by (cluster, namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{namespace="<namespace>",pod="<pod>"})`
  - `increase(kube_pod_container_status_restarts_total{namespace="<namespace>",pod="<pod>"}[30m])`
- Optional fallback (if you have cluster access):
  - `kubectl -n <namespace> logs <pod> --since=30m --timestamps --all-containers`
  - `kubectl -n <namespace> logs <pod> --since=30m --timestamps --all-containers --previous`
  - `kubectl -n <namespace> get events --sort-by=.lastTimestamp | tail -n 50`

### Condition: Metrics missing/unavailable (no relevant metrics evidence for this alert)

- First command (confirm raw alert series and labels):
  - `ALERTS{alertname="<alertname>",alertstate="firing"}`
- Follow-up (group to find the dimension that matters most):
  - `count by (cluster, namespace, job, instance) (ALERTS{alertname="<alertname>",alertstate="firing"})`

## Base report checklist (detailed)

### Alert identity + routing

- [ ] **Alertname / severity / normalized state** shown clearly
- [ ] **starts_at / ends_at_kind / generated_at** present
- [ ] **Runbook** present (or explicitly “missing runbook”)
- [ ] **Owning team / service** present (or explicitly “unknown owner”)

### Target identity (the thing that’s actually broken)

- [ ] **Target type** (pod/workload/service/node/cluster) and **stable identifiers**
- [ ] For K8s targets: **cluster, namespace, workload_kind/name (if known), pod, container**
- [ ] **Scrape metadata ≠ affected target** (explicitly distinguish kube-state-metrics `job/instance` from app/workload identity)

### Scope / blast radius

- [ ] **Firing count** and a simple label: **single-instance** vs **widespread**
- [ ] If widespread: show **where it’s concentrated** (cluster/namespace/workload) or state “unknown”
- [ ] If possible: show top grouping hints (e.g., “78 firing across 12 namespaces”)

### Impact statement (“how bad if real”)

- [ ] One sentence: **Potential user impact: unknown/low/medium/high** with the *why*
- [ ] If impact signals are missing: explicitly say **“Impact unknown (missing …)”** and avoid “high impact” tone
- [ ] Avoid claiming user impact from infra-only symptoms unless policy says otherwise

### Classification constraint (protect on-call trust)

- [ ] If there is **no concrete discriminator** (e.g., K8s Event reason, waiting reason, termination reason+message, clear metric threshold breach), the report must **not** present itself as confidently **actionable**; it should be framed as **incomplete triage** with explicit unknowns.

### Reality check / trust (“do we trust attribution/reproduction”)

- [ ] List what evidence is **actually present**: K8s API, metrics, logs, changes
- [ ] **Contradictions** are shown prominently (labels disagree, metrics disagree, etc.)
- [ ] If attribution is weak (missing workload/container), explicitly say **“attribution weak”** and why

### Primary symptom + best current hypothesis

- [ ] **Primary symptom** is specific (not tautological)
- [ ] **Hypothesis** is the best-supported immediate cause (e.g., FailedScheduling / ImagePullBackOff / OOMKilled / exitCode+message)

### Evidence snippets (minimum needed to trust the story)

- [ ] 1–3 key **K8s** facts (phase/condition/event/container state) with timestamps or “within window”
- [ ] 1–3 key **metric** facts relevant to the family (or explicitly “not available”)
- [ ] **Logs**: either 1–3 relevant lines or a clear **failure reason** (backend + error type)

### Missing inputs / unknowns (must be honest)

- [ ] Top 3 **unknowns blocking resolution**
- [ ] For each unknown: provide the **fastest way to obtain it** (command or link)
- [ ] Include a **copy/paste first command** to resolve the highest-impact unknown

### Next actions (fastest discriminators first)

- [ ] 3–5 steps max, ordered by **highest signal / lowest effort**
- [ ] Each step includes a **copy/paste command** where applicable (`kubectl`, PromQL, logs query)
- [ ] At least one step answers: **“is this expected?”** (expected Job failure vs incident)
- [ ] If scope is **widespread**, include one **stop-the-bleeding** option (rollback/scale/disable route/mute) or explicitly say **no containment action recommended** with why

### Operator UX / formatting

- [ ] One-liner includes **resource + reason** (not just “unhealthy”)
- [ ] If a dependency is down (logs backend, metrics), show it as a **hard failure** and suggest a workaround
- [ ] Don’t recommend actions that require missing data without giving a path to get that data

## “Change notes” (ideas only, no code yet)

Keep this section updated as we refine “actionable” definitions.

- [ ] Fix scope/noise correctness: ensure `firing_instances` influences both narrative (“widespread”) and noise scoring.
- [ ] Define base “Impact unknown” rule: if impact signals are missing, cap impact and force honest language unless policy overrides.
- [ ] Populate `analysis.decision` as the base triage contract (label/why/next) for all alerts, including unknown/generic families.
- [ ] Adopt ScopeLabel thresholds and ensure widespread/massive triggers a containment hook (or explicit “none recommended”).
- [ ] Maintain a default “first-command” library keyed by missing-condition so the report always gives a fast discriminator.
- [ ] Logs failure UX: always print backend + failure reason + workaround when logs are unavailable.
- [ ] Contradiction surfacing: explicit “Contradictions” block when severity/labels/scope disagree.
- [ ] Add a widespread “stop-the-bleeding” hook: suggest one safe containment action when blast radius is large (or explicitly recommend none).

## Scenario selection matrix (deterministic)

This matrix maps observable investigation state → which scenario guidance the base report should follow.

| Condition observed in investigation | Use scenario | Notes |
|---|---|---|
| `analysis.noise.prometheus.status != "ok"` OR scope counts missing/non-numeric | **D** | Scope is unknown to the agent; ask on-call to verify in Prom UI |
| Target identity not attributable (e.g., `target_type="unknown"` or missing required identifiers) | **A** | Focus on ALERTS label discovery + routing; do not guess target |
| `target_type="pod"` and `namespace/pod` present but K8s context missing in investigation | **B** | Blocked on K8s context; recover via kube-state-metrics PromQL |
| Logs missing (`features.logs.status in {"empty","unavailable",None}`) | **C** | Proceed with non-log discriminators; verify logs backend vs query/permissions |

## Canonical discriminator vocabulary (base)

To keep reports consistent and easy to scan, the base report should prefer this small set of discriminator labels:

- `blocked_no_target_identity`
- `blocked_no_k8s_context`
- `logs_missing`
- `blocked_prometheus_unavailable`
- `blocked_no_scope_no_identity` (double-blocked)
