export type InboxRow = {
  case_id: string;
  case_status: string;
  case_created_at: string;
  case_updated_at: string;
  run_id: string;
  run_created_at: string;

  title?: string | null;
  enrichment_summary?: string | null;
  alertname?: string | null;
  severity?: string | null;
  cluster?: string | null;
  namespace?: string | null;
  service?: string | null;
  instance?: string | null;
  family?: string | null;
  team?: string | null;
  classification?: string | null;
  primary_driver?: string | null;
  one_liner?: string | null;

  impact_score?: number | null;
  confidence_score?: number | null;
  noise_score?: number | null;
};

export type InboxResponse = {
  total: number;
  counts: Record<string, number>;
  items: InboxRow[];
};

export type CaseFacetsResponse = {
  teams: string[];
};

export type CaseDetailResponse = {
  case: CaseRecord;
  runs: InvestigationRunSummary[];
};

export type CaseRecord = {
  case_id?: string | null;
  status?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  latest_one_liner?: string | null;
  service?: string | null;
  namespace?: string | null;
  cluster?: string | null;
  family?: string | null;
  primary_driver?: string | null;
  resolved_at?: string | null;
  resolution_category?: string | null;
  resolution_summary?: string | null;
  postmortem_link?: string | null;
  [k: string]: unknown;
};

export type ActionConfigResponse = {
  enabled: boolean;
  require_approval: boolean;
  allow_execute: boolean;
  action_type_allowlist?: string[] | null;
  max_actions_per_case: number;
};

export type CaseActionRecord = {
  action_id: string;
  case_id: string;
  run_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  status: string;
  hypothesis_id?: string | null;
  action_type: string;
  title: string;
  risk?: string | null;
  preconditions?: string[] | null;
  execution_payload?: Record<string, unknown> | null;
  proposed_by?: string | null;
  approved_at?: string | null;
  approved_by?: string | null;
  approval_notes?: string | null;
  executed_at?: string | null;
  executed_by?: string | null;
  execution_notes?: string | null;
};

export type CaseActionsListResponse = {
  ok: boolean;
  items: CaseActionRecord[];
};

export type CaseMemorySimilarCase = {
  case_id: string;
  run_id: string;
  created_at?: string | null;
  one_liner?: string | null;
  s3_report_key?: string | null;
  resolution_category?: string | null;
  resolution_summary?: string | null;
  postmortem_link?: string | null;
};

export type CaseMemorySkill = {
  name: string;
  version: number;
  rendered?: string | null;
  match_reason?: string | null;
};

export type CaseMemoryResponse = {
  ok: boolean;
  enabled: boolean;
  similar_cases: CaseMemorySimilarCase[];
  skills: CaseMemorySkill[];
  errors: string[];
};

export type InvestigationRunSummary = {
  run_id?: string | null;
  created_at?: string | null;
  alertname?: string | null;
  severity?: string | null;
  classification?: string | null;
  primary_driver?: string | null;
  one_liner?: string | null;
  analysis_json?: AnalysisJson | null;
  report_text?: string | null;
  [k: string]: unknown;
};

export type InvestigationRunDetailResponse = {
  run: RunRecord;
};

export type RunRecord = {
  run_id?: string | null;
  case_id?: string | null;
  created_at?: string | null;
  alertname?: string | null;
  severity?: string | null;
  classification?: string | null;
  primary_driver?: string | null;
  one_liner?: string | null;
  impact_score?: number | null;
  confidence_score?: number | null;
  noise_score?: number | null;
  team?: string | null;
  report_text?: string | null;
  analysis_json?: AnalysisJson | null;
  case_match_reason?: string | null;
  [k: string]: unknown;
};

// Minimal slice of the backend `investigation_to_json_dict(mode="analysis")` output.
export type AnalysisJson = {
  alert?: {
    labels?: Record<string, unknown> | null;
    core_labels?: {
      alertname?: string | null;
      severity?: string | null;
      cluster?: string | null;
      namespace?: string | null;
      service?: string | null;
      target_type?: string | null;
      [k: string]: unknown;
    } | null;
    [k: string]: unknown;
  } | null;
  target?: {
    target_type?: string | null;
    namespace?: string | null;
    pod?: string | null;
    container?: string | null;
    workload_kind?: string | null;
    workload_name?: string | null;
    service?: string | null;
    instance?: string | null;
    cluster?: string | null;
    team?: string | null;
    [k: string]: unknown;
  } | null;
  analysis?: {
    decision?: AnalysisDecision | null;
    enrichment?: AnalysisDecision | null;
    verdict?: AnalysisVerdict | null;
    scores?: AnalysisScores | null;
    features?: AnalysisFeatures | null;
    change?: AnalysisChange | null;
    llm?: AnalysisLLM | null;
    hypotheses?: AnalysisHypothesis[] | null;
    debug?: AnalysisDebug | null;
    rca?: {
      status?: string | null;
      summary?: string | null;
      root_cause?: string | null;
      confidence_0_1?: number | null;
      evidence?: string[] | null;
      remediation?: string[] | null;
      unknowns?: string[] | null;
      [k: string]: unknown;
    } | null;
    [k: string]: unknown;
  } | null;
  errors?: string[] | null;
  [k: string]: unknown;
};

export type AnalysisHypothesis = {
  hypothesis_id?: string | null;
  title?: string | null;
  confidence_0_100?: number | null;
  why?: string[] | null;
  next_tests?: string[] | null;
  supporting_refs?: string[] | null;
  counter_refs?: string[] | null;
  proposed_actions?: AnalysisActionProposal[] | null;
  [k: string]: unknown;
};

export type AnalysisActionProposal = {
  action_type?: string | null;
  title?: string | null;
  risk?: string | null;
  preconditions?: string[] | null;
  execution_payload?: Record<string, unknown> | null;
  [k: string]: unknown;
};

export type ChatConfigResponse = {
  enabled: boolean;
  allow_promql: boolean;
  allow_k8s_read: boolean;
  allow_logs_query: boolean;
  allow_argocd_read: boolean;
  allow_report_rerun: boolean;
  allow_memory_read: boolean;
  max_steps: number;
  max_tool_calls: number;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type ChatToolEvent = {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  result?: unknown;
  error?: string | null;
  outcome?: "ok" | "empty" | "unavailable" | "error" | "skipped_duplicate" | null;
  summary?: string | null;
  key?: string | null;
};

export type ChatResponse = {
  reply: string;
  tool_events?: ChatToolEvent[] | null;
  updated_analysis?: Record<string, unknown> | null;
};

// --- Threaded chat (server persisted) ---

export type ChatStoredMessage = {
  message_id: string;
  seq: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

export type ChatThreadItem = {
  thread_id: string;
  kind: "global" | "case";
  case_id?: string | null;
  title?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_message_at?: string | null;
  last_message?: { seq: number; role: string; content: string; created_at: string } | null;
};

export type ChatThreadsListResponse = {
  ok: boolean;
  items: ChatThreadItem[];
};

export type ChatThreadGetResponse = {
  ok: boolean;
  thread: ChatThreadItem;
  messages: ChatStoredMessage[];
};

export type ChatThreadSendResponse = {
  ok: boolean;
  thread: ChatThreadItem;
  reply?: string | null;
  tool_events?: ChatToolEvent[] | null;
  updated_analysis?: Record<string, unknown> | null;
  messages: ChatStoredMessage[];
};

export type AnalysisDecision = {
  label?: string | null;
  why?: string[] | null;
  next?: string[] | null;
};

export type AnalysisScores = {
  impact_score?: number | null;
  confidence_score?: number | null;
  noise_score?: number | null;
  reason_codes?: string[] | null;
};

export type AnalysisVerdict = {
  classification?: string | null;
  severity?: string | null;
  primary_driver?: string | null;
  one_liner?: string | null;
  next_steps?: string[] | null;
};

export type AnalysisLLM = {
  provider?: string | null;
  status?: string | null;
  model?: string | null;
  error?: string | null;
  output?: {
    summary?: string | null;
    likely_root_cause?: string | null;
    confidence?: number | null;
    evidence?: string[] | null;
    next_steps?: string[] | null;
    unknowns?: string[] | null;
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
};

export type AnalysisFeatures = {
  family?: string | null;
  metrics?: {
    cpu_throttle_p95_pct?: number | null;
    http_5xx_rate_p95?: number | null;
    memory_near_limit?: boolean | null;
    cpu_near_limit?: boolean | null;
    [k: string]: unknown;
  } | null;
  logs?: {
    status?: string | null;
    backend?: string | null;
    reason?: string | null;
    error_hits?: number | null;
    timeout_hits?: number | null;
    [k: string]: unknown;
  } | null;
  changes?: {
    rollout_within_window?: boolean | null;
    last_change_ts?: string | null;
    workload_kind?: string | null;
    workload_name?: string | null;
    [k: string]: unknown;
  } | null;
  quality?: {
    evidence_quality?: string | null;
    alert_age_hours?: number | null;
    is_long_running?: boolean | null;
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
};

export type AnalysisChange = {
  has_recent_change?: boolean | null;
  score?: number | null;
  summary?: string | null;
  last_change_time?: string | null;
  [k: string]: unknown;
};

export type AnalysisDebug = {
  promql?: Record<string, string> | null;
  [k: string]: unknown;
};

// --- Exec/leadership dashboard API types ---

export type ExecTrendDay = {
  day: string;
  incidents_created: number;
  impact_median?: number | null;
  [k: string]: unknown;
};

export type ExecTopIncident = {
  incident_id: string;
  created_at?: string | null;
  one_liner?: string | null;
  alertname?: string | null;
  team?: string | null;
  service?: string | null;
  family?: string | null;
  impact_score?: number | null;
  confidence_score?: number | null;
  [k: string]: unknown;
};

export type ExecTopTeam = {
  team: string;
  active_count: number;
  high_impact_count: number;
  total_impact: number;
  [k: string]: unknown;
};

export type ExecTopDriver = {
  driver: string;
  active_count: number;
  high_impact_count: number;
  total_impact: number;
  [k: string]: unknown;
};

export type ExecRecurrenceTop = {
  incident_key: string;
  count: number;
  [k: string]: unknown;
};

export type ExecOverviewResponse = {
  risk?: {
    active_count?: number | null;
    active_high_impact_count?: number | null;
    stale_investigation_count?: number | null;
    oldest_active_created_at?: string | null;
    top_active?: ExecTopIncident[] | null;
    [k: string]: unknown;
  } | null;
  trends?: {
    daily?: ExecTrendDay[] | null;
    [k: string]: unknown;
  } | null;
  focus?: {
    top_teams?: ExecTopTeam[] | null;
    top_drivers?: ExecTopDriver[] | null;
    [k: string]: unknown;
  } | null;
  recurrence?: {
    rate?: number | null;
    top?: ExecRecurrenceTop[] | null;
    [k: string]: unknown;
  } | null;
  ai?: {
    ttfa_median_seconds?: number | null;
    ttfa_p90_seconds?: number | null;
    confidence_ge_70_pct?: number | null;
    gaps_pct?: {
      missing_one_liner?: number | null;
      missing_team?: number | null;
      missing_family?: number | null;
      [k: string]: unknown;
    } | null;
    [k: string]: unknown;
  } | null;
  [k: string]: unknown;
};

export type ExecLearningSkill = {
  skill_id: string;
  name: string;
  status: string;
  version?: number | null;
  feedback_count?: number | null;
  [k: string]: unknown;
};

export type ExecLearningResponse = {
  skills_by_status?: Record<string, number | null> | null;
  feedback_by_week?: Record<string, Record<string, number | null>> | null;
  top_skills_by_feedback?: ExecLearningSkill[] | null;
  [k: string]: unknown;
};
