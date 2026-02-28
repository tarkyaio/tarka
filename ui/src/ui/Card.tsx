import React from "react";
import styles from "./Card.module.css";

export function Card({
  title,
  children,
  className,
  bodyClassName,
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`uiCard ${styles.card} ${className || ""}`}>
      {title ? <div className={styles.header}>{title}</div> : null}
      <div className={`${styles.body} ${bodyClassName || ""}`}>{children}</div>
    </section>
  );
}
