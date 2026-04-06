import type {
  CaseDetailResponse,
  ExecOverviewResponse,
  InboxResponse,
  InvestigationRunDetailResponse,
} from "../lib/types";

export const mockInbox: InboxResponse = {
  total: 6,
  counts: { open: 12, closed: 3, total: 15 },
  items: [
    {
      case_id: "case_3920_11111111-1111-1111-1111-111111111111",
      case_status: "open",
      case_created_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
      case_updated_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
      run_id: "run_3920_22222222-2222-2222-2222-222222222222",
      run_created_at: new Date(Date.now() - 12 * 60 * 1000).toISOString(),
      classification: "actionable",
      one_liner: "High Latency on Payment Gateway",
      service: "Payments-Svc",
      team: "Payments",
      impact_score: 98,
      confidence_score: 85,
      noise_score: 12,
      llm_total_tokens: 8420,
      llm_cost_usd: 0.0238,
      alertname: "HttpLatencyHigh",
      family: "http_latency",
      primary_driver: "suspected_image_pull_backoff",
      severity: "critical",
      cluster: "prod",
      namespace: "payments",
      effective_status: "firing",
      latest_alert_state: "firing",
      run_count: 3,
    },
    {
      case_id: "case_3918_33333333-3333-3333-3333-333333333333",
      case_status: "open",
      case_created_at: new Date(Date.now() - 10 * 60 * 60 * 1000).toISOString(),
      case_updated_at: new Date(Date.now() - 45 * 60 * 1000).toISOString(),
      run_id: "run_3918_44444444-4444-4444-4444-444444444444",
      run_created_at: new Date(Date.now() - 45 * 60 * 1000).toISOString(),
      classification: "actionable",
      one_liner: "Error Rate Spike in Auth Service",
      service: "Auth-Service",
      team: "Identity",
      impact_score: 75,
      confidence_score: 60,
      noise_score: 25,
      llm_total_tokens: 5130,
      llm_cost_usd: 0.0144,
      alertname: "Http5xxRateHigh",
      family: "http_5xx",
      primary_driver: "suspected_image_pull_backoff",
      severity: "warning",
      cluster: "prod",
      namespace: "auth",
      effective_status: "firing",
      latest_alert_state: "firing",
      run_count: 1,
    },
    {
      case_id: "case_3915_55555555-5555-5555-5555-555555555555",
      case_status: "open",
      case_created_at: new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString(),
      case_updated_at: new Date(Date.now() - (60 * 60 + 20 * 60) * 1000).toISOString(),
      run_id: "run_3915_66666666-6666-6666-6666-666666666666",
      run_created_at: new Date(Date.now() - (60 * 60 + 20 * 60) * 1000).toISOString(),
      classification: "actionable",
      one_liner: "Database Connection Timeout",
      service: "User-DB",
      impact_score: 92,
      confidence_score: 90,
      noise_score: 5,
      llm_total_tokens: 11250,
      llm_cost_usd: 0.0317,
      alertname: "DBConnectionTimeout",
      family: "database",
      primary_driver: "suspected_image_pull_backoff",
      severity: "critical",
      cluster: "prod",
      namespace: "users",
      effective_status: "firing",
      latest_alert_state: "firing",
      run_count: 2,
    },
    {
      case_id: "case_3899_77777777-7777-7777-7777-777777777777",
      case_status: "open",
      case_created_at: new Date(Date.now() - 4 * 24 * 60 * 60 * 1000).toISOString(),
      case_updated_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
      run_id: "run_3899_88888888-8888-8888-8888-888888888888",
      run_created_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
      classification: "noisy",
      one_liner: "Memory Usage > 80%",
      service: "Cache-Cluster",
      impact_score: 45,
      confidence_score: 30,
      noise_score: 80,
      llm_total_tokens: 3870,
      llm_cost_usd: 0.0109,
      alertname: "MemoryUsageHigh",
      family: "memory_pressure",
      primary_driver: "suspected_image_pull_backoff",
      severity: "info",
      cluster: "prod",
      namespace: "cache",
      effective_status: "firing",
      latest_alert_state: "firing",
      run_count: 1,
    },
    {
      case_id: "case_3880_99999999-9999-9999-9999-999999999999",
      case_status: "open",
      case_created_at: new Date(Date.now() - 18 * 60 * 60 * 1000).toISOString(),
      case_updated_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
      run_id: "run_3880_aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      run_created_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
      classification: "actionable",
      one_liner: "API Response Degradation",
      service: "Search-API",
      impact_score: 68,
      confidence_score: 72,
      noise_score: 15,
      llm_total_tokens: 6640,
      llm_cost_usd: 0.0187,
      alertname: "ApiLatencyDegraded",
      family: "http_latency",
      primary_driver: "suspected_image_pull_backoff",
      severity: "warning",
      cluster: "prod",
      namespace: "search",
      effective_status: "firing",
      latest_alert_state: "firing",
      run_count: 4,
    },
    {
      case_id: "case_3875_bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
      case_status: "open",
      case_created_at: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString(),
      case_updated_at: new Date(Date.now() - 12 * 60 * 60 * 1000).toISOString(),
      run_id: "run_3875_cccccccc-cccc-cccc-cccc-cccccccccccc",
      run_created_at: new Date(Date.now() - 12 * 60 * 60 * 1000).toISOString(),
      classification: "informational",
      one_liner: "Image Processing Delay",
      service: "Media-Worker",
      impact_score: 35,
      confidence_score: 65,
      noise_score: 55,
      llm_total_tokens: 4290,
      llm_cost_usd: 0.0121,
      alertname: "QueueBacklogHigh",
      family: "queue_backlog",
      primary_driver: "suspected_image_pull_backoff",
      severity: "info",
      cluster: "prod",
      namespace: "media",
      effective_status: "stale",
      latest_alert_state: "unknown",
      run_count: 1,
    },
  ],
};

export function mockCaseDetail(caseId: string): CaseDetailResponse {
  const row = mockInbox.items.find((x) => x.case_id === caseId) || mockInbox.items[0];
  const runCount = (row as any).run_count ?? 1;
  const effectiveStatus = (row as any).effective_status ?? "firing";
  const latestAlertState = (row as any).latest_alert_state ?? "firing";

  const extraRuns =
    runCount > 1
      ? Array.from({ length: Math.min(runCount - 1, 3) }, (_, i) => ({
          run_id: `${row.run_id}-prev-${i + 1}`,
          created_at: new Date(
            Date.parse(row.run_created_at) - (i + 1) * 3 * 60 * 60 * 1000
          ).toISOString(),
          alertname: row.alertname,
          severity: row.severity,
          classification: row.classification,
          primary_driver: row.primary_driver,
          one_liner: `Previous: ${row.one_liner}`,
          normalized_state: "firing",
        }))
      : [];

  return {
    case: {
      case_id: caseId,
      status: effectiveStatus === "resolved" ? "closed" : "open",
      created_at: row.case_created_at,
      updated_at: row.case_updated_at,
      latest_one_liner: row.one_liner,
      service: row.service,
      cluster: row.cluster,
      namespace: row.namespace,
      family: row.family,
      primary_driver: row.primary_driver,
      effective_status: effectiveStatus,
      run_count: runCount,
    },
    runs: [
      {
        run_id: row.run_id,
        created_at: row.run_created_at,
        alertname: row.alertname,
        severity: row.severity,
        classification: row.classification,
        primary_driver: row.primary_driver,
        one_liner: row.one_liner,
        normalized_state: latestAlertState,
      },
      ...extraRuns,
    ],
  };
}

export function mockExecOverview(): ExecOverviewResponse {
  // Build daily trend for last 30 days
  const daily = Array.from({ length: 30 }, (_, i) => {
    const d = new Date(Date.now() - (29 - i) * 24 * 60 * 60 * 1000);
    const day = d.toISOString().slice(0, 10);
    const base = 3 + Math.floor(Math.sin(i / 3) * 2 + Math.random() * 3);
    return { day, incidents_created: base, impact_median: 45 + Math.floor(Math.random() * 40) };
  });

  const mttrWeekly = Array.from({ length: 4 }, (_, i) => {
    const d = new Date(Date.now() - (3 - i) * 7 * 24 * 60 * 60 * 1000);
    const week = d.toISOString().slice(0, 10);
    return { week, mttr_hours_median: 1.2 + i * 0.3, resolved_count: 8 + i * 3 };
  });

  const costDaily = Array.from({ length: 30 }, (_, i) => {
    const d = new Date(Date.now() - (29 - i) * 24 * 60 * 60 * 1000);
    return { day: d.toISOString().slice(0, 10), cost_usd: 0.08 + Math.random() * 0.12 };
  });

  return {
    risk: {
      active_count: 14,
      active_high_impact_count: 4,
      stale_investigation_count: 2,
      oldest_active_created_at: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString(),
      top_active: [
        {
          incident_id: "case_3920_11111111-1111-1111-1111-111111111111",
          created_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
          one_liner: "High Latency on Payment Gateway",
          alertname: "HttpLatencyHigh",
          team: "Payments",
          service: "Payments-Svc",
          family: "http_latency",
          impact_score: 98,
          confidence_score: 85,
        },
        {
          incident_id: "case_3915_55555555-5555-5555-5555-555555555555",
          created_at: new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString(),
          one_liner: "Database Connection Timeout",
          alertname: "DBConnectionTimeout",
          team: "Platform",
          service: "User-DB",
          family: "database",
          impact_score: 92,
          confidence_score: 90,
        },
        {
          incident_id: "case_3918_33333333-3333-3333-3333-333333333333",
          created_at: new Date(Date.now() - 10 * 60 * 60 * 1000).toISOString(),
          one_liner: "Error Rate Spike in Auth Service",
          alertname: "Http5xxRateHigh",
          team: "Identity",
          service: "Auth-Service",
          family: "http_5xx",
          impact_score: 75,
          confidence_score: 60,
        },
      ],
    },
    trends: {
      daily,
      mttr_weekly: mttrWeekly,
    },
    focus: {
      top_teams: [
        { team: "Payments", active_count: 4, high_impact_count: 2, total_impact: 320 },
        { team: "Platform", active_count: 3, high_impact_count: 2, total_impact: 280 },
        { team: "Identity", active_count: 3, high_impact_count: 1, total_impact: 195 },
        { team: "Search", active_count: 2, high_impact_count: 0, total_impact: 110 },
        { team: "Media", active_count: 2, high_impact_count: 0, total_impact: 80 },
      ],
      top_drivers: [
        { driver: "high_error_rate", active_count: 5, high_impact_count: 3, total_impact: 415 },
        { driver: "latency_spike", active_count: 4, high_impact_count: 2, total_impact: 310 },
        { driver: "memory_pressure", active_count: 3, high_impact_count: 1, total_impact: 185 },
        { driver: "image_pull_backoff", active_count: 2, high_impact_count: 1, total_impact: 140 },
        { driver: "queue_backlog", active_count: 2, high_impact_count: 0, total_impact: 70 },
      ],
      top_services: [
        {
          service: "Payments-Svc",
          incident_count: 4,
          unique_alert_types: 3,
          median_impact: 88,
          change_correlated_count: 2,
        },
        {
          service: "User-DB",
          incident_count: 3,
          unique_alert_types: 2,
          median_impact: 82,
          change_correlated_count: 1,
        },
        {
          service: "Auth-Service",
          incident_count: 3,
          unique_alert_types: 2,
          median_impact: 71,
          change_correlated_count: 0,
        },
        {
          service: "Search-API",
          incident_count: 2,
          unique_alert_types: 2,
          median_impact: 60,
          change_correlated_count: 1,
        },
        {
          service: "Media-Worker",
          incident_count: 2,
          unique_alert_types: 1,
          median_impact: 38,
          change_correlated_count: 0,
        },
      ],
    },
    recurrence: {
      rate: 0.22,
      top: [
        { incident_key: "HttpLatencyHigh/payments", count: 6 },
        { incident_key: "DBConnectionTimeout/users", count: 4 },
        { incident_key: "Http5xxRateHigh/auth", count: 3 },
      ],
    },
    ai: {
      ttfa_median_seconds: 47,
      ttfa_p90_seconds: 112,
      confidence_ge_70_pct: 74,
      gaps_pct: { missing_one_liner: 4, missing_team: 11, missing_family: 7 },
    },
    signal: {
      total_runs: 143,
      actionable: 68,
      noisy: 42,
      informational: 22,
      unclassified: 11,
      actionable_pct: 48,
      change_correlated_count: 19,
      change_correlated_pct: 13,
    },
    savings: {
      total_runs: 143,
      high_conf_runs: 98,
      low_conf_runs: 45,
      actionable_runs: 68,
      deflected_runs: 98,
      hours_saved: 32.7,
      cost_saved_usd: 4905,
      triage_minutes_assumed: 20,
      hourly_rate_usd_assumed: 150,
    },
    cost: {
      total_usd: costDaily.reduce((s, d) => s + d.cost_usd, 0),
      avg_per_run_usd: 0.0215,
      total_runs: 143,
      daily: costDaily,
    },
  };
}

export function mockRunDetail(runId: string): InvestigationRunDetailResponse {
  const row = mockInbox.items.find((x) => x.run_id === runId) || mockInbox.items[0];
  const md = `# Triage Report: ${row.alertname ?? "Unknown"}

## Triage

**Summary:** ${row.primary_driver ?? "n/a"}
`;

  return {
    run: {
      run_id: row.run_id,
      case_id: row.case_id,
      created_at: row.run_created_at,
      alertname: row.alertname,
      severity: row.severity,
      classification: row.classification,
      primary_driver: row.primary_driver,
      one_liner: row.one_liner,
      report_text: md,
      analysis_json: {
        analysis: {
          scores: {
            impact_score: row.impact_score,
            confidence_score: row.confidence_score,
            noise_score: row.noise_score,
          },
          verdict: {
            classification: row.classification,
            primary_driver: row.primary_driver,
            one_liner: row.one_liner,
            next_steps: [
              "Mock next step: verify recent changes",
              "Mock next step: check saturation",
            ],
          },
          llm: {
            usage: {
              input_tokens: 12480,
              output_tokens: 3200,
              total_tokens: 15680,
              estimated_cost_usd: 0.0855,
            },
          },
          rca: {
            usage: {
              input_tokens: 8100,
              output_tokens: 2400,
              total_tokens: 10500,
              estimated_cost_usd: 0.0603,
            },
          },
        },
        target: {
          service: row.service,
          namespace: row.namespace,
          cluster: row.cluster,
          team: (row as any).team || null,
        },
      },
    },
  };
}
