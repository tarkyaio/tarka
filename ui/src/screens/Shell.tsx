import React from "react";
import { NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { useApi } from "../lib/api";
import { UserChip } from "../ui/user";
import { useAuth } from "../state/auth";
import type { InboxResponse } from "../lib/types";
import { ChatShellProvider, useChatShell } from "../state/chat";
import styles from "./Shell.module.css";
import { IconButton } from "../ui/IconButton";
import { fingerprint7 } from "../lib/format";
import { ChatHost } from "../ui/ChatHost";

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

const HELP_ITEMS: { icon: string; label: string; href: string }[] = [
  {
    icon: "menu_book",
    label: "Documentation",
    href: "https://github.com/tarkyaio/tarka/tree/main/docs",
  },
  { icon: "headset_mic", label: "Support", href: "https://github.com/tarkyaio/tarka/issues" },
  {
    icon: "contract",
    label: "Changelog",
    href: "https://github.com/tarkyaio/tarka/blob/main/CHANGELOG.md",
  },
  { icon: "public", label: "Website", href: "https://tarkyaio.github.io/tarka/" },
];

function HelpPopover({
  open,
  pos,
  onClose,
  panelRef,
}: {
  open: boolean;
  pos: { top: number; right: number } | null;
  onClose: () => void;
  panelRef: React.RefObject<HTMLDivElement>;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !pos) return null;

  return (
    <div
      ref={panelRef}
      className={styles.helpPopover}
      style={{ top: pos.top, right: pos.right }}
      role="dialog"
      aria-label="Support resources"
    >
      <div className={styles.helpPopoverHeader}>Support Resources</div>
      {HELP_ITEMS.map(({ icon, label, href }) => (
        <a
          key={label}
          href={href}
          target="_blank"
          rel="noreferrer"
          className={styles.helpPopoverItem}
          onClick={onClose}
        >
          <MaterialIcon name={icon} />
          <span className={styles.helpPopoverLabel}>{label}</span>
        </a>
      ))}
    </div>
  );
}

function ShellInner() {
  const loc = useLocation();
  const [sp, setSp] = useSearchParams();
  const isInbox = loc.pathname.startsWith("/inbox");
  const isCase = loc.pathname.startsWith("/cases/");
  const caseId = isCase
    ? decodeURIComponent(loc.pathname.slice("/cases/".length).split("/")[0] || "")
    : "";
  const q = sp.get("q") || "";
  const { user } = useAuth();
  const { request } = useApi();
  const [inboxCount, setInboxCount] = React.useState<number | null>(null);
  const { mode, setMode, activeCase, setActiveCase } = useChatShell();
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);
  const [helpOpen, setHelpOpen] = React.useState(false);
  const [helpPos, setHelpPos] = React.useState<{ top: number; right: number } | null>(null);
  const helpPanelRef = React.useRef<HTMLDivElement>(null);
  const helpBtnRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (!helpOpen) return;
    const onPtr = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (!t || helpPanelRef.current?.contains(t) || helpBtnRef.current?.contains(t)) return;
      setHelpOpen(false);
    };
    window.addEventListener("pointerdown", onPtr, true);
    return () => window.removeEventListener("pointerdown", onPtr, true);
  }, [helpOpen]);

  // Auto-collapse sidebar when chat enters docked mode (maximize mode)
  React.useEffect(() => {
    if (mode === "docked") {
      setSidebarCollapsed(true);
    } else {
      setSidebarCollapsed(false);
    }
  }, [mode]);

  // Cmd+K (Mac) / Ctrl+K (Win/Linux) — toggle chat open/closed
  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        // Don't intercept when user is typing in an input/textarea
        const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
        if (tag === "input" || tag === "textarea") return;
        e.preventDefault();
        setMode(mode === "bubble" ? "floating" : "bubble");
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mode, setMode]);

  React.useEffect(() => {
    if (!user) {
      setInboxCount(null);
      return;
    }
    let cancelled = false;

    async function fetchInboxCount() {
      try {
        const d = await request<InboxResponse>("/api/v1/cases?status=all&limit=1&offset=0");
        if (cancelled) return;
        const n = typeof d?.total === "number" ? d.total : null;
        setInboxCount((prev) => (prev === n ? prev : n));
      } catch {
        if (cancelled) return;
        setInboxCount((prev) => (prev === null ? prev : null));
      }
    }

    const onInboxApplied = () => void fetchInboxCount();

    window.addEventListener("sre:inboxApplied", onInboxApplied as EventListener);
    void fetchInboxCount();
    return () => {
      cancelled = true;
      window.removeEventListener("sre:inboxApplied", onInboxApplied as EventListener);
    };
  }, [user, request]);

  return (
    <>
      <div className={`${styles.shell} appShell`} data-sidebar-collapsed={sidebarCollapsed}>
        <aside
          className={`${styles.sidebar} ${sidebarCollapsed ? styles.sidebarCollapsed : ""} appSidebar`}
        >
          <div className={styles.brand}>
            <div className={styles.brandMark} aria-hidden="true">
              <MaterialIcon name="shield_person" />
            </div>
            {!sidebarCollapsed && (
              <div className={styles.brandText}>
                <div className={styles.brandTitle}>SRE Console</div>
                <div className={styles.brandSub}>L1 Response Team</div>
              </div>
            )}
          </div>

          <nav className={styles.nav}>
            <NavLink
              to="/inbox"
              className={({ isActive }) =>
                `${styles.navItem} ${isActive ? styles.navItemActive : ""}`
              }
              title={sidebarCollapsed ? "Inbox" : undefined}
            >
              <span className={styles.navIcon} aria-hidden="true">
                <MaterialIcon name="inbox" filled />
              </span>
              {!sidebarCollapsed && <span className={styles.navLabel}>Inbox</span>}
              {!sidebarCollapsed && (
                <span
                  className={styles.navBadge}
                  aria-label="Total cases count"
                  title="Total cases"
                >
                  {inboxCount ?? "—"}
                </span>
              )}
            </NavLink>

            {isCase ? (
              <NavLink
                to={loc.pathname}
                className={({ isActive }) =>
                  `${styles.navItem} ${isActive ? styles.navItemActive : ""}`
                }
                title={sidebarCollapsed ? "Case Detail" : undefined}
              >
                <span className={styles.navIcon} aria-hidden="true">
                  <MaterialIcon name="analytics" filled />
                </span>
                {!sidebarCollapsed && <span className={styles.navLabel}>Case Detail</span>}
              </NavLink>
            ) : (
              <div
                className={`${styles.navItem} ${styles.navItemDisabled}`}
                aria-disabled="true"
                title={sidebarCollapsed ? "Case Detail" : "Open a case to view details"}
              >
                <span className={styles.navIcon} aria-hidden="true">
                  <MaterialIcon name="analytics" filled />
                </span>
                {!sidebarCollapsed && <span className={styles.navLabel}>Case Detail</span>}
              </div>
            )}
          </nav>

          <div className={styles.sidebarFooter}>
            <UserChip collapsed={sidebarCollapsed} />
          </div>
        </aside>

        <main className={`${styles.main} appMain`}>
          <header className={`${styles.topbar} appTopbar`}>
            {isInbox ? (
              <div className={styles.topSearch}>
                <span className={styles.topSearchIcon} aria-hidden="true">
                  <MaterialIcon name="search" />
                </span>
                <input
                  className={styles.topSearchInput}
                  placeholder="Search: ns: pod: deploy: svc: cluster: alert: (or free text)…"
                  aria-label="Search cases"
                  value={q}
                  onChange={(e) => {
                    const v = e.target.value;
                    const next = new URLSearchParams(sp);
                    if (!v) next.delete("q");
                    else next.set("q", v);
                    next.set("page", "0");
                    setSp(next, { replace: true });
                  }}
                />
              </div>
            ) : isCase ? (
              <div className={styles.crumbs}>
                <NavLink to="/inbox" className={styles.crumbLink}>
                  Cases
                </NavLink>
                <span className={styles.crumbSep} aria-hidden="true">
                  <MaterialIcon name="chevron_right" />
                </span>
                <span className={styles.crumbCurrent}>#{fingerprint7(caseId)}</span>
              </div>
            ) : (
              <div />
            )}

            <div className={styles.topbarActions}>
              {isCase &&
                activeCase?.caseStatus !== "closed" &&
                activeCase?.caseEffectiveStatus !== "snoozed" && (
                  <button
                    className={styles.resolveBtn}
                    type="button"
                    onClick={async () => {
                      const cid = activeCase?.caseId;
                      if (!cid) return;
                      try {
                        await request(`/api/v1/cases/${encodeURIComponent(cid)}/snooze`, {
                          method: "POST",
                        });
                        setActiveCase({ ...activeCase, caseEffectiveStatus: "snoozed" });
                      } catch {
                        // no-op
                      }
                    }}
                  >
                    Snooze
                  </button>
                )}
              <IconButton size="md" title="Notifications" disabled>
                <span className={styles.notifyWrap} aria-hidden="true">
                  <MaterialIcon name="notifications" />
                  {inboxCount !== null && inboxCount > 0 && <span className={styles.notifyDot} />}
                </span>
              </IconButton>
              <button
                ref={helpBtnRef}
                type="button"
                title="Help"
                className={`${styles.helpBtn} ${helpOpen ? styles.helpBtnActive : ""}`}
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  setHelpPos({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
                  setHelpOpen((o) => !o);
                }}
              >
                <MaterialIcon name="help" />
              </button>
            </div>
          </header>

          <div className={`${styles.content} appContent`}>
            <Outlet />
          </div>
        </main>
      </div>
      <ChatHost />
      <HelpPopover
        open={helpOpen}
        pos={helpPos}
        onClose={() => setHelpOpen(false)}
        panelRef={helpPanelRef}
      />
    </>
  );
}

export function Shell() {
  return (
    <ChatShellProvider>
      <ShellInner />
    </ChatShellProvider>
  );
}
