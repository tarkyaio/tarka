"""Canonical domain models (single source of truth).

This file is the one place where we define models used across:
- gathering evidence (K8s/Prometheus/Loki)
- analysis (noise, change correlation, capacity)
- rendering (reports)

Design note:
- Evidence payloads are intentionally permissive (`extra="allow"`) because upstream data sources
  and label conventions vary widely across clusters.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseModelAllowExtra(BaseModel):
    # Transitional default; we tighten per-model below.
    model_config = ConfigDict(extra="allow")


class BaseModelStrict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeWindow(BaseModelStrict):
    window: str
    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def _ensure_timezone_aware(cls, v: datetime) -> datetime:
        # Prevent naive/aware mixing bugs in downstream time math.
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class AlertInstance(BaseModelStrict):
    fingerprint: str = Field(default="")
    labels: Dict[str, Any] = Field(default_factory=dict)
    annotations: Dict[str, Any] = Field(default_factory=dict)
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    generator_url: Optional[str] = None
    state: Optional[str] = None
    normalized_state: Optional[Literal["firing", "resolved", "unknown"]] = None
    ends_at_kind: Optional[Literal["expires_at", "resolved_at", "unknown"]] = None


class TargetRef(BaseModelStrict):
    target_type: Literal["pod", "workload", "service", "node", "cluster", "unknown"] = "unknown"
    namespace: Optional[str] = None
    pod: Optional[str] = None
    container: Optional[str] = None
    playbook: Optional[str] = None
    workload_kind: Optional[str] = None
    workload_name: Optional[str] = None
    service: Optional[str] = None
    instance: Optional[str] = None
    job: Optional[str] = None
    cluster: Optional[str] = None
    # Organizational routing metadata (best-effort, derived from K8s workload labels)
    team: Optional[str] = None
    environment: Optional[str] = None


class K8sEvidence(BaseModelAllowExtra):
    pod_info: Optional[Dict[str, Any]] = None
    pod_conditions: List[Dict[str, Any]] = Field(default_factory=list)
    pod_events: List[Dict[str, Any]] = Field(default_factory=list)
    owner_chain: Optional[Dict[str, Any]] = None
    rollout_status: Optional[Dict[str, Any]] = None


class MetricsEvidence(BaseModelAllowExtra):
    throttling_data: Optional[Dict[str, Any]] = None
    cpu_metrics: Optional[Dict[str, Any]] = None
    memory_metrics: Optional[Dict[str, Any]] = None
    restart_data: Optional[Dict[str, Any]] = None
    pod_phase_signal: Optional[Dict[str, Any]] = None
    http_5xx: Optional[Dict[str, Any]] = None


class LogsEvidence(BaseModelAllowExtra):
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    logs_status: Optional[str] = None
    logs_reason: Optional[str] = None
    logs_backend: Optional[str] = None
    logs_query: Optional[str] = None

    # Structured log parsing results
    parsed_errors: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="ERROR/FATAL/Exception patterns extracted from logs"
    )
    parsing_metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Parsing stats: total_lines, error_count, fatal_count, exception_count"
    )


class AwsEvidence(BaseModelAllowExtra):
    """AWS infrastructure evidence (EC2, EBS, ELB, RDS, ECR, networking)."""

    ec2_instances: Dict[str, Any] = Field(default_factory=dict)
    ebs_volumes: Dict[str, Any] = Field(default_factory=dict)
    elb_health: Dict[str, Any] = Field(default_factory=dict)
    rds_instances: Dict[str, Any] = Field(default_factory=dict)
    ecr_images: Dict[str, Any] = Field(default_factory=dict)
    networking: Dict[str, Any] = Field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = None  # Extracted AWS resource IDs

    cloudtrail_events: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Raw CloudTrail events (chronological)"
    )
    cloudtrail_grouped: Optional[Dict[str, List[Dict[str, Any]]]] = Field(
        default=None, description="CloudTrail events grouped by category for presentation"
    )
    cloudtrail_metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="CloudTrail query metadata (time_window, event_count, query_duration)"
    )


class GitHubEvidence(BaseModelAllowExtra):
    """GitHub code change evidence (commits, workflows, docs)."""

    repo: Optional[str] = None  # "org/repo"
    repo_discovery_method: Optional[str] = None  # "annotation", "helm", "catalog", etc.
    is_third_party: bool = False

    recent_commits: List[Dict[str, Any]] = Field(default_factory=list)
    workflow_runs: List[Dict[str, Any]] = Field(default_factory=list)
    failed_workflow_logs: Optional[str] = None

    readme: Optional[str] = None
    docs: List[Dict[str, Any]] = Field(default_factory=list)  # [{path, content}]


class Evidence(BaseModelAllowExtra):
    k8s: K8sEvidence = Field(default_factory=K8sEvidence)
    metrics: MetricsEvidence = Field(default_factory=MetricsEvidence)
    logs: LogsEvidence = Field(default_factory=LogsEvidence)
    aws: AwsEvidence = Field(default_factory=AwsEvidence)
    github: GitHubEvidence = Field(default_factory=GitHubEvidence)


class ChangeCorrelation(BaseModelStrict):
    has_recent_change: Optional[bool] = None
    score: Optional[float] = None
    summary: Optional[str] = None
    last_change_time: Optional[str] = None
    timeline: Optional["ChangeTimeline"] = None


class ChangeEvent(BaseModelStrict):
    timestamp: Optional[str] = None
    kind: str
    name: str
    namespace: str
    reason: Optional[str] = None
    message: Optional[str] = None
    source: str = "kubernetes"


class ChangeTimeline(BaseModelStrict):
    source: str = "kubernetes"
    workload: Optional[Dict[str, Any]] = None
    events: List[ChangeEvent] = Field(default_factory=list)
    last_change_time: Optional[str] = None


class NoiseInsights(BaseModelStrict):
    label_shape: Optional[Dict[str, Any]] = None
    prometheus: Optional[Dict[str, Any]] = None
    notes: List[str] = Field(default_factory=list)
    flap: Optional["NoiseFlapInsights"] = None
    cardinality: Optional["NoiseCardinalityInsights"] = None
    missing_labels: Optional["NoiseMissingLabelsInsights"] = None


class NoiseFlapInsights(BaseModelStrict):
    lookback: str
    flaps_estimate: Optional[float] = None
    flap_score_0_100: int = 0
    notes: List[str] = Field(default_factory=list)


class NoiseCardinalityInsights(BaseModelStrict):
    ephemeral_labels_present: List[str] = Field(default_factory=list)
    recommended_group_by: List[str] = Field(default_factory=list)
    recommended_drop_labels: List[str] = Field(default_factory=list)


class NoiseMissingLabelsInsights(BaseModelStrict):
    missing: List[str] = Field(default_factory=list)
    inferred: List[str] = Field(default_factory=list)
    recommendation: List[str] = Field(default_factory=list)


class CapacityReport(BaseModelStrict):
    status: Optional[str] = None
    error: Optional[str] = None
    scope: Optional[Dict[str, Any]] = None
    queries_used: Optional[Dict[str, Any]] = None
    recommendations: List[str] = Field(default_factory=list)
    rightsizing_cpu: Optional[List[Dict[str, Any]]] = None
    top_cpu_over_request: Optional[List[Dict[str, Any]]] = None
    top_cpu_under_request: Optional[List[Dict[str, Any]]] = None
    top_mem_over_request: Optional[List[Dict[str, Any]]] = None
    top_mem_under_request: Optional[List[Dict[str, Any]]] = None


class Decision(BaseModelStrict):
    label: Optional[str] = None
    why: List[str] = Field(default_factory=list)
    next: List[str] = Field(default_factory=list)


class ActionProposal(BaseModelStrict):
    """
    Placeholder for policy-gated action proposals (approval required).

    Action execution is intentionally out-of-scope for the deterministic core model.
    """

    action_type: str
    title: str
    risk: Optional[str] = None
    preconditions: List[str] = Field(default_factory=list)
    execution_payload: Dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModelStrict):
    """
    Deterministic, evidence-cited diagnosis candidate.

    This is designed to be:
    - portable across orgs (no hard dependency on ownership/catalog)
    - explainable (why + refs)
    - usable by both reports and tool-using chat
    """

    hypothesis_id: str
    title: str
    confidence_0_100: int = 0
    why: List[str] = Field(default_factory=list)
    supporting_refs: List[str] = Field(default_factory=list)
    counter_refs: List[str] = Field(default_factory=list)
    next_tests: List[str] = Field(default_factory=list)
    proposed_actions: List[ActionProposal] = Field(default_factory=list)


LLMStatus = Literal["ok", "disabled", "unavailable", "error", "rate_limited"]


class LLMInsights(BaseModelStrict):
    provider: str
    status: LLMStatus
    model: Optional[str] = None
    error: Optional[str] = None
    output: Optional[Dict[str, Any]] = None


RCAStatus = Literal["ok", "unknown", "blocked", "unavailable", "error"]


class RCAInsights(BaseModelStrict):
    """
    Structured root-cause + remediation output.

    This is intentionally provider-agnostic and should be grounded only in SSOT evidence
    (plus any explicitly captured tool results).
    """

    status: RCAStatus = "unknown"
    summary: Optional[str] = None
    root_cause: Optional[str] = None
    confidence_0_1: Optional[float] = None
    evidence: List[str] = Field(default_factory=list)
    remediation: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)


class Analysis(BaseModelStrict):
    change: Optional[ChangeCorrelation] = None
    noise: Optional[NoiseInsights] = None
    capacity: Optional[CapacityReport] = None
    decision: Optional[Decision] = None
    enrichment: Optional[Decision] = None
    rca: Optional[RCAInsights] = None
    llm: Optional[LLMInsights] = None
    hypotheses: List[Hypothesis] = Field(default_factory=list)
    features: Optional["DerivedFeatures"] = None
    scores: Optional["DeterministicScores"] = None
    verdict: Optional["DeterministicVerdict"] = None
    debug: Optional["DebugInfo"] = None


class DebugInfo(BaseModelStrict):
    promql: Dict[str, str] = Field(default_factory=dict)


class K8sConditionSummary(BaseModelStrict):
    type: str
    status: str
    reason: Optional[str] = None


class K8sContainerWaiting(BaseModelStrict):
    container: str
    reason: Optional[str] = None
    message: Optional[str] = None


class K8sContainerLastTerminated(BaseModelStrict):
    container: str
    reason: Optional[str] = None
    exit_code: Optional[int] = None


class K8sEventSummary(BaseModelStrict):
    reason: Optional[str] = None
    count: Optional[int] = None
    type: Optional[str] = None
    message: Optional[str] = None


class FeaturesK8s(BaseModelStrict):
    pod_phase: Optional[str] = None
    ready: Optional[bool] = None
    waiting_reason: Optional[str] = None
    restart_count: Optional[int] = None
    restart_rate_5m_max: Optional[float] = None
    warning_events_count: Optional[int] = None
    oom_killed: Optional[bool] = None
    oom_killed_events: Optional[int] = None
    evicted: Optional[bool] = None
    status_reason: Optional[str] = None
    status_message: Optional[str] = None
    not_ready_conditions: List[K8sConditionSummary] = Field(default_factory=list)
    container_waiting_reasons_top: List[K8sContainerWaiting] = Field(default_factory=list)
    container_last_terminated_top: List[K8sContainerLastTerminated] = Field(default_factory=list)
    recent_event_reasons_top: List[K8sEventSummary] = Field(default_factory=list)


class FeaturesMetrics(BaseModelStrict):
    cpu_throttle_p95_pct: Optional[float] = None
    cpu_usage_p95_cores: Optional[float] = None
    cpu_limit_cores: Optional[float] = None
    cpu_near_limit: Optional[bool] = None
    pod_unhealthy_phase_observed: Optional[bool] = None
    http_5xx_rate_p95: Optional[float] = None
    http_5xx_rate_max: Optional[float] = None
    memory_usage_p95_bytes: Optional[float] = None
    memory_limit_bytes: Optional[float] = None
    memory_near_limit: Optional[bool] = None
    cpu_throttle_top_container: Optional[str] = None
    cpu_throttle_top_container_p95_pct: Optional[float] = None
    cpu_throttle_top_container_usage_limit_ratio: Optional[float] = None


class FeaturesLogs(BaseModelStrict):
    status: Optional[str] = None
    backend: Optional[str] = None
    reason: Optional[str] = None
    query_used: Optional[str] = None
    timeout_hits: Optional[int] = None
    error_hits: Optional[int] = None


class FeaturesChanges(BaseModelStrict):
    rollout_within_window: Optional[bool] = None
    last_change_ts: Optional[str] = None
    workload_kind: Optional[str] = None
    workload_name: Optional[str] = None


class FeaturesQuality(BaseModelStrict):
    evidence_quality: Optional[Literal["high", "medium", "low"]] = None
    missing_inputs: List[str] = Field(default_factory=list)
    contradiction_flags: List[str] = Field(default_factory=list)
    impact_signals_available: Optional[bool] = None
    missing_impact_signals: List[str] = Field(default_factory=list)
    alert_age_hours: Optional[float] = None
    is_long_running: Optional[bool] = None
    is_recently_started: Optional[bool] = None


class DerivedFeatures(BaseModelStrict):
    family: str
    k8s: FeaturesK8s = Field(default_factory=FeaturesK8s)
    metrics: FeaturesMetrics = Field(default_factory=FeaturesMetrics)
    logs: FeaturesLogs = Field(default_factory=FeaturesLogs)
    changes: FeaturesChanges = Field(default_factory=FeaturesChanges)
    quality: FeaturesQuality = Field(default_factory=FeaturesQuality)
    job_metrics: Optional[Dict[str, Any]] = None  # Job-specific metrics for Evidence display


class ScoreBreakdownItem(BaseModelStrict):
    code: str
    delta: int
    feature_ref: Optional[str] = None
    why: Optional[str] = None


class DeterministicScores(BaseModelStrict):
    impact_score: int
    confidence_score: int
    noise_score: int
    reason_codes: List[str] = Field(default_factory=list)
    breakdown: List[ScoreBreakdownItem] = Field(default_factory=list)


class DeterministicVerdict(BaseModelStrict):
    classification: Literal["actionable", "informational", "noisy", "artifact"]
    # Derived severity (agent-computed). Raw alert label severity remains under `alert.labels.severity`.
    severity: Optional[Literal["critical", "warning", "info"]] = None
    primary_driver: str
    one_liner: str
    next_steps: List[str] = Field(default_factory=list)


class Investigation(BaseModelStrict):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    alert: AlertInstance
    time_window: TimeWindow
    target: TargetRef = Field(default_factory=TargetRef)
    evidence: Evidence = Field(default_factory=Evidence)
    analysis: Analysis = Field(default_factory=Analysis)
    errors: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
