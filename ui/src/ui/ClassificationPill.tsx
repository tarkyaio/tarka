import React from "react";
import { classificationLabel } from "../lib/format";
import styles from "./ClassificationPill.module.css";

export function ClassificationPill({ classification }: { classification?: string | null }) {
  const cls = (classification || "").toLowerCase();
  const kind =
    cls === "actionable"
      ? styles.actionable
      : cls === "informational"
        ? styles.info
        : cls === "noisy"
          ? styles.noise
          : styles.muted;
  return <span className={`${styles.pill} ${kind}`}>{classificationLabel(classification)}</span>;
}
