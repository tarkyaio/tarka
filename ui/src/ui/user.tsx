import React from "react";
import styles from "./UserChip.module.css";
import { useAuth } from "../state/auth";
import { IconButton } from "./IconButton";

export function UserChip({ collapsed = false }: { collapsed?: boolean } = {}) {
  const { user, logout } = useAuth();

  // Display logic:
  // - For local users: show name or username
  // - For OIDC users: show name or email
  let primary = "Not signed in";
  let secondary = "—";

  if (user) {
    if (user.provider === "local") {
      // Local user: prefer name, fallback to username
      primary = user.name || user.username || "Local user";
      secondary = user.email || "—";
    } else {
      // OIDC user: prefer name, fallback to email
      primary = user.name || user.email || `Signed in (${user.provider})`;
      secondary = user.name && user.email ? user.email : "—";
    }
  }

  // Avatar: use picture if available (OIDC), otherwise generate initials avatar for local users
  let avatarUrl = user?.picture;
  if (!avatarUrl && user) {
    // For local users, use a simple initials-based avatar (data URI)
    const initials = (user.name || user.username || "U").substring(0, 2).toUpperCase();
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="#6366f1"/><text x="20" y="25" text-anchor="middle" fill="white" font-family="sans-serif" font-size="16" font-weight="600">${initials}</text></svg>`;
    avatarUrl = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  }

  return (
    <div className={`${styles.userChip} ${collapsed ? styles.collapsed : ""}`}>
      <div
        className={styles.avatar}
        aria-hidden="true"
        style={{
          backgroundImage: `url("${avatarUrl}")`,
        }}
      />
      {!collapsed && (
        <div className={styles.userText}>
          <div className={styles.userName} title={primary}>
            {primary}
          </div>
          <div className={styles.userRole} title={secondary}>
            {secondary}
          </div>
        </div>
      )}

      {user ? (
        <IconButton size="sm" title="Log out" ariaLabel="Log out" onClick={() => void logout()}>
          <span className="material-symbols-outlined" aria-hidden="true">
            logout
          </span>
        </IconButton>
      ) : null}
    </div>
  );
}
