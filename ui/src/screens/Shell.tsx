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
  const { mode } = useChatShell();
  const [sidebarCollapsed, setSidebarCollapsed] = React.useState(false);

  // Auto-collapse sidebar when chat enters docked mode (maximize mode)
  React.useEffect(() => {
    if (mode === "docked") {
      setSidebarCollapsed(true);
    } else {
      setSidebarCollapsed(false);
    }
  }, [mode]);

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
              <IconButton size="md" title="Notifications (coming soon)" disabled>
                <span className={styles.notifyWrap} aria-hidden="true">
                  <MaterialIcon name="notifications" />
                  <span className={styles.notifyDot} />
                </span>
              </IconButton>
              <IconButton size="md" title="Help (coming soon)" disabled>
                <MaterialIcon name="help" />
              </IconButton>
            </div>
          </header>

          <div className={`${styles.content} appContent`}>
            <Outlet />
          </div>
        </main>
      </div>
      <ChatHost />
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
