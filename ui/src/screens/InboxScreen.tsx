import React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useApi, ApiError } from "../lib/api";
import { CaseFacetsResponse, InboxResponse, InboxRow } from "../lib/types";
import { classificationLabel, fingerprint7, formatAge } from "../lib/format";
import { useAuth } from "../state/auth";
import { LoginDialog } from "../ui/LoginDialog";
import { IconButton } from "../ui/IconButton";
import { MetaPill } from "../ui/MetaPill";
import { ClassificationPill } from "../ui/ClassificationPill";
import { SeverityPill } from "../ui/SeverityPill";
import styles from "./InboxScreen.module.css";

type InboxCacheEntry = { ts: number; data: InboxResponse };
const INBOX_CACHE_TTL_MS = 30_000;
const _inboxCache = new Map<string, InboxCacheEntry>();

function _cacheKey(url: string): string {
  return `inbox:${url}`;
}

function _readInboxCache(url: string): InboxResponse | null {
  const key = _cacheKey(url);
  const now = Date.now();

  const mem = _inboxCache.get(key);
  if (mem && now - mem.ts <= INBOX_CACHE_TTL_MS) return mem.data;

  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as InboxCacheEntry;
    if (!parsed?.ts || !parsed?.data) return null;
    if (now - parsed.ts > INBOX_CACHE_TTL_MS) return null;
    _inboxCache.set(key, parsed);
    return parsed.data;
  } catch {
    return null;
  }
}

function _writeInboxCache(url: string, data: InboxResponse) {
  const key = _cacheKey(url);
  const entry: InboxCacheEntry = { ts: Date.now(), data };
  _inboxCache.set(key, entry);
  try {
    sessionStorage.setItem(key, JSON.stringify(entry));
  } catch {
    // ignore (storage quota / disabled)
  }
}

const CLASSIFICATION_OPTIONS = [
  { value: "", label: "All" },
  { value: "actionable", label: "Actionable" },
  { value: "informational", label: "Informational" },
  { value: "noisy", label: "Noise" },
  { value: "artifact", label: "Artifact" },
];

const FAMILY_OPTIONS = [
  { value: "", label: "All" },
  { value: "crashloop", label: "Crashloop" },
  { value: "cpu_throttling", label: "CPU Throttling" },
  { value: "pod_not_healthy", label: "Pod Not Healthy" },
  { value: "http_5xx", label: "HTTP 5xx" },
  { value: "oom_killed", label: "OOM Killed" },
  { value: "memory_pressure", label: "Memory Pressure" },
  { value: "target_down", label: "Target Down" },
  { value: "k8s_rollout_health", label: "K8s Rollout Health" },
  { value: "observability_pipeline", label: "Observability Pipeline" },
  { value: "meta", label: "Meta" },
  { value: "generic", label: "Generic" },
];

function scoreColor(score?: number | null): "green" | "amber" | "red" | "muted" {
  if (score == null) return "muted";
  // Match sample: high-impact bars are red
  if (score >= 85) return "green";
  if (score >= 60) return "amber";
  return "red";
}

function impactTone(
  score?: number | null
): "impactHigh" | "impactMed" | "impactLow" | "impactMuted" {
  if (score == null) return "impactMuted";
  if (score >= 85) return "impactHigh";
  if (score >= 60) return "impactMed";
  return "impactLow";
}

function noiseTone(score?: number | null): "noiseHigh" | "noiseMuted" {
  if (score == null) return "noiseMuted";
  return score >= 70 ? "noiseHigh" : "noiseMuted";
}

function inboxSignature(d: InboxResponse | null): string {
  const items = d?.items || [];
  // Include both identity (run_id) and freshness (case_updated_at) for current page.
  return items.map((it) => `${it.run_id}|${it.case_updated_at || ""}`).join(",");
}

export function InboxScreen() {
  const nav = useNavigate();
  const { user, loading: authLoading } = useAuth();
  const { request } = useApi();
  const [sp, setSp] = useSearchParams();

  const q = sp.get("q") || "";
  const classification = sp.get("classification") || "";
  const family = sp.get("family") || "";
  const team = sp.get("team") || "";
  const page = Math.max(0, parseInt(sp.get("page") || "0", 10) || 0);
  const pageSize = 6;

  const [data, setData] = React.useState<InboxResponse | null>(null);
  const [facets, setFacets] = React.useState<CaseFacetsResponse | null>(null);
  const [pendingData, setPendingData] = React.useState<InboxResponse | null>(null);
  const [dismissedSig, setDismissedSig] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState<ApiError | null>(null);

  const [menuOpen, setMenuOpen] = React.useState<null | "classification" | "family" | "team">(null);
  const [menuPos, setMenuPos] = React.useState<{ top: number; left: number; width: number } | null>(
    null
  );
  const menuRef = React.useRef<HTMLDivElement | null>(null);

  const classificationButtonRef = React.useRef<HTMLButtonElement | null>(null);
  const familyButtonRef = React.useRef<HTMLButtonElement | null>(null);
  const teamButtonRef = React.useRef<HTMLButtonElement | null>(null);

  const appliedSigRef = React.useRef<string>("");
  const dismissedSigRef = React.useRef<string | null>(null);

  const inboxUrl = React.useMemo(() => {
    // Default to `status=all` and do not include `service` filtering.
    return `/api/v1/cases?status=all&q=${encodeURIComponent(q)}&classification=${encodeURIComponent(
      classification
    )}&family=${encodeURIComponent(family)}&team=${encodeURIComponent(team)}&limit=${pageSize}&offset=${page * pageSize}`;
  }, [q, classification, family, team, page, pageSize]);

  React.useEffect(() => {
    appliedSigRef.current = inboxSignature(data);
  }, [data]);

  React.useEffect(() => {
    dismissedSigRef.current = dismissedSig;
  }, [dismissedSig]);

  React.useEffect(() => {
    if (!user) return;
    let cancelled = false;

    // Instant render on back-navigation (cache), then refresh in background.
    const cached = _readInboxCache(inboxUrl);
    if (cached) setData(cached);

    setLoading(true);
    setErr(null);
    request<InboxResponse>(inboxUrl)
      .then((d) => {
        if (cancelled) return;
        _writeInboxCache(inboxUrl, d);
        setData(d);
        setPendingData(null);
        setDismissedSig(null);
        window.dispatchEvent(new CustomEvent("sre:inboxApplied"));
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
  }, [user, request, inboxUrl]);

  // Fetch facets (non-paginated) so dropdown options don't shift when paging.
  React.useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const facetsUrl = `/api/v1/cases/facets?status=all&q=${encodeURIComponent(q)}&classification=${encodeURIComponent(
      classification
    )}&family=${encodeURIComponent(family)}`;
    request<CaseFacetsResponse>(facetsUrl)
      .then((d) => {
        if (cancelled) return;
        setFacets(d);
      })
      .catch(() => {
        if (cancelled) return;
        setFacets(null);
      });
    return () => {
      cancelled = true;
    };
  }, [user, request, q, classification, family]);

  // Smooth background refresh: fetch every 30s, but don't apply to the table until the user clicks "Update".
  React.useEffect(() => {
    if (!user) return;
    let cancelled = false;
    const intervalMs = 30_000;

    async function tick() {
      try {
        const d = await request<InboxResponse>(inboxUrl);
        if (cancelled) return;

        const currentSig = appliedSigRef.current;
        const nextSig = inboxSignature(d);

        if (!nextSig || nextSig === currentSig) return;
        if (dismissedSigRef.current && nextSig === dismissedSigRef.current) return;

        setPendingData(d);
      } catch {
        // ignore background refresh errors; main UX surface is existing error card / manual refresh.
      }
    }

    // Stagger the first tick so we don't double-fetch right after the main load.
    const t0 = window.setTimeout(() => void tick(), 5_000);
    const t = window.setInterval(() => void tick(), intervalMs);

    return () => {
      cancelled = true;
      window.clearTimeout(t0);
      window.clearInterval(t);
    };
  }, [user, request, inboxUrl]);

  React.useEffect(() => {
    if (!menuOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(null);
    };
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (menuRef.current && menuRef.current.contains(t)) return;
      if (classificationButtonRef.current && classificationButtonRef.current.contains(t)) return;
      if (familyButtonRef.current && familyButtonRef.current.contains(t)) return;
      if (teamButtonRef.current && teamButtonRef.current.contains(t)) return;
      setMenuOpen(null);
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onPointerDown, true);
    };
  }, [menuOpen]);

  function openMenu(kind: "classification" | "family" | "team", el: HTMLButtonElement | null) {
    if (!el) return;
    if (menuOpen === kind) {
      setMenuOpen(null);
      return;
    }
    const r = el.getBoundingClientRect();
    setMenuPos({ top: r.bottom + 8, left: r.left, width: Math.max(220, r.width) });
    setMenuOpen(kind);
  }

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(sp);
    if (!value) next.delete(key);
    else next.set(key, value);
    next.set("page", "0");
    setSp(next, { replace: true });
  }

  const total = data?.total ?? 0;
  const showingFrom = total === 0 ? 0 : page * pageSize + 1;
  const showingTo = Math.min(total, page * pageSize + (data?.items?.length || 0));
  const canPrev = page > 0;
  const canNext = showingTo < total;

  const TEAM_OPTIONS = React.useMemo(() => {
    const opts = (facets?.teams || []).map((v) => ({ value: v, label: v }));
    return [{ value: "", label: "All" }, ...opts];
  }, [facets]);

  return (
    <div className={styles.inbox}>
      <LoginDialog open={!user && !authLoading} />

      {!user || authLoading ? null : (
        <>
          <div className={styles.inboxHeader}>
            <div>
              <div className={styles.inboxTitle}>Case Inbox</div>
              <div className={styles.inboxSubtitle}>
                Triage alerts with evidence and next steps.
              </div>
            </div>
          </div>

          <div className={styles.toolbar}>
            <div className={styles.toolbarLeft}>
              <div className={`${styles.chip} ${styles.chipActive}`}>
                <span className={`material-symbols-outlined ${styles.listIcon}`}>list</span>
                List View
              </div>

              <div className={styles.toolbarDivider} aria-hidden="true" />

              <button
                ref={classificationButtonRef}
                className={styles.filterBtn}
                type="button"
                onClick={() => openMenu("classification", classificationButtonRef.current)}
                aria-haspopup="menu"
                aria-expanded={menuOpen === "classification" ? "true" : "false"}
              >
                <span className={styles.filterBtnLabel}>Classification</span>
                <span className={styles.filterBtnValue}>
                  {classification ? classificationLabel(classification) : "All"}
                </span>
                <span className={`material-symbols-outlined ${styles.dropIcon}`}>expand_more</span>
              </button>

              <button
                ref={familyButtonRef}
                className={styles.filterBtn}
                type="button"
                onClick={() => openMenu("family", familyButtonRef.current)}
                aria-haspopup="menu"
                aria-expanded={menuOpen === "family" ? "true" : "false"}
              >
                <span className={styles.filterBtnLabel}>Family</span>
                <span className={styles.filterBtnValue}>
                  {family
                    ? FAMILY_OPTIONS.find((opt) => opt.value === family)?.label || family
                    : "All"}
                </span>
                <span className={`material-symbols-outlined ${styles.dropIcon}`}>expand_more</span>
              </button>

              <button
                ref={teamButtonRef}
                className={styles.filterBtn}
                type="button"
                onClick={() => openMenu("team", teamButtonRef.current)}
                aria-haspopup="menu"
                aria-expanded={menuOpen === "team" ? "true" : "false"}
              >
                <span className={styles.filterBtnLabel}>Team</span>
                <span className={styles.filterBtnValue}>{team ? team : "All"}</span>
                <span className={`material-symbols-outlined ${styles.dropIcon}`}>expand_more</span>
              </button>
            </div>

            <div className={styles.toolbarRight}>
              <IconButton
                size="sm"
                title="Clear filters"
                onClick={() => {
                  const next = new URLSearchParams(sp);
                  next.set("page", "0");
                  next.delete("q");
                  next.delete("classification");
                  next.delete("family");
                  next.delete("team");
                  // Cleanup legacy params if present.
                  next.delete("status");
                  next.delete("service");
                  next.delete("_ts");
                  setMenuOpen(null);
                  setSp(next, { replace: true });
                }}
              >
                <span className="material-symbols-outlined">filter_list</span>
              </IconButton>
              <IconButton
                size="sm"
                title="Refresh"
                onClick={() => {
                  // soft refresh: reset page to 0 and bump a dummy param so effect re-runs
                  const next = new URLSearchParams(sp);
                  next.set("page", "0");
                  next.set("_ts", String(Date.now()));
                  setSp(next, { replace: true });
                }}
              >
                <span className="material-symbols-outlined">refresh</span>
              </IconButton>
            </div>
          </div>

          {menuOpen && menuPos ? (
            <div
              ref={menuRef}
              className={styles.filterMenu}
              role="menu"
              style={{
                position: "fixed",
                top: menuPos.top,
                left: menuPos.left,
                width: menuPos.width,
              }}
            >
              {menuOpen === "classification" ? (
                <div className={styles.filterMenuSection}>
                  {CLASSIFICATION_OPTIONS.map((opt) => (
                    <button
                      key={opt.value || "__all__"}
                      type="button"
                      className={`${styles.filterMenuItem} ${classification === opt.value ? styles.filterMenuItemActive : ""}`}
                      onClick={() => {
                        setParam("classification", opt.value);
                        setMenuOpen(null);
                      }}
                    >
                      <span className={styles.filterMenuItemLabel}>{opt.label}</span>
                      {classification === opt.value ? (
                        <span className="material-symbols-outlined">check</span>
                      ) : (
                        <span />
                      )}
                    </button>
                  ))}
                </div>
              ) : null}
              {menuOpen === "family" ? (
                <div className={styles.filterMenuSection}>
                  {FAMILY_OPTIONS.map((opt) => (
                    <button
                      key={opt.value || "__all__"}
                      type="button"
                      className={`${styles.filterMenuItem} ${family === opt.value ? styles.filterMenuItemActive : ""}`}
                      onClick={() => {
                        setParam("family", opt.value);
                        setMenuOpen(null);
                      }}
                    >
                      <span className={styles.filterMenuItemLabel}>{opt.label}</span>
                      {family === opt.value ? (
                        <span className="material-symbols-outlined">check</span>
                      ) : (
                        <span />
                      )}
                    </button>
                  ))}
                </div>
              ) : null}
              {menuOpen === "team" ? (
                <div className={styles.filterMenuSection}>
                  {TEAM_OPTIONS.map((opt) => (
                    <button
                      key={opt.value || "__all__"}
                      type="button"
                      className={`${styles.filterMenuItem} ${team === opt.value ? styles.filterMenuItemActive : ""}`}
                      onClick={() => {
                        setParam("team", opt.value);
                        setMenuOpen(null);
                      }}
                    >
                      <span className={styles.filterMenuItemLabel}>{opt.label}</span>
                      {team === opt.value ? (
                        <span className="material-symbols-outlined">check</span>
                      ) : (
                        <span />
                      )}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {err ? (
            <div className={styles.errorCard} role="status">
              <div className={styles.errorTitle}>Couldn’t load cases</div>
              <div className={styles.errorDetail}>
                {err.status}: {err.message}
              </div>
            </div>
          ) : null}

          <div className={styles.tableCard} aria-busy={loading ? "true" : "false"}>
            {pendingData ? (
              <div className={styles.updateBanner} role="status" aria-live="polite">
                <div className={styles.updateBannerText}>Updates available</div>
                <div className={styles.updateBannerActions}>
                  <button
                    type="button"
                    className={styles.updateBannerBtnPrimary}
                    onClick={() => {
                      _writeInboxCache(inboxUrl, pendingData);
                      setData(pendingData);
                      setPendingData(null);
                      setDismissedSig(null);
                      window.dispatchEvent(new CustomEvent("sre:inboxApplied"));
                    }}
                  >
                    Update
                  </button>
                  <button
                    type="button"
                    className={styles.updateBannerBtn}
                    onClick={() => {
                      setDismissedSig(inboxSignature(pendingData));
                      setPendingData(null);
                    }}
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            ) : null}
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead className={styles.thead}>
                  <tr>
                    <th className={`${styles.th} ${styles.colClassification}`}>Classification</th>
                    <th className={`${styles.th} ${styles.colIncident}`}>Case</th>
                    <th className={`${styles.th} ${styles.hideMd} ${styles.colFamily}`}>Family</th>
                    <th className={`${styles.th} ${styles.colTarget}`}>Target</th>
                    <th className={`${styles.th} ${styles.colScores}`}>Scores</th>
                    <th className={`${styles.th} ${styles.colAge}`}>Age</th>
                    <th className={`${styles.th} ${styles.colSeverity}`}>Severity</th>
                  </tr>
                </thead>
                <tbody className={styles.tbody}>
                  {loading && !data ? (
                    <tr>
                      <td className={styles.td} colSpan={7}>
                        <div className={styles.skeletonRow}>Loading…</div>
                      </td>
                    </tr>
                  ) : null}

                  {(data?.items || []).map((r: InboxRow) => {
                    return (
                      <tr
                        key={r.run_id}
                        className={styles.tr}
                        onClick={() => nav(`/cases/${encodeURIComponent(r.case_id)}`)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ")
                            nav(`/cases/${encodeURIComponent(r.case_id)}`);
                        }}
                      >
                        <td className={styles.td}>
                          <ClassificationPill classification={r.classification} />
                        </td>
                        <td className={styles.td}>
                          <div className={styles.incidentCell}>
                            <div className={styles.incidentTitleRow}>
                              <div className={styles.incidentTitle} title={r.alertname || ""}>
                                {r.alertname || "Unknown"}
                              </div>
                              {r.team ? (
                                <span className={styles.teamBadge}>
                                  {String(r.team).trim().toLowerCase()}
                                </span>
                              ) : null}
                            </div>
                            <div className={styles.incidentMeta}>
                              <span>#{fingerprint7(r.case_id)}</span>
                              <span className={styles.sep}>•</span>
                              <span>{r.enrichment_summary || r.primary_driver || "n/a"}</span>
                            </div>
                          </div>
                        </td>
                        <td className={`${styles.td} ${styles.hideMd} ${styles.familyCell}`}>
                          <MetaPill>{r.family || "—"}</MetaPill>
                        </td>
                        <td className={`${styles.td} ${styles.targetCell}`}>
                          {r.service || r.namespace || r.cluster || "—"}
                        </td>
                        <td className={styles.td}>
                          <div className={styles.scoresStack}>
                            <div
                              className={styles.scoreItem}
                              title={
                                r.impact_score != null
                                  ? `Impact Score: ${r.impact_score}/100`
                                  : "Impact Score: —"
                              }
                            >
                              <span
                                className={`material-symbols-outlined ${styles.scoreIcon} ${styles.scoreIconFilled} ${styles[impactTone(r.impact_score)]}`}
                              >
                                bolt
                              </span>
                              <span className={styles.scoreNum}>
                                {r.impact_score != null ? r.impact_score : "—"}
                              </span>
                            </div>
                            <div
                              className={styles.scoreItem}
                              title={
                                r.confidence_score != null
                                  ? `AI Confidence: ${r.confidence_score}%`
                                  : "AI Confidence: —"
                              }
                            >
                              <span
                                className={`material-symbols-outlined ${styles.scoreIcon} ${styles.scoreIconFilled} ${styles.confIcon} ${styles[scoreColor(r.confidence_score)]}`}
                              >
                                verified
                              </span>
                              <span className={styles.scorePct}>
                                {r.confidence_score != null ? `${r.confidence_score}%` : "—"}
                              </span>
                            </div>
                            <div
                              className={styles.scoreItem}
                              title={
                                r.noise_score != null
                                  ? `Noise Level: ${r.noise_score}%`
                                  : "Noise Level: —"
                              }
                            >
                              <span
                                className={`material-symbols-outlined ${styles.scoreIcon} ${styles.noiseIcon} ${styles[noiseTone(r.noise_score)]}`}
                              >
                                graphic_eq
                              </span>
                              <span className={styles.scorePct}>
                                {r.noise_score != null ? `${r.noise_score}%` : "—"}
                              </span>
                            </div>
                          </div>
                        </td>
                        <td className={`${styles.td} ${styles.ageCell}`}>
                          {formatAge(r.case_created_at)}
                        </td>
                        <td className={`${styles.td} ${styles.severityCell}`}>
                          <SeverityPill severity={r.severity} />
                        </td>
                      </tr>
                    );
                  })}

                  {data && data.items.length === 0 && !loading ? (
                    <tr>
                      <td className={styles.td} colSpan={7}>
                        <div className={styles.empty}>No cases match your filters.</div>
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <div className={styles.tableFooter}>
              <div className={styles.footerText}>
                Showing <span className={styles.footerStrong}>{showingFrom}</span> to{" "}
                <span className={styles.footerStrong}>{showingTo}</span> of{" "}
                <span className={styles.footerStrong}>{total}</span> results
              </div>
              <div className={styles.pager}>
                <button
                  type="button"
                  disabled={!canPrev}
                  onClick={() => {
                    const next = new URLSearchParams(sp);
                    next.set("page", String(Math.max(0, page - 1)));
                    setSp(next, { replace: true });
                  }}
                  className={styles.pagerBtn}
                  aria-label="Previous page"
                >
                  <span className="material-symbols-outlined">chevron_left</span>
                </button>
                <button
                  type="button"
                  disabled={!canNext}
                  onClick={() => {
                    const next = new URLSearchParams(sp);
                    next.set("page", String(page + 1));
                    setSp(next, { replace: true });
                  }}
                  className={styles.pagerBtn}
                  aria-label="Next page"
                >
                  <span className="material-symbols-outlined">chevron_right</span>
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
