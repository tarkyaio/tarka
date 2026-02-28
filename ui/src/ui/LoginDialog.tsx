import React from "react";
import { useAuth } from "../state/auth";
import styles from "./LoginDialog.module.css";

export function LoginDialog({
  open,
  title = "Sign in",
  subtitle = "This Console is protected. Please sign in to continue.",
}: {
  open: boolean;
  title?: string;
  subtitle?: string;
}) {
  const { user, loading, authModeConfig, refresh } = useAuth();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [loginPending, setLoginPending] = React.useState(false);
  const [loginError, setLoginError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) return;
    setUsername("");
    setPassword("");
    setShowPassword(false);
    setLoginPending(false);
    setLoginError(null);
  }, [open]);

  if (!open) return null;
  if (user) return null;
  if (!authModeConfig) return null; // Still loading config

  const showLocal = authModeConfig.localEnabled;
  const showOIDC = authModeConfig.oidcEnabled;

  const handleLocalLogin = async () => {
    if (!username.trim() || !password) return;
    setLoginError(null);
    setLoginPending(true);
    try {
      const res = await fetch("/api/auth/login/local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ username: username.trim(), password }),
      });

      if (res.ok) {
        // Session cookie is set, refresh to get user info
        await refresh();
      } else {
        const body = await res.json().catch(() => ({ detail: "Login failed" }));
        setLoginError(body.detail || "Invalid username or password");
      }
    } catch (error) {
      setLoginError("Network error. Please try again.");
    } finally {
      setLoginPending(false);
    }
  };

  const handleOIDCLogin = () => {
    if (!authModeConfig.oidcProvider) return;
    const next = `${window.location.pathname}${window.location.search || ""}`;
    const url = `${authModeConfig.oidcProvider.loginUrl}?next=${encodeURIComponent(next)}`;
    window.location.assign(url);
  };

  return (
    <div className={styles.loginBackdrop} role="dialog" aria-modal="true" aria-label="Login">
      <div className={`uiCard ${styles.loginCard}`}>
        <div className={styles.loginHeader}>
          <div className={styles.loginTitle}>{title}</div>
          <div className={styles.loginSub}>{subtitle}</div>
        </div>

        <form className={styles.loginForm} onSubmit={(e) => e.preventDefault()}>
          {showOIDC && authModeConfig.oidcProvider ? (
            <>
              <button
                className={`uiBtn uiBtnPrimary ${styles.primaryBtn} ${styles.oidcBtn}`}
                type="button"
                disabled={loading}
                onClick={handleOIDCLogin}
              >
                {authModeConfig.oidcProvider.logo ? (
                  <img
                    src={authModeConfig.oidcProvider.logo}
                    alt=""
                    className={styles.providerLogo}
                  />
                ) : null}
                <span>Continue with {authModeConfig.oidcProvider.name}</span>
              </button>

              <div className={styles.loginHint}>
                Tip: you&apos;ll be redirected to {authModeConfig.oidcProvider.name} and back.
              </div>
            </>
          ) : null}

          {showOIDC && showLocal ? (
            <div className={styles.loginDivider}>
              <span>Or sign in with username and password</span>
            </div>
          ) : null}

          {showLocal ? (
            <>
              <label className={styles.field}>
                <div className={styles.label}>Username</div>
                <input
                  className={`uiInput ${styles.input}`}
                  autoFocus={!showOIDC}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void handleLocalLogin();
                  }}
                  autoComplete="username"
                  placeholder="admin"
                  disabled={loginPending || loading}
                />
              </label>

              <label className={styles.field}>
                <div className={styles.labelRow}>
                  <div className={styles.label}>Password</div>
                  <button
                    className={styles.linkBtn}
                    type="button"
                    onClick={() => setShowPassword((x) => !x)}
                  >
                    {showPassword ? "Hide" : "Show"}
                  </button>
                </div>
                <input
                  className={`uiInput ${styles.input}`}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void handleLocalLogin();
                  }}
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  placeholder="••••••••"
                  disabled={loginPending || loading}
                />
              </label>

              {loginError ? (
                <div className={styles.loginError} role="alert">
                  {loginError}
                </div>
              ) : null}

              <button
                className={`uiBtn uiBtnPrimary ${styles.primaryBtn}`}
                type="button"
                disabled={loginPending || loading || !username.trim() || !password}
                onClick={handleLocalLogin}
              >
                Sign in
              </button>
            </>
          ) : null}
        </form>
      </div>
    </div>
  );
}
