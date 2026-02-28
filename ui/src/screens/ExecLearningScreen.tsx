import React from "react";
import { useNavigate } from "react-router-dom";
import { useApi, ApiError } from "../lib/api";
import type { ExecLearningResponse } from "../lib/types";
import { useAuth } from "../state/auth";
import { LoginDialog } from "../ui/LoginDialog";
import { Card } from "../ui/Card";
import styles from "./ExecLearningScreen.module.css";

function fmtInt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return String(Math.round(n));
}

export function ExecLearningScreen() {
  const nav = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const { request } = useApi();

  const [data, setData] = React.useState<ExecLearningResponse | null>(null);
  const [err, setErr] = React.useState<ApiError | null>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (!user) return;
    let cancelled = false;
    setLoading(true);
    setErr(null);
    request<ExecLearningResponse>("/api/v1/exec/learning?days=30&top_n=10")
      .then((d) => {
        if (cancelled) return;
        setData(d);
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(e as ApiError);
        setData(null);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user, request]);

  const skillsByStatus: Record<string, number | null> = data?.skills_by_status ?? {};
  const feedbackByWeek: Record<string, Record<string, number | null>> = data?.feedback_by_week ??
  {};
  const weeks = Object.keys(feedbackByWeek).sort();
  const outcomes = Array.from(
    new Set(
      weeks
        .flatMap((w) => Object.keys(feedbackByWeek[w] || {}))
        .map((x) => String(x || "unknown").toLowerCase())
    )
  ).sort();

  return (
    <div className={styles.wrap} aria-busy={loading ? "true" : "false"}>
      <LoginDialog open={!user && !authLoading} />

      <div className={styles.headerRow}>
        <div>
          <h1>Learning loop</h1>
          <div className={styles.subtitle}>
            Skills inventory + human feedback outcomes (read-only).
          </div>
        </div>
        <button className="uiBtn uiBtnGhost" type="button" onClick={() => nav("/exec")}>
          ← Back to dashboard
        </button>
      </div>

      {err ? (
        <Card title="Couldn’t load learning metrics">
          <div className={styles.muted}>
            {err.status}: {err.message}
          </div>
        </Card>
      ) : null}

      <div className={styles.grid}>
        <Card title="Skills by status" className={styles.col4}>
          <div className={styles.kpiRow}>
            {Object.keys(skillsByStatus).length ? (
              Object.entries(skillsByStatus)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([k, v]) => (
                  <span key={k} className={`${styles.pill} ${styles.pillMuted}`}>
                    {k}: {fmtInt(v)}
                  </span>
                ))
            ) : (
              <span className={styles.muted}>No skills found.</span>
            )}
          </div>
        </Card>

        <Card title="Feedback outcomes by week" className={styles.col8}>
          {!weeks.length ? (
            <div className={styles.muted}>No feedback in the selected window.</div>
          ) : (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.th}>Week</th>
                  {outcomes.map((o) => (
                    <th key={o} className={styles.th}>
                      {o}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {weeks.map((w) => {
                  const row = feedbackByWeek[w] || {};
                  return (
                    <tr key={w}>
                      <td className={`${styles.td} ${styles.mono}`}>{w}</td>
                      {outcomes.map((o) => (
                        <td key={`${w}-${o}`} className={styles.td}>
                          {fmtInt(row[o] ?? 0)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Top skills by feedback (30d)" className={styles.col12}>
          {data?.top_skills_by_feedback?.length ? (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.th}>Skill</th>
                  <th className={styles.th}>Status</th>
                  <th className={styles.th}>Version</th>
                  <th className={styles.th}>Feedback</th>
                </tr>
              </thead>
              <tbody>
                {data.top_skills_by_feedback.map((s) => (
                  <tr
                    key={s.skill_id}
                    className={styles.rowBtn}
                    title="(Read-only) Skill detail is not implemented yet"
                  >
                    <td className={styles.td}>
                      <span className={styles.mono}>{s.name}</span>
                    </td>
                    <td className={styles.td}>{s.status}</td>
                    <td className={styles.td}>{fmtInt(s.version)}</td>
                    <td className={styles.td}>{fmtInt(s.feedback_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className={styles.muted}>No skill feedback in the selected window.</div>
          )}
        </Card>
      </div>
    </div>
  );
}
