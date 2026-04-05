import React from "react";
import styles from "./AlertStatusPill.module.css";

type AlertStatus = "firing" | "resolved" | "stale" | "snoozed";

function normalizeStatus(s?: string | null): AlertStatus {
  const v = (s || "").trim().toLowerCase();
  if (v === "resolved") return "resolved";
  if (v === "stale") return "stale";
  if (v === "snoozed") return "snoozed";
  return "firing";
}

export function AlertStatusPill({ status }: { status?: string | null }) {
  const norm = normalizeStatus(status);
  const label = norm.charAt(0).toUpperCase() + norm.slice(1);
  const kindCls =
    norm === "firing"
      ? styles.firing
      : norm === "resolved"
        ? styles.resolved
        : norm === "snoozed"
          ? styles.snoozed
          : styles.stale;

  return (
    <span className={`${styles.pill} ${kindCls}`} title={`Alert status: ${label}`}>
      <span className={styles.dot} aria-hidden="true" />
      {label}
    </span>
  );
}
