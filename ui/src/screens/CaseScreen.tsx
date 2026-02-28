import React from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { useApi, ApiError } from "../lib/api";
import {
  ActionConfigResponse,
  AnalysisActionProposal,
  AnalysisJson,
  CaseActionRecord,
  CaseActionsListResponse,
  CaseDetailResponse,
  CaseMemoryResponse,
  InvestigationRunDetailResponse,
} from "../lib/types";
import { formatAge, shortId } from "../lib/format";
import { extractMarkdownSection, impactLabel, pickPromql, scoreTone } from "../lib/triageReport";
import { useAuth } from "../state/auth";
import { LoginDialog } from "../ui/LoginDialog";
import { ClassificationPill } from "../ui/ClassificationPill";
import { SeverityPill } from "../ui/SeverityPill";
import { useChatShell } from "../state/chat";
import styles from "./CaseScreen.module.css";

// Custom code component for ReactMarkdown - simple, clean rendering
const CodeBlock = ({ inline, className, children, ...props }: any) => {
  return !inline ? (
    <code className={className} {...props}>
      {children}
    </code>
  ) : (
    <code className={className} {...props}>
      {children}
    </code>
  );
};

// Markdown components configuration for all ReactMarkdown instances
const markdownComponents = {
  code: CodeBlock,
};

// Format next steps with smart code block detection (mirrors backend logic)
function formatNextSteps(steps: string[]): string {
  const lines: string[] = [];
  let i = 0;

  const isCommandLine = (s: string): boolean => {
    const stripped = s.trim();
    if (!stripped || stripped.startsWith("```")) return false;

    const commandPrefixes = [
      "kubectl",
      "aws",
      "gcloud",
      "curl",
      "docker",
      "helm",
      "git",
      "python",
      "pip",
      "npm",
      "yarn",
    ];
    if (commandPrefixes.some((cmd) => stripped.startsWith(cmd))) return true;

    // Check for PromQL queries
    if (
      ["ALERTS{", "kube_", "rate(", "sum(", "increase(", "count("].some((pattern) =>
        stripped.includes(pattern)
      ) &&
      stripped.includes("{") &&
      (stripped.includes("=") || stripped.includes("}"))
    ) {
      return true;
    }

    return false;
  };

  while (i < steps.length) {
    const step = steps[i];

    // Handle multi-line code blocks (```json...```)
    if (step.trim().startsWith("```")) {
      const codeBlock = [step];
      i++;
      while (i < steps.length && !steps[i].trim().startsWith("```")) {
        codeBlock.push(steps[i]);
        i++;
      }
      if (i < steps.length) {
        codeBlock.push(steps[i]);
        i++;
      }
      lines.push(...codeBlock);
      continue;
    }

    // Handle empty lines
    if (!step.trim()) {
      lines.push("");
      i++;
      continue;
    }

    // Handle command lines
    if (isCommandLine(step)) {
      lines.push("```bash");
      lines.push(step);
      lines.push("```");
      i++;
      continue;
    }

    // Default: bullet point
    lines.push(`- ${step}`);
    i++;
  }

  return lines.join("\n");
}

function MaterialIcon({ name, filled }: { name: string; filled?: boolean }) {
  return (
    <span
      className={`material-symbols-outlined ${styles.materialIcon} ${filled ? styles.materialIconFilled : ""}`}
      aria-hidden="true"
    >
      {name}
    </span>
  );
}

export function CaseScreen() {
  const { caseId } = useParams();
  const { user, loading: authLoading } = useAuth();
  const { request } = useApi();
  const { mode: chatMode, setActiveCase } = useChatShell();

  const [caseDetail, setCaseDetail] = React.useState<CaseDetailResponse | null>(null);
  const [run, setRun] = React.useState<InvestigationRunDetailResponse | null>(null);
  const [err, setErr] = React.useState<ApiError | null>(null);
  const [loading, setLoading] = React.useState(false);

  const [actionCfg, setActionCfg] = React.useState<ActionConfigResponse | null>(null);
  const [caseActions, setCaseActions] = React.useState<CaseActionRecord[]>([]);
  const [actionsBusy, setActionsBusy] = React.useState(false);
  const [memoryLive, setMemoryLive] = React.useState<CaseMemoryResponse | null>(null);
  const [memoryLoading, setMemoryLoading] = React.useState(false);

  React.useEffect(() => {
    if (!user) return;
    if (!caseId) return;
    let cancelled = false;
    // Prevent stale content flash when navigating between cases.
    setCaseDetail(null);
    setRun(null);
    setLoading(true);
    setErr(null);

    request<CaseDetailResponse>(`/api/v1/cases/${encodeURIComponent(caseId)}?runs_limit=1`)
      .then((d) => {
        if (cancelled) return;
        setCaseDetail(d);
        const latestRunId = String(d.runs?.[0]?.run_id || "");
        if (!latestRunId) return null;
        return request<InvestigationRunDetailResponse>(
          `/api/v1/investigation-runs/${encodeURIComponent(latestRunId)}`
        );
      })
      .then((r) => {
        if (cancelled) return;
        if (r) setRun(r);
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(e as ApiError);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [user, caseId, request]);

  React.useEffect(() => {
    if (!user) return;
    request<ActionConfigResponse>("/api/v1/actions/config")
      .then((cfg) => setActionCfg(cfg))
      .catch(() => setActionCfg(null));
  }, [user, request]);

  async function refreshCaseActions() {
    if (!caseId) return;
    if (!actionCfg?.enabled) return;
    setActionsBusy(true);
    try {
      const resp = await request<CaseActionsListResponse>(
        `/api/v1/cases/${encodeURIComponent(String(caseId))}/actions`
      );
      setCaseActions(resp.items || []);
    } catch {
      setCaseActions([]);
    } finally {
      setActionsBusy(false);
    }
  }

  React.useEffect(() => {
    if (!user) return;
    if (!caseId) return;
    if (!actionCfg?.enabled) return;
    refreshCaseActions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, caseId, actionCfg?.enabled]);

  const title =
    run?.run?.one_liner || caseDetail?.case?.latest_one_liner || run?.run?.alertname || "Case";

  const reportText = run?.run?.report_text || "";
  const analysisJson: AnalysisJson | null = run?.run?.analysis_json || null;

  const caseObj = caseDetail?.case || {};
  const runObj = run?.run || {};

  // Shared fields: single source of truth is the run payload, derived from analysis_json in the backend.
  const classification = runObj.classification || "";

  const alertname = runObj.alertname || analysisJson?.alert?.core_labels?.alertname || "";
  const oneLiner = runObj.one_liner || "";
  const createdAt = caseObj.created_at || "";
  const age = createdAt ? formatAge(createdAt) : "—";
  // Canonical severity source (must match inbox list): the run payload.
  const severity = runObj.severity || null;
  // Team can live either on the run view or inside analysis_json.target (depending on backend mode/source).
  const team = runObj.team || analysisJson?.target?.team || null;
  const teamLabel = team ? String(team).trim().toLowerCase() : "";

  // Convenience alias (used by the Verdict section).
  const analysis = analysisJson?.analysis || null;

  const decision = analysisJson?.analysis?.decision || null;
  const verdict = analysisJson?.analysis?.verdict || null;
  const scores = analysisJson?.analysis?.scores || null;
  const enrichment = analysisJson?.analysis?.enrichment || null;
  const features = analysisJson?.analysis?.features || null;
  const change = analysisJson?.analysis?.change || null;
  const rca = analysisJson?.analysis?.rca || null;
  const promql = pickPromql(analysisJson, 3);

  const affectedBits: string[] = [];
  const tgt = analysisJson?.target || null;
  if (tgt?.service) affectedBits.push(String(tgt.service));
  if (tgt?.workload_name) affectedBits.push(String(tgt.workload_name));
  if (!affectedBits.length && caseObj.service) affectedBits.push(String(caseObj.service));
  if (!affectedBits.length && caseObj.namespace) affectedBits.push(String(caseObj.namespace));
  const affected = affectedBits.filter(Boolean).join(", ") || "—";
  const memoryMd = extractMarkdownSection(reportText, "Memory");

  const confidenceTone = scoreTone(runObj.confidence_score ?? null);
  const impactTone = scoreTone(runObj.impact_score ?? null);
  const noiseTone = scoreTone(runObj.noise_score ?? null);
  const showSkeleton = loading && !err && !run;

  const canActions = Boolean(actionCfg?.enabled && caseId);

  const gridCls = [styles.caseGrid, chatMode === "docked" ? styles.caseGridDocked : ""]
    .filter(Boolean)
    .join(" ");
  const reportWrapCls = [styles.reportWrap, chatMode === "docked" ? styles.reportWrapDocked : ""]
    .filter(Boolean)
    .join(" ");

  // Provide the active case context to the shell-mounted chat host.
  React.useEffect(() => {
    if (!user) return;
    if (!caseId) return;
    const rid = String(runObj?.run_id || "").trim();
    if (!rid) return;
    setActiveCase({ caseId: String(caseId), runId: rid, analysisJson });
    return () => {
      setActiveCase(null);
    };
  }, [user, caseId, runObj?.run_id, analysisJson, setActiveCase]);

  React.useEffect(() => {
    if (!user) return;
    if (!caseId) return;
    if (!runObj?.run_id) return;
    let cancelled = false;
    setMemoryLoading(true);
    request<CaseMemoryResponse>(
      `/api/v1/cases/${encodeURIComponent(String(caseId))}/memory?limit=5`
    )
      .then((d) => {
        if (cancelled) return;
        setMemoryLive(d);
      })
      .catch(() => {
        if (cancelled) return;
        setMemoryLive({
          ok: false,
          enabled: true,
          similar_cases: [],
          skills: [],
          errors: ["memory_fetch_failed"],
        } as any);
      })
      .finally(() => {
        if (cancelled) return;
        setMemoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user, caseId, runObj?.run_id, request]);

  const [resolveCategory, setResolveCategory] = React.useState("unknown");
  const [resolveSummary, setResolveSummary] = React.useState("");
  const [resolveLink, setResolveLink] = React.useState("");
  const [resolveSaving, setResolveSaving] = React.useState(false);

  async function markResolved() {
    if (!caseId) return;
    const cat = resolveCategory.trim();
    const sum = resolveSummary.trim();
    if (!cat || !sum) return;
    setResolveSaving(true);
    try {
      await request(`/api/v1/cases/${encodeURIComponent(String(caseId))}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resolution_category: cat,
          resolution_summary: sum,
          postmortem_link: resolveLink.trim() || null,
        }),
      });
      // Refresh case detail so status/fields update in UI.
      const d = await request<CaseDetailResponse>(
        `/api/v1/cases/${encodeURIComponent(String(caseId))}?runs_limit=1`
      );
      setCaseDetail(d);
    } catch {
      // no-op; UI remains optimistic
    } finally {
      setResolveSaving(false);
    }
  }

  async function reopenCase() {
    if (!caseId) return;
    setResolveSaving(true);
    try {
      await request(`/api/v1/cases/${encodeURIComponent(String(caseId))}/reopen`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "reopen" }),
      });
      const d = await request<CaseDetailResponse>(
        `/api/v1/cases/${encodeURIComponent(String(caseId))}?runs_limit=1`
      );
      setCaseDetail(d);
    } catch {
      // no-op
    } finally {
      setResolveSaving(false);
    }
  }

  const suggestedActions: Array<{ hypothesis_id: string; action: AnalysisActionProposal }> = [];
  try {
    const hyps = analysisJson?.analysis?.hypotheses || [];
    for (const h of hyps || []) {
      const hid = String(h?.hypothesis_id || "").trim();
      const acts = (h as any)?.proposed_actions as AnalysisActionProposal[] | null | undefined;
      if (!hid || !acts || !Array.isArray(acts)) continue;
      for (const a of acts) {
        suggestedActions.push({ hypothesis_id: hid, action: a });
      }
    }
  } catch {
    // ignore
  }

  async function proposeSuggestedAction(hypothesisId: string, a: AnalysisActionProposal) {
    if (!caseId) return;
    const rid = String(runObj.run_id || "");
    const action_type = String(a.action_type || "").trim();
    const title = String(a.title || "").trim();
    if (!action_type || !title) return;
    setActionsBusy(true);
    try {
      await request(`/api/v1/cases/${encodeURIComponent(String(caseId))}/actions/propose`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: rid || null,
          hypothesis_id: hypothesisId || null,
          action_type,
          title,
          risk: (a.risk || null) as any,
          preconditions: (a.preconditions || []) as any,
          execution_payload: (a.execution_payload || {}) as any,
          actor: (user as any)?.email || null,
        }),
      });
      await refreshCaseActions();
    } finally {
      setActionsBusy(false);
    }
  }

  async function transitionAction(actionId: string, verb: "approve" | "reject" | "execute") {
    if (!caseId) return;
    setActionsBusy(true);
    try {
      await request(
        `/api/v1/cases/${encodeURIComponent(String(caseId))}/actions/${encodeURIComponent(String(actionId))}/${verb}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ actor: (user as any)?.email || null, notes: "" }),
        }
      );
      await refreshCaseActions();
    } finally {
      setActionsBusy(false);
    }
  }

  return (
    <div className={styles.case}>
      <LoginDialog open={!user && !authLoading} />

      {!user || authLoading ? null : (
        <>
          {err ? (
            <div className={styles.errorCard} role="status">
              <div className={styles.errorTitle}>Couldn’t load case</div>
              <div className={styles.errorDetail}>
                {err.status}: {err.message}
              </div>
            </div>
          ) : null}

          <div className={gridCls} aria-busy={loading ? "true" : "false"}>
            <div className={`${reportWrapCls} triageReport`}>
              {showSkeleton ? (
                <div
                  className={`uiCard ${styles.reportCard} ${styles.reportCardLoading}`}
                  aria-label="Loading triage report"
                >
                  <div className={styles.reportHeader}>
                    <div className={styles.reportHeaderLeft}>
                      <div className={styles.reportMetaRow}>
                        <span className={`${styles.skel} ${styles.skelPill}`} />
                        <span className={`${styles.skel} ${styles.skelPill}`} />
                        <span className={`${styles.skel} ${styles.skelChip}`} />
                        <span className={`${styles.skel} ${styles.skelChip}`} />
                      </div>
                      <div className={`${styles.skel} ${styles.skelTitle}`} />
                      <div className={`${styles.skel} ${styles.skelSubtitle}`} />
                    </div>
                    <div className={styles.reportHeaderRight}>
                      <span className={`${styles.skel} ${styles.skelBtn}`} />
                    </div>
                  </div>

                  <div className={styles.reportBody}>
                    {["Triage", "Evidence", "Verdict", "Scores"].map((k) => (
                      <section key={k} className={styles.section}>
                        <div className={styles.sectionTitle}>
                          <span className={`${styles.skel} ${styles.skelIcon}`} />
                          <span className={`${styles.skel} ${styles.skelSectionTitle}`} />
                        </div>
                        <div className={`${styles.skel} ${styles.skelBlock}`} />
                      </section>
                    ))}
                  </div>
                </div>
              ) : (
                <div
                  className={`uiCard ${styles.reportCard} ${styles.reportCardReady}`}
                  data-testid="triage-report-card"
                  aria-label="Triage report card"
                >
                  <div className={styles.reportHeader}>
                    <div className={styles.reportHeaderLeft}>
                      <div className={styles.reportMetaRow}>
                        <ClassificationPill classification={classification} />
                        <SeverityPill severity={severity} />
                        <span className={styles.metaText}>Age: {age}</span>
                        <span className={styles.metaText}>
                          ID: {shortId(String(caseId || ""), 7)}
                        </span>
                      </div>
                      <div className={styles.reportTitleRow}>
                        <div className={styles.reportTitleLine}>
                          <div className={styles.reportTitle}>
                            Triage Report: {alertname || title}
                          </div>
                          {teamLabel ? <span className={styles.teamBadge}>{teamLabel}</span> : null}
                        </div>
                      </div>
                      <div className={styles.reportSubtitle}>
                        {oneLiner ? (
                          <span className={styles.subtitleText}>{oneLiner}</span>
                        ) : (
                          <span className={styles.subtitleMuted}>—</span>
                        )}
                      </div>
                    </div>

                    <div className={styles.reportHeaderRight}>
                      <button
                        className={`uiBtn ${styles.printBtn}`}
                        type="button"
                        onClick={() => window.print()}
                      >
                        <MaterialIcon name="print" />
                        <span>Print</span>
                      </button>
                    </div>
                  </div>

                  <div className={styles.reportBody}>
                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="medical_services" />
                        <span>Triage</span>
                      </div>

                      <div className={styles.callout}>
                        <div className={styles.calloutIcon}>
                          <MaterialIcon name="info" filled />
                        </div>
                        <div>
                          <div className={styles.calloutTitle}>Summary</div>
                          <div className={styles.calloutText}>
                            {decision?.label ? (
                              <span>{String(decision.label)}</span>
                            ) : reportText ? (
                              <span className={styles.calloutMuted}>
                                Summary unavailable in structured data.
                              </span>
                            ) : (
                              <span className={styles.calloutMuted}>No report available yet.</span>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className={styles.metaGrid}>
                        <div>
                          <div className={styles.metaLabel}>Impact Level</div>
                          <div className={styles.metaValue}>
                            {impactLabel(runObj.impact_score ?? null)}
                          </div>
                        </div>
                        <div>
                          <div className={styles.metaLabel}>Affected Components</div>
                          <div className={styles.metaValue}>{affected}</div>
                        </div>
                      </div>
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="visibility" />
                        <span>Evidence</span>
                      </div>

                      <div className={styles.evidenceStack}>
                        {promql.map((q) => (
                          <div key={q.name} className={styles.codeBox}>
                            <div className={styles.codeBoxHead}>
                              <span className={styles.codeBoxName}>{q.name}</span>
                              <span className={styles.codeBoxSource}>Prometheus</span>
                            </div>
                            <pre className={styles.codeBoxBody}>
                              <code>{q.query}</code>
                            </pre>
                          </div>
                        ))}

                        <ul className={styles.evidenceList}>
                          {runObj.created_at ? (
                            <li>
                              <strong>Run created:</strong>{" "}
                              <span className="mono">{String(runObj.created_at)}</span>
                            </li>
                          ) : null}
                          {(features as any)?.job_metrics ? (
                            <>
                              {(features as any).job_metrics.exit_code != null ? (
                                <li>
                                  <strong>Exit code:</strong>{" "}
                                  {(features as any).job_metrics.exit_code}
                                  {(features as any).job_metrics.exit_reason
                                    ? ` (${(features as any).job_metrics.exit_reason})`
                                    : ""}
                                </li>
                              ) : null}
                              {(features as any).job_metrics.service_account ? (
                                <li>
                                  <strong>Service account:</strong>{" "}
                                  <span className="mono">
                                    {(features as any).job_metrics.service_account}
                                  </span>
                                </li>
                              ) : null}
                              {(features as any).job_metrics.attempts != null ? (
                                <li>
                                  <strong>Attempts:</strong>{" "}
                                  {(features as any).job_metrics.attempts}
                                  {(features as any).job_metrics.backoff_limit != null
                                    ? ` / ${(features as any).job_metrics.backoff_limit} (backoff limit)`
                                    : ""}
                                </li>
                              ) : null}
                              {(features as any).job_metrics.error_count != null ? (
                                <li>
                                  <strong>Error patterns:</strong>{" "}
                                  {(features as any).job_metrics.error_count} occurrences in logs
                                </li>
                              ) : null}
                            </>
                          ) : (
                            <>
                              {features?.metrics?.cpu_throttle_p95_pct != null ? (
                                <li>
                                  <strong>CPU throttle (p95):</strong>{" "}
                                  {Number(features.metrics.cpu_throttle_p95_pct).toFixed(2)}%
                                </li>
                              ) : null}
                              {features?.metrics?.http_5xx_rate_p95 != null ? (
                                <li>
                                  <strong>HTTP 5xx rate (p95):</strong>{" "}
                                  {Number(features.metrics.http_5xx_rate_p95).toFixed(3)}
                                </li>
                              ) : null}
                            </>
                          )}
                          {features?.logs?.status ? (
                            <li>
                              <strong>Logs:</strong> {String(features.logs.status)}
                              {features.logs.backend ? ` (${String(features.logs.backend)})` : ""}
                            </li>
                          ) : null}
                          {change?.summary ? (
                            <li>
                              <strong>Recent change:</strong> {String(change.summary)}
                            </li>
                          ) : change?.last_change_time ? (
                            <li>
                              <strong>Recent change:</strong> last at{" "}
                              <span className="mono">{String(change.last_change_time)}</span>
                            </li>
                          ) : null}
                          {!promql.length &&
                          !(features as any)?.job_metrics &&
                          !features?.metrics?.cpu_throttle_p95_pct &&
                          !features?.metrics?.http_5xx_rate_p95 &&
                          !features?.logs?.status &&
                          !change?.summary &&
                          !change?.last_change_time ? (
                            <li className={styles.evidenceMuted}>
                              No structured evidence highlights available.
                            </li>
                          ) : null}
                        </ul>
                      </div>
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="gavel" />
                        <span>Verdict</span>
                      </div>

                      <div className={styles.verdictText}>
                        {analysis?.llm?.status === "ok" &&
                        (analysis?.llm?.output?.summary ||
                          analysis?.llm?.output?.likely_root_cause) ? (
                          <>
                            <p>
                              {String(
                                analysis.llm.output?.summary ||
                                  analysis.llm.output?.likely_root_cause
                              )}
                              {analysis?.llm?.output?.confidence != null ? (
                                <span>
                                  {" "}
                                  (LLM confidence: {String(analysis.llm.output.confidence)})
                                </span>
                              ) : null}
                            </p>
                            {verdict?.one_liner ? (
                              <p className={styles.mutedBlock}>
                                Deterministic verdict: {String(verdict.one_liner)}{" "}
                                {verdict?.primary_driver ? (
                                  <span>
                                    Root signal: <code>{String(verdict.primary_driver)}</code>.
                                  </span>
                                ) : null}
                              </p>
                            ) : null}
                          </>
                        ) : verdict?.one_liner ? (
                          <p>
                            {String(verdict.one_liner)}{" "}
                            {verdict?.primary_driver ? (
                              <span>
                                Root signal: <code>{String(verdict.primary_driver)}</code>.
                              </span>
                            ) : null}
                          </p>
                        ) : analysis?.llm?.status && analysis.llm.status !== "ok" ? (
                          <p className={styles.mutedBlock}>
                            LLM enabled but unavailable: <code>{String(analysis.llm.status)}</code>
                            {analysis?.llm?.error ? (
                              <span>
                                {" "}
                                (<code>{String(analysis.llm.error)}</code>)
                              </span>
                            ) : null}
                          </p>
                        ) : reportText ? (
                          <p className={styles.mutedBlock}>
                            Verdict unavailable in structured data.
                          </p>
                        ) : (
                          <p className={styles.mutedBlock}>No report available yet.</p>
                        )}
                      </div>

                      {Array.isArray(verdict?.next_steps) && verdict.next_steps.length ? (
                        <div className={styles.nextSteps}>
                          <div className={styles.nextStepsTitle}>Next steps</div>
                          <div className={styles.nextStepsContent}>
                            <ReactMarkdown components={markdownComponents}>
                              {formatNextSteps(verdict.next_steps)}
                            </ReactMarkdown>
                          </div>
                        </div>
                      ) : null}
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="psychology" />
                        <span>Root Cause Analysis</span>
                      </div>

                      {rca?.status === "ok" ? (
                        <>
                          <div className={styles.rcaStatusBadge + " " + styles.rcaStatusOk}>
                            RCA Available
                          </div>

                          {rca.root_cause ? (
                            <div className={styles.rcaRootCause}>
                              <div className={styles.rcaRootCauseTitle}>Root Cause</div>
                              <div className={styles.rcaRootCauseText}>
                                {String(rca.root_cause)}
                              </div>
                            </div>
                          ) : null}

                          {rca.confidence_0_1 != null ? (
                            <div className={styles.rcaConfidence}>
                              <MaterialIcon name="trending_up" />
                              <span>
                                Confidence: {Math.round(Number(rca.confidence_0_1) * 100)}%
                              </span>
                            </div>
                          ) : null}

                          {Array.isArray(rca.evidence) && rca.evidence.length ? (
                            <div className={styles.rcaSubsection}>
                              <div className={styles.rcaSubsectionTitle}>Evidence</div>
                              <ul className={styles.rcaList}>
                                {rca.evidence.map((e: any, idx: number) => (
                                  <li key={`evidence-${idx}`}>{String(e)}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}

                          {Array.isArray(rca.remediation) && rca.remediation.length ? (
                            <div className={styles.rcaSubsection}>
                              <div className={styles.rcaSubsectionTitle}>Remediation Steps</div>
                              <ul className={styles.rcaList}>
                                {rca.remediation.map((r: any, idx: number) => (
                                  <li key={`remediation-${idx}`}>{String(r)}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}

                          {Array.isArray(rca.unknowns) && rca.unknowns.length ? (
                            <div className={styles.rcaSubsection}>
                              <div className={styles.rcaSubsectionTitle}>
                                Unknowns / Open Questions
                              </div>
                              <ul className={styles.rcaList}>
                                {rca.unknowns.map((u: any, idx: number) => (
                                  <li key={`unknown-${idx}`}>{String(u)}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}

                          {rca.summary ? (
                            <div className={styles.mutedBlock} style={{ marginTop: "12px" }}>
                              {String(rca.summary)}
                            </div>
                          ) : null}
                        </>
                      ) : rca?.status === "blocked" || rca?.status === "unavailable" ? (
                        <div className={styles.mutedBlock}>
                          <div className={styles.rcaStatusBadge + " " + styles.rcaStatusBlocked}>
                            RCA {String(rca.status)}
                          </div>
                          {rca.summary ? (
                            <div style={{ marginTop: "10px" }}>{String(rca.summary)}</div>
                          ) : null}
                        </div>
                      ) : (
                        <div className={styles.mutedBlock}>
                          RCA not yet available for this case.
                        </div>
                      )}
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="score" />
                        <span>Scores</span>
                      </div>

                      <div className={styles.scoresGrid}>
                        <div
                          className={`${styles.scoreCard} ${styles[`scoreCard_${confidenceTone}`]}`}
                        >
                          <div className={styles.scoreLabel}>Confidence Score</div>
                          <div
                            className={`${styles.scoreValue} ${styles[`scoreValue_confidence_${confidenceTone}`]}`}
                          >
                            {runObj.confidence_score != null ? `${runObj.confidence_score}%` : "—"}
                          </div>
                          <div className={styles.scoreHint}>How sure we are in the diagnosis</div>
                        </div>
                        <div className={`${styles.scoreCard} ${styles[`scoreCard_${impactTone}`]}`}>
                          <div className={styles.scoreLabel}>Impact Score</div>
                          <div
                            className={`${styles.scoreValue} ${styles[`scoreValue_impact_${impactTone}`]}`}
                          >
                            {runObj.impact_score != null ? String(runObj.impact_score) : "—"}
                          </div>
                          <div className={styles.scoreHint}>Severity and blast radius proxy</div>
                        </div>
                        <div className={`${styles.scoreCard} ${styles[`scoreCard_${noiseTone}`]}`}>
                          <div className={styles.scoreLabel}>Noise Score</div>
                          <div
                            className={`${styles.scoreValue} ${styles[`scoreValue_noise_${noiseTone}`]}`}
                          >
                            {runObj.noise_score != null ? String(runObj.noise_score) : "—"}
                          </div>
                          <div className={styles.scoreHint}>Higher means less actionable</div>
                        </div>
                      </div>
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="history" />
                        <span>Timeline</span>
                      </div>

                      <div className={styles.timeline}>
                        <div className={styles.timelineItem}>
                          <div className={styles.timelineDot} />
                          <div className={styles.timelineRow}>
                            <div className={styles.timelineTime}>
                              {createdAt ? <span className="mono">{createdAt}</span> : "—"}
                            </div>
                            <div className={styles.timelineText}>Case created</div>
                          </div>
                        </div>
                        <div className={styles.timelineItem}>
                          <div className={`${styles.timelineDot} ${styles.timelineDotMuted}`} />
                          <div className={styles.timelineRow}>
                            <div className={styles.timelineTime}>
                              {runObj.created_at ? (
                                <span className="mono">{String(runObj.created_at)}</span>
                              ) : (
                                "—"
                              )}
                            </div>
                            <div className={styles.timelineText}>Latest run</div>
                          </div>
                        </div>
                        {change?.last_change_time ? (
                          <div className={styles.timelineItem}>
                            <div className={`${styles.timelineDot} ${styles.timelineDotMuted}`} />
                            <div className={styles.timelineRow}>
                              <div className={styles.timelineTime}>
                                <span className="mono">{String(change.last_change_time)}</span>
                              </div>
                              <div className={styles.timelineText}>Recent change</div>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="memory" />
                        <span>Memory</span>
                      </div>

                      {memoryLoading ? (
                        <div className={styles.mutedBlock}>Loading memory…</div>
                      ) : memoryLive && memoryLive.enabled ? (
                        memoryLive.errors && memoryLive.errors.length ? (
                          <div className={styles.mutedBlock}>
                            Memory unavailable: {memoryLive.errors[0]}
                          </div>
                        ) : memoryLive.similar_cases.length || memoryLive.skills.length ? (
                          <div className={styles.memoryMarkdown}>
                            {memoryLive.similar_cases.length ? (
                              <>
                                <div style={{ fontWeight: 900, marginBottom: 6 }}>
                                  Similar cases
                                </div>
                                <ul style={{ margin: "6px 0", paddingLeft: 18 }}>
                                  {memoryLive.similar_cases.slice(0, 5).map((s) => (
                                    <li key={`${s.case_id}-${s.run_id}`}>
                                      <span className="mono">{String(s.case_id).slice(0, 7)}</span>{" "}
                                      · {String(s.one_liner || "n/a")}
                                      {s.resolution_category
                                        ? ` (resolved=${s.resolution_category})`
                                        : ""}
                                      {s.postmortem_link ? (
                                        <>
                                          {" "}
                                          <a
                                            href={String(s.postmortem_link)}
                                            target="_blank"
                                            rel="noreferrer"
                                          >
                                            link
                                          </a>
                                        </>
                                      ) : null}
                                    </li>
                                  ))}
                                </ul>
                              </>
                            ) : null}
                            {memoryLive.skills.length ? (
                              <>
                                <div style={{ fontWeight: 900, marginTop: 12, marginBottom: 6 }}>
                                  Matched skills
                                </div>
                                {memoryLive.skills.slice(0, 5).map((sk) => (
                                  <div
                                    key={`${sk.name}-${sk.version}`}
                                    style={{ marginBottom: 10 }}
                                  >
                                    <div style={{ fontWeight: 800 }}>
                                      {sk.name} (v{sk.version})
                                    </div>
                                    {sk.rendered ? (
                                      <ReactMarkdown components={markdownComponents}>
                                        {String(sk.rendered)}
                                      </ReactMarkdown>
                                    ) : null}
                                  </div>
                                ))}
                              </>
                            ) : null}
                          </div>
                        ) : (
                          <div className={styles.mutedBlock}>No memory matches yet.</div>
                        )
                      ) : memoryMd ? (
                        <div className={styles.memoryMarkdown}>
                          <ReactMarkdown components={markdownComponents}>{memoryMd}</ReactMarkdown>
                        </div>
                      ) : (
                        <div className={styles.mutedBlock}>No memory matches yet.</div>
                      )}
                    </section>

                    {canActions ? (
                      <section className={styles.section}>
                        <div className={styles.sectionTitle}>
                          <MaterialIcon name="verified_user" />
                          <span>Actions</span>
                        </div>
                        <div className={styles.actionsBlock}>
                          <div className={styles.actionsHint}>
                            Suggestions are policy-gated and require approval. Nothing is executed
                            automatically.
                          </div>

                          {suggestedActions.length ? (
                            <div className={styles.actionsSubsection}>
                              <div className={styles.actionsSubTitle}>
                                Suggested (from diagnostics)
                              </div>
                              {suggestedActions.slice(0, 6).map((x, idx) => (
                                <div key={`${x.hypothesis_id}-${idx}`} className={styles.actionRow}>
                                  <div className={styles.actionMain}>
                                    <div className={styles.actionTitle}>
                                      {String(x.action.title || x.action.action_type || "Action")}
                                      {x.action.risk ? (
                                        <span className={styles.actionRisk}>
                                          risk: {String(x.action.risk)}
                                        </span>
                                      ) : null}
                                    </div>
                                    <div className={styles.actionMeta}>
                                      hypothesis: <code>{x.hypothesis_id}</code> · type:{" "}
                                      <code>{String(x.action.action_type || "")}</code>
                                    </div>
                                  </div>
                                  <div className={styles.actionBtns}>
                                    <button
                                      className="uiBtn"
                                      type="button"
                                      disabled={actionsBusy}
                                      onClick={() =>
                                        proposeSuggestedAction(x.hypothesis_id, x.action)
                                      }
                                    >
                                      Propose
                                    </button>
                                  </div>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className={styles.mutedBlock}>
                              No action suggestions for this case.
                            </div>
                          )}

                          <div className={styles.actionsSubsection}>
                            <div className={styles.actionsSubTitle}>Audit trail (case actions)</div>
                            {caseActions.length ? (
                              <div className={styles.actionsList}>
                                {caseActions.slice(0, 12).map((a) => (
                                  <div key={a.action_id} className={styles.actionRow}>
                                    <div className={styles.actionMain}>
                                      <div className={styles.actionTitle}>
                                        {a.title}{" "}
                                        <span className={styles.actionStatus}>{a.status}</span>
                                        {a.risk ? (
                                          <span className={styles.actionRisk}>
                                            risk: {String(a.risk)}
                                          </span>
                                        ) : null}
                                      </div>
                                      <div className={styles.actionMeta}>
                                        type: <code>{a.action_type}</code>
                                        {a.hypothesis_id ? (
                                          <>
                                            {" "}
                                            · hypothesis: <code>{a.hypothesis_id}</code>
                                          </>
                                        ) : null}
                                      </div>
                                    </div>
                                    <div className={styles.actionBtns}>
                                      {a.status === "proposed" ? (
                                        <>
                                          <button
                                            className="uiBtn"
                                            type="button"
                                            disabled={actionsBusy}
                                            onClick={() => transitionAction(a.action_id, "approve")}
                                          >
                                            Approve
                                          </button>
                                          <button
                                            className="uiBtn uiBtnSecondary"
                                            type="button"
                                            disabled={actionsBusy}
                                            onClick={() => transitionAction(a.action_id, "reject")}
                                          >
                                            Reject
                                          </button>
                                        </>
                                      ) : null}
                                      {a.status === "approved" ? (
                                        <button
                                          className="uiBtn"
                                          type="button"
                                          disabled={actionsBusy || !actionCfg?.allow_execute}
                                          title={
                                            actionCfg?.allow_execute
                                              ? ""
                                              : "Execution is disabled by policy"
                                          }
                                          onClick={() => transitionAction(a.action_id, "execute")}
                                        >
                                          Mark executed
                                        </button>
                                      ) : null}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <div className={styles.mutedBlock}>
                                {actionsBusy ? "Loading…" : "No proposed actions yet."}
                              </div>
                            )}
                          </div>
                        </div>
                      </section>
                    ) : null}

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="task_alt" />
                        <span>Resolution</span>
                      </div>
                      {caseObj.status === "closed" ? (
                        <div className={styles.resolutionCard}>
                          <div className={styles.resolutionHeaderRow}>
                            <div className={styles.resolutionLeft}>
                              <span className={styles.resolutionBadge}>
                                <span className={styles.resolutionDot} aria-hidden="true" />
                                Resolved
                              </span>
                              <span className={styles.resolutionCategoryPill}>
                                {String(caseObj.resolution_category || "unknown")}
                              </span>
                              {caseObj.resolved_at ? (
                                <span className={styles.resolutionMeta}>
                                  · {formatAge(String(caseObj.resolved_at))} ago
                                </span>
                              ) : null}
                            </div>
                            <div className={styles.resolutionRight}>
                              {caseObj.postmortem_link ? (
                                <a
                                  className={styles.resolutionLink}
                                  href={String(caseObj.postmortem_link)}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  Postmortem
                                </a>
                              ) : null}
                              <button
                                className="uiBtn uiBtnSecondary"
                                type="button"
                                onClick={reopenCase}
                                disabled={resolveSaving}
                              >
                                Reopen
                              </button>
                            </div>
                          </div>
                          {caseObj.resolution_summary ? (
                            <div className={styles.resolutionSummary}>
                              {String(caseObj.resolution_summary)}
                            </div>
                          ) : (
                            <div className={styles.mutedBlock}>No resolution summary recorded.</div>
                          )}
                        </div>
                      ) : (
                        <div className={styles.resolutionCard}>
                          <div className={styles.resolutionIntro}>
                            <div className={styles.resolutionIntroTitle}>
                              Mark this case resolved
                            </div>
                            <div className={styles.resolutionIntroSub}>
                              This is used to improve similar-case retrieval and hypothesis ranking
                              over time.
                            </div>
                          </div>
                          <div className={styles.resolveForm}>
                            <div className={styles.resolveField}>
                              <label className={styles.resolveLabel}>Category</label>
                              <select
                                className={styles.resolveSelect}
                                value={resolveCategory}
                                onChange={(e) => setResolveCategory(e.target.value)}
                              >
                                {[
                                  "deploy",
                                  "dependency",
                                  "capacity",
                                  "config",
                                  "k8s_rollout",
                                  "node",
                                  "unknown",
                                ].map((x) => (
                                  <option key={x} value={x}>
                                    {x}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className={styles.resolveField}>
                              <label className={styles.resolveLabel}>Summary</label>
                              <input
                                className={styles.resolveInput}
                                value={resolveSummary}
                                onChange={(e) => setResolveSummary(e.target.value)}
                                placeholder="What fixed it? (short)"
                              />
                            </div>
                            <div className={styles.resolveField}>
                              <label className={styles.resolveLabel}>Link</label>
                              <input
                                className={styles.resolveInput}
                                value={resolveLink}
                                onChange={(e) => setResolveLink(e.target.value)}
                                placeholder="Postmortem / PR / ArgoCD link (optional)"
                              />
                            </div>
                            <div className={styles.resolveActions}>
                              <button
                                className="uiBtn"
                                type="button"
                                onClick={markResolved}
                                disabled={resolveSaving || !resolveSummary.trim()}
                              >
                                {resolveSaving ? "Saving…" : "Mark resolved"}
                              </button>
                            </div>
                          </div>
                        </div>
                      )}
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="dataset" />
                        <span>Enrichment</span>
                      </div>

                      {enrichment?.label ? (
                        <div className={styles.enrichment}>
                          <div className={styles.enrichmentSummary}>
                            <strong>Summary:</strong> {String(enrichment.label)}
                          </div>

                          {Array.isArray(enrichment.why) && enrichment.why.length ? (
                            <div className={styles.enrichmentBlock}>
                              <div className={styles.enrichmentTitle}>Why</div>
                              <ul className={styles.enrichmentList}>
                                {enrichment.why.slice(0, 8).map((s: any, idx: number) => (
                                  <li key={`${idx}-${String(s).slice(0, 24)}`}>{String(s)}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}

                          {Array.isArray(enrichment.next) && enrichment.next.length ? (
                            <div className={styles.enrichmentBlock}>
                              <div className={styles.enrichmentTitle}>On-call next</div>
                              <ul className={styles.enrichmentList}>
                                {enrichment.next.slice(0, 8).map((s: any, idx: number) => (
                                  <li key={`${idx}-${String(s).slice(0, 24)}`}>{String(s)}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                        </div>
                      ) : (
                        <div className={styles.mutedBlock}>No enrichment available.</div>
                      )}
                    </section>

                    <section className={styles.section}>
                      <div className={styles.sectionTitle}>
                        <MaterialIcon name="description" />
                        <span>Raw report</span>
                      </div>
                      <details className={styles.rawDetails}>
                        <summary className={styles.rawSummary}>Show raw Markdown report</summary>
                        <div className={styles.markdown}>
                          {reportText ? (
                            <ReactMarkdown components={markdownComponents}>
                              {reportText}
                            </ReactMarkdown>
                          ) : (
                            <div className={styles.empty}>No report text available yet.</div>
                          )}
                        </div>
                      </details>
                    </section>
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
