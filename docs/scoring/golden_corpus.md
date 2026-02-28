# Golden corpus (Phase 3 calibration)

This file captures a small set of **real alerts** and the current agent outputs, so we can calibrate:
- score bands (Impact / Confidence / Noise)
- classification wording and thresholds
- artifact split (recovered vs low-confidence)

Each entry includes:
- alert identity (name + fingerprint prefix)
- target summary
- evidence availability summary
- current scores + verdict
- notes (what felt wrong from an on-call perspective)

> Note: this corpus is intentionally small and should evolve as we see more real incidents.

## Non-pod families

### k8s_rollout_health

- alertname: `KubeDeploymentRolloutStuck`
- fingerprint: `2962cb41…`
- observed:
  - family: `k8s_rollout_health`
  - target_type: `workload`
  - missing_inputs: `k8s.pod_info`, `logs`, `metrics.cpu`, `metrics.restarts`
  - scores: impact=80 confidence=70 noise=10
  - verdict: `actionable` — “Kubernetes rollout/workload health alert fired; validate rollout status and controller conditions.”
- notes (on-call): looks reasonable for a real rollout-stuck signal.

### k8s_rollout_health (recovered/stale example)

- alertname: `KubeDaemonSetRolloutStuck`
- fingerprint: `4856ac86…`
- observed:
  - family: `k8s_rollout_health`
  - target_type: `workload`
  - verdict: `artifact` (recovered/stale) — “Rollout health alert is firing, but current rollout status appears healthy…”
  - reason_codes (high-signal): `ROLLOUT_CONTRADICTION_HEALTHY_STATUS`, `ARTIFACT_RECOVERED`
- notes (on-call): this is the exact “artifact_recovered” case we want: alert fired, but current status contradicts it.

### target_down

- alertname: `TargetDown`
- fingerprint: `8edf252d…`
- observed:
  - family: `target_down`
  - target_type: `service`
  - missing_inputs: `k8s.pod_info`, `logs`, `metrics.cpu`, `metrics.restarts`
  - scores: impact=90 confidence=40 noise=0
  - verdict: `informational` — “TargetDown alert is firing, but label-derived up() checks suggest 0 targets down; verify in Prometheus /targets (possible label mismatch or stale signal).”
  - reason_codes (high-signal): `TARGETDOWN_CONTRADICTION_UP_NONE`
- notes (on-call): report evidence said `up==0 count (job): 0/3` while `firing_instances=10` from `ALERTS`. That’s a contradiction we should surface and have it affect confidence/noise.

### observability_pipeline

- alertname: `RowsRejectedOnIngestion`
- fingerprint: `6f3b5aad…`
- observed:
  - family: `observability_pipeline`
  - target_type: `service`
  - missing_inputs: `k8s.pod_info`, `logs`, `metrics.cpu`, `metrics.restarts`
  - scores: impact=70 confidence=70 noise=20
  - verdict: `actionable` — “Monitoring/logging pipeline health alert fired; investigate rule evaluation and ingestion/backpressure.”
- notes (on-call): verdict next steps mention vminsert/logs backend in generic text; enrichment “on-call next” should mention `vminsert` more directly when it’s in labels.

### meta

- alertname: `InfoInhibitor`
- fingerprint: `29abd0d8…`
- observed:
  - family: `meta`
  - target_type: `unknown`
  - missing_inputs: `k8s.pod_info`, `logs`, `metrics.cpu`, `metrics.restarts`
  - scores: impact=0 confidence=70 noise=100
  - verdict: `noisy` — “This is a meta/inhibitor alert intended to suppress other alerts; treat it as operational noise, not a direct incident symptom.”
- notes (on-call): looks correct.

## Pod families

### oom_killed

- alertname: `KubernetesContainerOomKiller`
- fingerprint: `da094824…`
- observed:
  - family: `oom_killed`
  - target_type: `pod`
  - missing_inputs: `k8s.pod_info`
  - scores: impact=50 confidence=0 noise=20
  - verdict: `artifact` — “OOM alert fired (derived from metrics), but the agent could not retrieve corroborating K8s evidence for the container/pod in this window (missing K8s context or stale window).”
  - reason_codes (high-signal): `OOM_CORROBORATION_MISSING`, `ARTIFACT_LOW_CONFIDENCE`
- notes (on-call): we treat alert firing as weak evidence (impact signal), while still being honest that corroboration is missing.

### cpu_throttling

- alertname: `CPUThrottlingHigh`
- fingerprint: `870ac0f9…`
- observed:
  - family: `cpu_throttling`
  - target_type: `pod`
  - missing_inputs: `k8s.pod_info`
  - scores: impact=30 confidence=60 noise=45
  - verdict: `informational` — “CPU throttling p95 is high, but CPU usage is far from the configured limit…”
- notes (on-call): looks reasonable; this is the right direction for “don’t page for throttling that doesn’t constrain usage”.

### pod_not_healthy

- alertname: `KubernetesPodNotHealthyCritical`
- fingerprint: `0a3bd18b…`
- observed:
  - family: `pod_not_healthy`
  - target_type: `pod`
  - missing_inputs: `k8s.pod_info`
  - scores: impact=50 confidence=50 noise=10
  - verdict: `informational` — “Pod phase is `Unknown` in this window.”
- notes (on-call): for a `Critical` variant, we likely want either a stronger impact or stronger “blocked / evidence missing” wording (this is a good calibration candidate).
