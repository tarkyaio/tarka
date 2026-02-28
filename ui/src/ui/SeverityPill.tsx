import React from "react";
import styles from "./SeverityPill.module.css";

function normalizeSeverity(s?: string | null): "critical" | "warning" | "info" | "low" | "muted" {
  const v = (s || "").trim().toLowerCase();
  if (!v) return "muted";
  if (v === "critical" || v === "crit" || v === "page" || v === "paging") return "critical";
  if (v === "high") return "critical";
  if (v === "warning" || v === "warn") return "warning";
  if (v === "info" || v === "information") return "info";
  if (v === "low") return "low";
  return "muted";
}

export function SeverityPill({ severity }: { severity?: string | null }) {
  const norm = normalizeSeverity(severity);
  const label = (severity || "").trim() || "—";
  const kind =
    norm === "critical"
      ? styles.critical
      : norm === "warning"
        ? styles.warning
        : norm === "info"
          ? styles.info
          : norm === "low"
            ? styles.low
            : styles.muted;

  return (
    <span className={`${styles.pill} ${kind}`} title={severity || ""}>
      {label}
    </span>
  );
}
