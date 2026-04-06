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

// ── Inline types for new backend sections (not yet in types.ts) ────────────
type ExecMttrWeek = {
  week: string;
  mttr_hours_median: number | null;
  resolved_count: number;
};
type ExecServiceRow = {
  service: string;
  incident_count: number;
  unique_alert_types: number;
  median_impact: number | null;
  change_correlated_count: number;
};
type ExecCostDay = { day: string; cost_usd: number };
type ExecSignal = {
  total_runs?: number | null;
  actionable?: number | null;
  noisy?: number | null;
  informational?: number | null;
  unclassified?: number | null;
  actionable_pct?: number | null;
  change_correlated_count?: number | null;
  change_correlated_pct?: number | null;
};
type ExecSavings = {
  total_runs?: number | null;
  high_conf_runs?: number | null;
  low_conf_runs?: number | null;
  actionable_runs?: number | null;
  deflected_runs?: number | null;
  hours_saved?: number | null;
  cost_saved_usd?: number | null;
  triage_minutes_assumed?: number | null;
  hourly_rate_usd_assumed?: number | null;
};
type ExecCost = {
  total_usd?: number | null;
  avg_per_run_usd?: number | null;
  total_runs?: number | null;
  daily?: ExecCostDay[] | null;
};

// ── Format helpers ─────────────────────────────────────────────────────────
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
  return `${Math.round(m / 60)}h`;
}

function fmtAge(d: Date): string {
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.round(m / 60)}h ago`;
}

function fmtCostUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function ellipsizeMiddle(value: string, maxLen: number): string {
  const s = String(value || "");
  if (s.length <= maxLen) return s;
  const keep = Math.max(6, Math.floor((maxLen - 1) / 2));
  return `${s.slice(0, keep)}…${s.slice(-keep)}`;
}

// ── Info tooltip ──────────────────────────────────────────────────────────
function InfoTip({ children }: { children: React.ReactNode }) {
  return (
    <span className={styles.infoTip} role="img" aria-label="More info">
      i<span className={styles.infoTipBubble}>{children}</span>
    </span>
  );
}

// ── Sparkline components ───────────────────────────────────────────────────
type BarVariant = "blue" | "amber" | "green";

function variantClass(variant: BarVariant) {
  if (variant === "amber") return styles.barAmber;
  if (variant === "green") return styles.barGreen;
  return ""; // blue is default
}

function MiniBars({
  days,
  kind,
  variant = "blue",
}: {
  days: ExecTrendDay[];
  kind: "volume" | "impact";
  variant?: BarVariant;
}) {
  const vals = (days || []).map((d) =>
    kind === "volume" ? d.incidents_created : (d.impact_median ?? 0)
  );
  const max = Math.max(1, ...vals);
  const sliced = vals.slice(-14);
  return (
    <div className={styles.miniBars} aria-hidden="true">
      {sliced.map((v, idx) => {
        const h = Math.max(6, Math.round((v / max) * 60));
        const muted = v === 0;
        return (
          <div
            key={`${idx}-${v}`}
            className={`${styles.bar} ${muted ? styles.barMuted : variantClass(variant)}`}
            style={
              {
                height: `${h}px`,
                "--bar-delay": `${idx * 22}ms`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}

function CostBars({ daily }: { daily: ExecCostDay[] }) {
  const vals = (daily || []).map((d) => d.cost_usd ?? 0);
  const max = Math.max(0.000001, ...vals);
  const sliced = vals.slice(-14);
  return (
    <div className={styles.miniBars} aria-hidden="true">
      {sliced.map((v, i) => {
        const h = Math.max(6, Math.round((v / max) * 60));
        return (
          <div
            key={i}
            className={`${styles.bar} ${v === 0 ? styles.barMuted : styles.barGreen}`}
            style={
              {
                height: `${h}px`,
                "--bar-delay": `${i * 22}ms`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}

function MttrBars({ weekly }: { weekly: ExecMttrWeek[] }) {
  const vals = (weekly || []).map((w) => w.mttr_hours_median ?? 0);
  const max = Math.max(0.001, ...vals);
  const sliced = vals.slice(-12);
  return (
    <div className={styles.miniBars} aria-hidden="true">
      {sliced.map((v, i) => {
        const h = Math.max(6, Math.round((v / max) * 60));
        return (
          <div
            key={i}
            className={`${styles.bar} ${v === 0 ? styles.barMuted : styles.barAmber}`}
            style={
              {
                height: `${h}px`,
                "--bar-delay": `${i * 28}ms`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}

// ── Constants ──────────────────────────────────────────────────────────────
const TIME_RANGE_OPTIONS = [
  { label: "Last 7 days", days: 7 },
  { label: "Last 14 days", days: 14 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
];

// ── Main component ─────────────────────────────────────────────────────────
export function ExecDashboardScreen() {
  const nav = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const { request } = useApi();

  const [data, setData] = React.useState<ExecOverviewResponse | null>(null);
  const [err, setErr] = React.useState<ApiError | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [days, setDays] = React.useState(30);
  const [rangeOpen, setRangeOpen] = React.useState(false);
  const [exportOpen, setExportOpen] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [lastFetched, setLastFetched] = React.useState<Date | null>(null);
  const [, setTick] = React.useState(0);
  const rangeRef = React.useRef<HTMLDivElement>(null);
  const exportRef = React.useRef<HTMLDivElement>(null);

  // Close dropdowns on Escape or outside click
  React.useEffect(() => {
    if (!rangeOpen && !exportOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setRangeOpen(false);
        setExportOpen(false);
      }
    }
    function onPointer(e: PointerEvent) {
      const t = e.target as Node | null;
      if (rangeOpen && rangeRef.current && !rangeRef.current.contains(t)) setRangeOpen(false);
      if (exportOpen && exportRef.current && !exportRef.current.contains(t)) setExportOpen(false);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onPointer, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onPointer, true);
    };
  }, [rangeOpen, exportOpen]);

  // Re-render every 30s so the "X ago" label stays accurate
  React.useEffect(() => {
    if (!lastFetched) return;
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, [lastFetched]);

  const fetchData = React.useCallback(() => {
    if (!user) return;
    setLoading(true);
    setErr(null);
    request<ExecOverviewResponse>(
      `/api/v1/exec/overview?days=${days}&top_n=5&stale_minutes=60&high_impact_threshold=85`
    )
      .then((d) => {
        setData(d);
        setLastFetched(new Date());
      })
      .catch((e) => {
        setErr(e as ApiError);
        setData(null);
      })
      .finally(() => setLoading(false));
  }, [user, request, days]);

  React.useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function handleExport(fmt: "csv" | "xlsx" | "json") {
    setExporting(true);
    setExportOpen(false);
    try {
      const res = await fetch(`/api/v1/exec/export?days=${days}&fmt=${fmt}`, {
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `tarka-${days}d.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error("Export failed", e);
    } finally {
      setExporting(false);
    }
  }

  // ── Destructure ────────────────────────────────────────────────────────
  const risk = data?.risk;
  const trends = data?.trends?.daily || [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mttrWeekly: ExecMttrWeek[] = (data?.trends as any)?.mttr_weekly || [];
  const focusTeams: ExecTopTeam[] = data?.focus?.top_teams || [];
  const focusDrivers: ExecTopDriver[] = data?.focus?.top_drivers || [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const topServices: ExecServiceRow[] = (data?.focus as any)?.top_services || [];
  const topActive: ExecTopIncident[] = risk?.top_active || [];
  const recurrenceTop = data?.recurrence?.top ?? [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const signal: ExecSignal = (data as any)?.signal ?? {};
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const savings: ExecSavings = (data as any)?.savings ?? {};
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cost: ExecCost = (data as any)?.cost ?? {};

  const oldestAge = risk?.oldest_active_created_at ? formatAge(risk.oldest_active_created_at) : "—";
  const latestMttr =
    mttrWeekly.length > 0 ? mttrWeekly[mttrWeekly.length - 1]?.mttr_hours_median : null;
  const totalResolved = mttrWeekly.reduce((s, w) => s + w.resolved_count, 0);
  const rangeLabel = TIME_RANGE_OPTIONS.find((o) => o.days === days)?.label ?? "Last 30 days";

  return (
    <div className={styles.wrap}>
      <LoginDialog open={!user && !authLoading} />

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className={styles.headerRow}>
        <div className={styles.title}>
          <h1>Leadership Dashboard</h1>
          <div className={styles.subtitle}>
            Read-only overview · incident risk, trends, signal quality, and ROI.
          </div>
        </div>
        <div className={styles.actions}>
          {/* Time range dropdown */}
          <div className={styles.dropWrap} ref={rangeRef}>
            <button
              className={`uiBtn ${styles.actionBtn}`}
              type="button"
              onClick={() => {
                setRangeOpen((v) => !v);
                setExportOpen(false);
              }}
              aria-expanded={rangeOpen}
            >
              <span className={`material-symbols-outlined ${styles.actionIcon}`}>
                calendar_today
              </span>
              <span>{rangeLabel}</span>
              <span className={`material-symbols-outlined ${styles.actionChevron}`}>
                expand_more
              </span>
            </button>
            {rangeOpen && (
              <div className={styles.dropPanel}>
                {TIME_RANGE_OPTIONS.map((o) => (
                  <button
                    key={o.days}
                    className={`${styles.dropItem} ${o.days === days ? styles.dropItemActive : ""}`}
                    type="button"
                    onClick={() => {
                      setDays(o.days);
                      setRangeOpen(false);
                    }}
                  >
                    {o.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Export dropdown */}
          <div className={styles.dropWrap} ref={exportRef}>
            <button
              className={`uiBtn ${styles.actionBtn}`}
              type="button"
              disabled={exporting}
              onClick={() => {
                setExportOpen((v) => !v);
                setRangeOpen(false);
              }}
              aria-expanded={exportOpen}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
                download
              </span>
              <span>{exporting ? "Exporting…" : "Export Report"}</span>
              <span className={`material-symbols-outlined ${styles.actionChevron}`}>
                expand_more
              </span>
            </button>
            {exportOpen && (
              <div className={styles.dropPanel}>
                {(["csv", "xlsx", "json"] as const).map((fmt) => (
                  <button
                    key={fmt}
                    className={styles.dropItem}
                    type="button"
                    onClick={() => handleExport(fmt)}
                  >
                    {fmt.toUpperCase()}
                  </button>
                ))}
              </div>
            )}
          </div>

          {ENABLE_LEARNING_LOOP && (
            <button
              className="uiBtn uiBtnGhost"
              type="button"
              onClick={() => nav("/exec/learning")}
            >
              Learning loop →
            </button>
          )}

          <button className="uiBtn" type="button" onClick={fetchData} disabled={loading}>
            {loading ? "Loading…" : lastFetched ? `Refresh · ${fmtAge(lastFetched)}` : "Refresh"}
          </button>
        </div>
      </div>

      {err && (
        <Card title="Couldn't load dashboard">
          <div className={styles.muted}>
            {err.status}: {err.message}
          </div>
        </Card>
      )}

      <div className={styles.grid} aria-busy={loading ? "true" : "false"}>
        {/* ── Row 1: Hero — engineer hours saved ────────────────────────── */}
        <Card
          title={
            <>
              Engineer hours saved by automated triage
              <InfoTip>
                Every alert Tarka investigated saved an engineer from manually triaging it. Hours =
                deflected alerts × assumed triage time per alert. Adjust rate assumptions in
                settings.
              </InfoTip>
            </>
          }
          className={`${styles.col12} ${styles.heroCard}`}
        >
          <div className={styles.heroBody}>
            <div className={styles.heroLeft}>
              <div className={styles.heroNumber}>
                {savings.hours_saved != null ? savings.hours_saved.toFixed(1) : "—"}
                <span className={styles.heroUnit}> hrs saved</span>
              </div>
              <div className={styles.heroSub}>
                based on {fmtInt(savings.total_runs)} automated triages
                {savings.triage_minutes_assumed != null
                  ? ` · ${savings.triage_minutes_assumed}min assumed per manual triage`
                  : ""}
              </div>
              {savings.cost_saved_usd != null && cost.total_usd != null && cost.total_usd > 0 && (
                <div className={styles.heroRoi}>
                  <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                    trending_up
                  </span>
                  ${Math.round(savings.cost_saved_usd / cost.total_usd).toLocaleString()} saved
                  <span className={styles.heroRoiMuted}>per $1 of AI spend</span>
                </div>
              )}
            </div>
          </div>

          <div className={styles.heroGrid}>
            <div className={styles.heroTile}>
              <div className={styles.heroTileVal}>
                {savings.cost_saved_usd != null
                  ? `$${Math.round(savings.cost_saved_usd).toLocaleString()}`
                  : "—"}
              </div>
              <div className={styles.heroTileLabel}>Estimated cost saved</div>
            </div>
            <div className={styles.heroTile}>
              <div className={styles.heroTileVal}>{fmtInt(savings.deflected_runs)}</div>
              <div className={styles.heroTileLabel}>Noise incidents deflected</div>
            </div>
            <div className={styles.heroTile}>
              <div className={styles.heroTileVal}>{fmtInt(savings.actionable_runs)}</div>
              <div className={styles.heroTileLabel}>Actionable incidents caught</div>
            </div>
            <div className={styles.heroTile}>
              <div className={styles.heroTileVal}>{fmtInt(savings.high_conf_runs)}</div>
              <div className={styles.heroTileLabel}>High-confidence triages</div>
            </div>
          </div>

          <div className={styles.heroFootnote}>
            High-confidence triages (≥70% confidence) count fully; partial-confidence triages count
            as 0.5×
            {savings.hourly_rate_usd_assumed != null
              ? ` · at $${savings.hourly_rate_usd_assumed}/hr engineer rate`
              : ""}
          </div>
        </Card>

        {/* ── Row 2: Risk snapshot ───────────────────────────────────────── */}
        <Card
          title={
            <>
              Active incidents
              <InfoTip>
                Open cases right now, excluding snoozed ones. High impact = AI score ≥ 85/100. Stale
                = no new activity in the last 60 minutes.
              </InfoTip>
            </>
          }
          className={styles.col4}
        >
          <div className={styles.kpi}>
            <div className={styles.kpiValue}>{risk ? fmtInt(risk.active_count) : "—"}</div>
            <div className={styles.kpiLabel}>Open, not snoozed</div>
            <div className={styles.kpiMetaRow}>
              <span className={styles.pill}>
                High impact: {risk ? fmtInt(risk.active_high_impact_count) : "—"}
              </span>
              <span className={styles.pill}>
                Stale: {risk ? fmtInt(risk.stale_investigation_count) : "—"}
              </span>
              <span className={styles.pill}>Oldest: {oldestAge}</span>
            </div>
          </div>
        </Card>

        <Card
          title={
            <>
              Critical incidents this month
              <InfoTip>
                Cases created this calendar month where the AI assigned an impact score ≥ 85. Total
                = all cases regardless of severity.
              </InfoTip>
            </>
          }
          className={styles.col4}
        >
          <div className={styles.kpi}>
            <div className={styles.criticalNumber}>
              {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
              {risk ? fmtInt((risk as any).critical_this_month) : "—"}
            </div>
            <div className={styles.kpiLabel}>
              {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
              of {risk ? fmtInt((risk as any).total_this_month) : "—"} total this month
            </div>
          </div>
        </Card>

        <Card
          title={
            <>
              MTTR trend
              <InfoTip>
                Median Time To Resolve: the midpoint time from when a case was opened to when it was
                marked resolved, measured weekly. Lower is better.
              </InfoTip>
            </>
          }
          className={styles.col4}
        >
          <div className={styles.kpi}>
            {totalResolved === 0 ? (
              <div className={styles.muted}>No resolved cases in this period.</div>
            ) : (
              <>
                <div className={styles.kpiValue}>
                  {latestMttr != null ? `${latestMttr.toFixed(1)}h` : "—"}
                </div>
                <div className={styles.kpiLabel}>
                  Latest weekly median · {totalResolved} resolved cases
                </div>
                {(() => {
                  const prev =
                    mttrWeekly.length > 1
                      ? mttrWeekly[mttrWeekly.length - 2]?.mttr_hours_median
                      : null;
                  if (latestMttr == null || prev == null) return null;
                  const diff = latestMttr - prev;
                  const deltaClass =
                    diff > 0
                      ? styles.trendDeltaUp
                      : diff < 0
                        ? styles.trendDeltaDown
                        : styles.trendDeltaFlat;
                  const arrow = diff > 0 ? "↑" : diff < 0 ? "↓" : "→";
                  return (
                    <div className={`${styles.trendDelta} ${deltaClass}`}>
                      {arrow} {Math.abs(diff).toFixed(1)}h vs prior week
                    </div>
                  );
                })()}
                <MttrBars weekly={mttrWeekly} />
              </>
            )}
          </div>
        </Card>

        {/* ── Row 3: Signal quality ──────────────────────────────────────── */}
        <Card
          title={
            <>
              Alert signal quality
              <InfoTip>
                How Tarka classified each alert in the selected window. Actionable = engineer must
                act. Noisy = safe to discard. Informational = useful context, no action needed. High
                actionable % = your alerting is well-tuned.
              </InfoTip>
            </>
          }
          className={styles.col12}
        >
          <div className={styles.signalGrid}>
            <div className={styles.signalTile}>
              <div className={`${styles.signalVal} ${styles.signalValGreen}`}>
                {fmtPct(signal.actionable_pct)}
              </div>
              <div className={styles.signalLabel}>Actionable</div>
            </div>
            <div className={styles.signalTile}>
              <div className={`${styles.signalVal} ${styles.signalValAmber}`}>
                {fmtPct(
                  signal.noisy != null && signal.total_runs
                    ? (signal.noisy / signal.total_runs) * 100
                    : null
                )}
              </div>
              <div className={styles.signalLabel}>Noisy</div>
            </div>
            <div className={styles.signalTile}>
              <div className={`${styles.signalVal} ${styles.signalValMuted}`}>
                {fmtPct(
                  signal.informational != null && signal.total_runs
                    ? (signal.informational / signal.total_runs) * 100
                    : null
                )}
              </div>
              <div className={styles.signalLabel}>Informational</div>
            </div>
            <div className={styles.signalTile}>
              <div className={`${styles.signalVal} ${styles.signalValMuted}`}>
                {fmtInt(signal.unclassified)}
              </div>
              <div className={styles.signalLabel}>Unclassified</div>
            </div>
          </div>
          <hr className={styles.signalDivider} />
          <div className={styles.signalBottom}>
            <div className={styles.kpiMetaRow}>
              <span className={styles.pill}>{fmtInt(signal.actionable)} actionable</span>
              <span className={styles.pill}>{fmtInt(signal.noisy)} noisy</span>
              <span className={styles.pill}>{fmtInt(signal.informational)} informational</span>
            </div>
            {signal.change_correlated_count != null && (
              <div className={styles.signalNote}>
                {fmtInt(signal.change_correlated_count)} active incidents (
                {fmtPct(signal.change_correlated_pct)}) correlated with a recent deployment
              </div>
            )}
          </div>
        </Card>

        {/* ── Row 4: Top active + top teams ─────────────────────────────── */}
        <Card
          title={
            <>
              Top active (by impact)
              <InfoTip>
                Open cases ranked by AI-assigned impact score (0–100). Impact reflects estimated
                blast radius and severity. Click any row to open the full investigation.
              </InfoTip>
            </>
          }
          className={styles.col6}
        >
          <div className={styles.list}>
            {!topActive.length ? (
              <div className={styles.muted}>No active incidents in the current window.</div>
            ) : (
              topActive.map((it) => {
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
                    onClick={() => nav(`/cases/${encodeURIComponent(it.incident_id)}`)}
                  >
                    <div className={styles.liLeft}>
                      <div className={styles.liTitle}>{title}</div>
                      <div className={styles.liSub}>
                        <span>Age: {it.created_at ? formatAge(it.created_at) : "—"}</span>
                        {shownBits.length ? <span>•</span> : null}
                        {shownBits.map((b) => (
                          <span key={b}>{b}</span>
                        ))}
                        {subBits.length > shownBits.length && (
                          <span title={subBits.join(" • ")}>…</span>
                        )}
                      </div>
                    </div>
                    <div className={styles.liRight}>
                      <div
                        className={`${styles.score} ${
                          (it.impact_score ?? 0) >= 85
                            ? styles.scoreHigh
                            : (it.impact_score ?? 0) >= 60
                              ? styles.scoreMid
                              : styles.scoreLow
                        }`}
                      >
                        {fmtInt(it.impact_score)}
                      </div>
                      <div className={styles.muted}>conf {fmtInt(it.confidence_score)}%</div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
          {topActive.length > 0 && (
            <div className={styles.listFooter}>
              <button className={styles.listFooterLink} onClick={() => nav("/inbox?status=open")}>
                View all in Inbox →
              </button>
            </div>
          )}
        </Card>

        <Card
          title={
            <>
              Focus: top teams (active)
              <InfoTip>
                Teams with the most open incidents right now. Impact = average AI severity score
                across their active cases (0–100). Click a row to filter the Inbox by that team.
              </InfoTip>
            </>
          }
          className={styles.col6}
        >
          <div className={styles.list}>
            {!focusTeams.length ? (
              <div className={styles.muted}>No team data on latest runs.</div>
            ) : (
              focusTeams.map((t) => (
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
                    <div className={styles.muted}>impact</div>
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>

        {/* ── Row 5: Drivers + unstable services ────────────────────────── */}
        <Card
          title={
            <>
              Focus: top drivers (active)
              <InfoTip>
                Root causes appearing most often in open cases. Impact = average severity score of
                cases with that driver (0–100). Click a row to search the Inbox.
              </InfoTip>
            </>
          }
          className={styles.col6}
        >
          <div className={styles.list}>
            {!focusDrivers.length ? (
              <div className={styles.muted}>No driver/family data on latest runs.</div>
            ) : (
              focusDrivers.map((d) => (
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
                    <div className={styles.muted}>impact</div>
                  </div>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card
          title={
            <>
              Top unstable services
              <InfoTip>
                Services with the most distinct incidents in the selected window. Change-correlated
                = incidents where a recent deployment was detected as a likely trigger. Click a row
                to search the Inbox.
              </InfoTip>
            </>
          }
          className={styles.col6}
        >
          {!topServices.length ? (
            <div className={styles.muted}>No service data in this period.</div>
          ) : (
            <table className={styles.svcTable}>
              <thead>
                <tr>
                  <th>Service</th>
                  <th>Incidents</th>
                  <th>Alert types</th>
                  <th>Median impact</th>
                  <th>Change-corr.</th>
                </tr>
              </thead>
              <tbody>
                {topServices.map((s, i) => {
                  const maxCount = Math.max(1, ...topServices.map((x) => x.incident_count));
                  const barPct = Math.round((s.incident_count / maxCount) * 100);
                  return (
                    <tr
                      key={s.service}
                      className={styles.svcRow}
                      onClick={() => nav(`/inbox?q=${encodeURIComponent(s.service)}`)}
                      title="Open inbox search for this service"
                    >
                      <td className={styles.svcName}>{s.service}</td>
                      <td>
                        <span className={styles.inlineBar}>
                          <span>{s.incident_count}</span>
                          <span className={styles.inlineBarTrack}>
                            <span
                              className={styles.inlineBarFill}
                              style={{ width: `${barPct}%` }}
                            />
                          </span>
                        </span>
                      </td>
                      <td>{s.unique_alert_types}</td>
                      <td>
                        <span
                          className={
                            (s.median_impact ?? 0) >= 80
                              ? styles.scoreHigh
                              : (s.median_impact ?? 0) >= 55
                                ? styles.scoreMid
                                : ""
                          }
                        >
                          {s.median_impact != null ? Math.round(s.median_impact) : "—"}
                        </span>
                      </td>
                      <td>{s.change_correlated_count}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {topServices.length > 0 && (
            <div className={styles.listFooter}>
              <button className={styles.listFooterLink} onClick={() => nav("/inbox")}>
                View all in Inbox →
              </button>
            </div>
          )}
        </Card>

        {/* ── Row 6: Trends + recurrence ────────────────────────────────── */}
        <Card
          title={
            <>
              Trend: incidents created (14d)
              <InfoTip>
                New cases opened per day over the last 14 days. Bar height = relative daily volume.
                The delta compares the latest 7 days to the prior 7 days.
              </InfoTip>
            </>
          }
          className={styles.col4}
        >
          {(() => {
            const slice = trends.slice(-14);
            const prev7 = slice.slice(0, 7).reduce((s, d) => s + (d.incidents_created ?? 0), 0);
            const curr7 = slice.slice(7).reduce((s, d) => s + (d.incidents_created ?? 0), 0);
            const total = prev7 + curr7;
            const avg = slice.length ? (total / slice.length).toFixed(1) : "—";
            const diff = curr7 - prev7;
            const deltaClass =
              diff > 0
                ? styles.trendDeltaUp
                : diff < 0
                  ? styles.trendDeltaDown
                  : styles.trendDeltaFlat;
            const deltaArrow = diff > 0 ? "↑" : diff < 0 ? "↓" : "→";
            return (
              <div className={styles.trendCard}>
                <div className={styles.trendStat}>
                  <div className={styles.kpiValue}>{total || "—"}</div>
                  <div className={styles.kpiLabel}>{avg}/day avg over 14d</div>
                  <div className={`${styles.trendDelta} ${deltaClass}`}>
                    {deltaArrow} {Math.abs(diff)} vs prev 7d
                  </div>
                </div>
                <MiniBars days={trends} kind="volume" variant="blue" />
              </div>
            );
          })()}
        </Card>

        <Card
          title={
            <>
              Trend: median impact (14d)
              <InfoTip>
                Daily median impact score (0–100) across cases created each day. Rising = recent
                alerts are more severe on average. Driven by service health, not alert volume.
              </InfoTip>
            </>
          }
          className={styles.col4}
        >
          {(() => {
            const slice = trends.slice(-14);
            const latest = slice.length ? slice[slice.length - 1]?.impact_median : null;
            const prev7avg = slice.slice(0, 7).reduce((s, d) => s + (d.impact_median ?? 0), 0) / 7;
            const curr7avg = slice.slice(7).reduce((s, d) => s + (d.impact_median ?? 0), 0) / 7;
            const diff = Math.round(curr7avg - prev7avg);
            const deltaClass =
              diff > 0
                ? styles.trendDeltaUp
                : diff < 0
                  ? styles.trendDeltaDown
                  : styles.trendDeltaFlat;
            const deltaArrow = diff > 0 ? "↑" : diff < 0 ? "↓" : "→";
            return (
              <div className={styles.trendCard}>
                <div className={styles.trendStat}>
                  <div
                    className={`${styles.kpiValue} ${
                      (latest ?? 0) >= 70
                        ? styles.scoreHigh
                        : (latest ?? 0) >= 45
                          ? styles.scoreMid
                          : ""
                    }`}
                  >
                    {latest != null ? Math.round(latest) : "—"}
                  </div>
                  <div className={styles.kpiLabel}>latest median impact score</div>
                  <div className={`${styles.trendDelta} ${deltaClass}`}>
                    {deltaArrow} {Math.abs(diff)} pts vs prev 7d
                  </div>
                </div>
                <MiniBars days={trends} kind="impact" variant="amber" />
              </div>
            );
          })()}
        </Card>

        <Card
          title={
            <>
              Recurrence
              <InfoTip>
                Share of alert + service combinations that fired more than once in the window. High
                recurrence means alerts are being acknowledged but not fixed at the root cause.
              </InfoTip>
            </>
          }
          className={styles.col4}
        >
          <div className={styles.kpi}>
            <div className={styles.kpiValue}>
              {data ? fmtPct((data.recurrence?.rate || 0) * 100) : "—"}
            </div>
            <div className={styles.kpiLabel}>Share of incident keys that repeat in window.</div>
            <div className={styles.list}>
              {recurrenceTop.length ? (
                recurrenceTop.map((r) => (
                  <div
                    key={r.incident_key}
                    className={styles.listItem}
                    onClick={() => {
                      const colonIdx = r.incident_key.indexOf(":");
                      const alertname =
                        colonIdx >= 0 ? r.incident_key.slice(0, colonIdx) : r.incident_key;
                      const service = colonIdx >= 0 ? r.incident_key.slice(colonIdx + 1) : "";
                      const parts = [`alertname:${alertname}`];
                      if (service && service !== "unknown") parts.push(service);
                      nav(`/inbox?q=${encodeURIComponent(parts.join(" "))}`);
                    }}
                  >
                    <div className={styles.liLeft}>
                      <div className={styles.liTitle} title={r.incident_key}>
                        {r.incident_key.split(":")[0] || r.incident_key}
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
                <div className={styles.muted}>No recurring incidents in window.</div>
              )}
            </div>
          </div>
        </Card>

        {/* ── Row 7: AI effectiveness + cost ────────────────────────────── */}
        <Card
          title={
            <>
              AI effectiveness
              <InfoTip>
                TTFA (Time To First Analysis) = how fast Tarka completed its analysis after a fresh
                alert arrived. Only alerts received within 30 min of firing are counted, so this
                reflects pipeline speed, not how long alerts had been firing. Confidence = share of
                analyses scored ≥ 70% certain.
              </InfoTip>
            </>
          }
          className={styles.col6}
        >
          <div className={styles.rowSplit}>
            <div>
              <div
                className={`${styles.kpiValue} ${
                  (data?.ai?.ttfa_median_seconds ?? 999) < 60
                    ? styles.signalValGreen
                    : (data?.ai?.ttfa_median_seconds ?? 999) < 120
                      ? styles.scoreMid
                      : styles.scoreHigh
                }`}
              >
                {fmtSeconds(data?.ai?.ttfa_median_seconds ?? null)}
              </div>
              <div className={styles.kpiLabel}>TTFA median · target &lt;60s</div>
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
          <div className={styles.aiGapRow}>
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

        <Card
          title={
            <>
              AI / LLM cost
              <InfoTip>
                Total AI inference cost to power Tarka&apos;s investigations. This is the compute
                spend, not engineer time. Compare against the savings card to see the return on
                every dollar of AI spend.
              </InfoTip>
            </>
          }
          className={styles.col6}
        >
          <div className={styles.rowSplit}>
            <div>
              <div className={styles.kpiValue}>{fmtCostUsd(cost.total_usd)}</div>
              <div className={styles.kpiLabel}>Total spend ({days}d)</div>
            </div>
            <div>
              <div className={styles.kpiValue}>{fmtCostUsd(cost.avg_per_run_usd)}</div>
              <div className={styles.kpiLabel}>Avg per run</div>
            </div>
            <div>
              <div className={styles.kpiValue}>{fmtInt(cost.total_runs)}</div>
              <div className={styles.kpiLabel}>Runs with cost data</div>
            </div>
          </div>
          {cost.daily && cost.daily.length > 0 && <CostBars daily={cost.daily} />}
        </Card>
      </div>
    </div>
  );
}
