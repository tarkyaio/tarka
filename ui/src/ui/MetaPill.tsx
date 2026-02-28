import React from "react";
import styles from "./MetaPill.module.css";

export function MetaPill({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span className={styles.metaPill} title={title}>
      {children}
    </span>
  );
}
