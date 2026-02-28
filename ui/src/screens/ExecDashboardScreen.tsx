import React from "react";
import { useNavigate } from "react-router-dom";
import { useApi, ApiError, ENABLE_LEARNING_LOOP } from "../lib/api";
import type {
  ExecOverviewResponse,
  ExecTrendDay,
  ExecTopTeam,
  ExecTopDriver,
  ExecTopIncident,
} from "../lib/types";
import { useAuth } from "../state/auth";
import { LoginDialog } from "../ui/LoginDialog";
import { Card } from "../ui/Card";
import { formatAge } from "../lib/format";
import styles from "./ExecDashboardScreen.module.css";

function fmtInt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return String(Math.round(n));
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n)}%`;
}

function fmtSeconds(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const s = Math.max(0, Math.round(n));
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  return `${h}h`;
}

function ellipsizeMiddle(value: string, maxLen: number): string {
  const s = String(value || "");
  if (s.length <= maxLen) return s;
  const keep = Math.max(6, Math.floor((maxLen - 1) / 2));
  const a = s.slice(0, keep);
  const b = s.slice(-keep);
  return `${a}…${b}`;
}

function MiniBars({ days, kind }: { days: ExecTrendDay[]; kind: "volume" | "impact" }) {
  const vals = (days || []).map((d) =>
    kind === "volume" ? d.incidents_created : (d.impact_median ?? 0)
  );
  const max = Math.max(1, ...vals);
  return (
    <div className={styles.miniBars} aria-hidden="true">
      {vals.slice(-14).map((v, idx) => {
        const h = Math.max(6, Math.round((v / max) * 52));
        const muted = v === 0;
        return (
          <div
            key={`${idx}-${v}`}
            className={`${styles.bar} ${muted ? styles.barMuted : ""}`}
            style={{ height: `${h}px` }}
          />
        );
      })}
    </div>
  );
}

export function ExecDashboardScreen() {
  const nav = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const { request } = useApi();

  const [data, setData] = React.useState<ExecOverviewResponse | null>(null);
  const [err, setErr] = React.useState<ApiError | null>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (!user) return;
    let cancelled = false;
    setLoading(true);
    setErr(null);
    request<ExecOverviewResponse>(
      "/api/v1/exec/overview?days=30&top_n=5&stale_minutes=60&high_impact_threshold=85"
    )
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

  const risk = data?.risk;
  const trends = data?.trends?.daily || [];
  const focusTeams: ExecTopTeam[] = data?.focus?.top_teams || [];
  const focusDrivers: ExecTopDriver[] = data?.focus?.top_drivers || [];
  const topActive: ExecTopIncident[] = risk?.top_active || [];
  const recurrenceTop = data?.recurrence?.top ?? [];

  const oldestAge = risk?.oldest_active_created_at ? formatAge(risk.oldest_active_created_at) : "—";

  return (
    <div className={styles.wrap}>
      <LoginDialog open={!user && !authLoading} />

      <div className={styles.headerRow}>
        <div className={styles.title}>
          <h1>Leadership Dashboard</h1>
          <div className={styles.subtitle}>
            Read-only overview: risk now, trends, focus, and AI effectiveness.
          </div>
        </div>
        <div className={styles.actions}>
          <button
            className={`uiBtn ${styles.actionBtn}`}
            type="button"
            disabled
            title="Coming soon"
          >
            <span className={`material-symbols-outlined ${styles.actionIcon}`}>calendar_today</span>
            <span>Last 30 Days</span>
            <span className={`material-symbols-outlined ${styles.actionChevron}`}>expand_more</span>
          </button>
          <button
            className={`uiBtn ${styles.actionBtn}`}
            type="button"
            disabled
            title="Coming soon"
          >
            <span className={`material-symbols-outlined ${styles.actionIcon}`}>filter_alt</span>
            <span>Team: All</span>
            <span className={`material-symbols-outlined ${styles.actionChevron}`}>expand_more</span>
          </button>
          <button
            className={`uiBtn uiBtnPrimary ${styles.actionBtn}`}
            type="button"
            disabled
            title="Coming soon (CSV export)"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
              download
            </span>
            <span>Export Report</span>
          </button>
          {ENABLE_LEARNING_LOOP ? (
            <button
              className="uiBtn uiBtnGhost"
              type="button"
              onClick={() => nav("/exec/learning")}
            >
              Learning loop →
            </button>
          ) : null}
          <button
            className="uiBtn"
            type="button"
            onClick={() => {
              // simple refresh: re-run effect by clearing data
              setData(null);
              setErr(null);
              setLoading(true);
              request<ExecOverviewResponse>(
                "/api/v1/exec/overview?days=30&top_n=5&stale_minutes=60&high_impact_threshold=85"
              )
                .then((d) => setData(d))
                .catch((e) => setErr(e as ApiError))
                .finally(() => setLoading(false));
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {err ? (
        <Card title="Couldn’t load CTO dashboard">
          <div className={styles.muted}>
            {err.status}: {err.message}
          </div>
        </Card>
      ) : null}

      <div className={styles.grid} aria-busy={loading ? "true" : "false"}>
        <Card title="Active incidents" className={styles.col4}>
          <div className={styles.kpi}>
            <div className={styles.kpiValue}>{risk ? fmtInt(risk.active_count) : "—"}</div>
            <div className={styles.kpiLabel}>Open + ack + in progress</div>
            <div className={styles.kpiMetaRow}>
              <span className={styles.pill}>
                High impact: {risk ? fmtInt(risk.active_high_impact_count) : "—"}
              </span>
              <span className={styles.pill}>Oldest: {oldestAge}</span>
              <span
                className={styles.pill}
                title="Active incidents where the latest run is older than the stale threshold"
              >
                Stale: {risk ? fmtInt(risk.stale_investigation_count) : "—"}
              </span>
            </div>
          </div>
        </Card>

        <Card title="Trend: incidents created (14d)" className={styles.col4}>
          <div className={styles.kpiLabel}>New incidents per day.</div>
          <MiniBars days={trends} kind="volume" />
        </Card>

        <Card title="Trend: median impact (14d)" className={styles.col4}>
          <div className={styles.kpiLabel}>Median impact per day.</div>
          <MiniBars days={trends} kind="impact" />
        </Card>

        <Card title="Top active (by impact)" className={styles.col6}>
          <div className={styles.list}>
            {!topActive.length ? (
              <div className={styles.muted}>No active incidents in the current window.</div>
            ) : null}
            {topActive.map((it) => {
              const title = it.one_liner || it.alertname || "Incident";
              const subBits = [
                it.team ? `team:${String(it.team).trim().toLowerCase()}` : null,
                it.service ? `svc:${it.service}` : null,
                it.family ? `fam:${it.family}` : null,
              ].filter(Boolean);
              const shownBits = subBits.slice(0, 2);
              return (
                <div
                  key={it.incident_id}
                  className={styles.listItem}
                  onClick={() => nav(`/incidents/${encodeURIComponent(it.incident_id)}`)}
                >
                  <div className={styles.liLeft}>
                    <div className={styles.liTitle}>{title}</div>
                    <div className={styles.liSub}>
                      <span>Age: {it.created_at ? formatAge(it.created_at) : "—"}</span>
                      {shownBits.length ? <span>•</span> : null}
                      {shownBits.map((b) => (
                        <span key={b}>{b}</span>
                      ))}
                      {subBits.length > shownBits.length ? (
                        <span title={subBits.join(" • ")}>…</span>
                      ) : null}
                    </div>
                  </div>
                  <div className={styles.liRight}>
                    <div className={styles.score}>Impact {fmtInt(it.impact_score)}</div>
                    <div className={styles.muted}>Conf {fmtInt(it.confidence_score)}%</div>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card title="Focus: top teams (active)" className={styles.col6}>
          <div className={styles.list}>
            {!focusTeams.length ? (
              <div className={styles.muted}>No team data on latest runs.</div>
            ) : null}
            {focusTeams.map((t) => (
              <div
                key={t.team}
                className={styles.listItem}
                onClick={() => nav(`/inbox?team=${encodeURIComponent(t.team)}`)}
                title="Open inbox filtered by team"
              >
                <div className={styles.liLeft}>
                  <div className={styles.liTitle}>{t.team}</div>
                  <div className={styles.liSub}>
                    <span>Active: {t.active_count}</span>
                    <span>•</span>
                    <span>High impact: {t.high_impact_count}</span>
                  </div>
                </div>
                <div className={styles.liRight}>
                  <div className={styles.score}>{t.total_impact}</div>
                  <div className={styles.muted}>Impact sum</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Focus: top drivers (active)" className={styles.col6}>
          <div className={styles.list}>
            {!focusDrivers.length ? (
              <div className={styles.muted}>No driver/family data on latest runs.</div>
            ) : null}
            {focusDrivers.map((d) => (
              <div
                key={d.driver}
                className={styles.listItem}
                onClick={() => nav(`/inbox?q=${encodeURIComponent(d.driver)}`)}
                title="Open inbox search for this driver"
              >
                <div className={styles.liLeft}>
                  <div className={styles.liTitle}>{d.driver}</div>
                  <div className={styles.liSub}>
                    <span>Active: {d.active_count}</span>
                    <span>•</span>
                    <span>High impact: {d.high_impact_count}</span>
                  </div>
                </div>
                <div className={styles.liRight}>
                  <div className={styles.score}>{d.total_impact}</div>
                  <div className={styles.muted}>Impact sum</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Recurrence (proxy)" className={styles.col6}>
          <div className={styles.kpi}>
            <div className={styles.kpiValue}>
              {data ? fmtPct((data.recurrence?.rate || 0) * 100) : "—"}
            </div>
            <div className={styles.kpiLabel}>
              Share of incidents that repeat by incident key (window).
            </div>
            <div className={styles.list}>
              {recurrenceTop.length ? (
                recurrenceTop.map((r) => (
                  <div
                    key={r.incident_key}
                    className={styles.listItem}
                    onClick={() => nav(`/inbox?q=${encodeURIComponent(r.incident_key)}`)}
                    title="Open inbox search for this incident_key"
                  >
                    <div className={styles.liLeft}>
                      <div className={styles.liTitle} title={r.incident_key}>
                        {ellipsizeMiddle(r.incident_key, 46)}
                      </div>
                      <div className={styles.liSub}>
                        <span>{r.count} occurrences</span>
                      </div>
                    </div>
                    <div className={styles.liRight}>
                      <div className={styles.score}>{r.count}</div>
                    </div>
                  </div>
                ))
              ) : (
                <div className={styles.muted}>No recurring incident_key in the current window.</div>
              )}
            </div>
          </div>
        </Card>

        <Card title="AI effectiveness" className={styles.col12}>
          <div className={styles.rowSplit}>
            <div>
              <div className={styles.kpiValue}>
                {fmtSeconds(data?.ai?.ttfa_median_seconds ?? null)}
              </div>
              <div className={styles.kpiLabel}>TTFA median</div>
            </div>
            <div>
              <div className={styles.kpiValue}>
                {fmtSeconds(data?.ai?.ttfa_p90_seconds ?? null)}
              </div>
              <div className={styles.kpiLabel}>TTFA p90</div>
            </div>
            <div>
              <div className={styles.kpiValue}>
                {fmtPct(data?.ai?.confidence_ge_70_pct ?? null)}
              </div>
              <div className={styles.kpiLabel}>Confidence ≥ 70%</div>
            </div>
          </div>
          <div className={styles.kpiMetaRow} style={{ marginTop: 10 }}>
            <span className={styles.pill}>
              Missing one-liner: {fmtPct(data?.ai?.gaps_pct?.missing_one_liner ?? 0)}
            </span>
            <span className={styles.pill}>
              Missing team: {fmtPct(data?.ai?.gaps_pct?.missing_team ?? 0)}
            </span>
            <span className={styles.pill}>
              Missing family: {fmtPct(data?.ai?.gaps_pct?.missing_family ?? 0)}
            </span>
          </div>
        </Card>
      </div>
    </div>
  );
}
