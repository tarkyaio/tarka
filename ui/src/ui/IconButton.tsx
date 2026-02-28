import React from "react";
import styles from "./IconButton.module.css";

export function IconButton({
  title,
  ariaLabel,
  disabled,
  onClick,
  size = "md",
  children,
  type = "button",
}: {
  title?: string;
  ariaLabel?: string;
  disabled?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  size?: "sm" | "md";
  children: React.ReactNode;
  type?: "button" | "submit" | "reset";
}) {
  return (
    <button
      type={type}
      className={`${styles.btn} ${size === "sm" ? styles.sm : styles.md}`}
      title={title}
      aria-label={ariaLabel || title}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
