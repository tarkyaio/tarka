import React from "react";
import styles from "./IconButton.module.css";

export function IconButton({
  title,
  ariaLabel,
  disabled,
  onClick,
  size = "md",
  className,
  children,
  type = "button",
}: {
  title?: string;
  ariaLabel?: string;
  disabled?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  size?: "sm" | "md";
  className?: string;
  children: React.ReactNode;
  type?: "button" | "submit" | "reset";
}) {
  return (
    <button
      type={type}
      className={`${styles.btn} ${size === "sm" ? styles.sm : styles.md}${className ? ` ${className}` : ""}`}
      title={title}
      aria-label={ariaLabel || title}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
