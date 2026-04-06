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

  return (
    <div className={`${styles.userChip} ${collapsed ? styles.collapsed : ""}`}>
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
