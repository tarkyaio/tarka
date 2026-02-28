import React from "react";

export type AuthUser = {
  provider: string;
  email: string | null;
  name: string | null;
  picture?: string | null;
  username?: string | null; // For local auth
};

export type AuthModeConfig = {
  oidcEnabled: boolean;
  localEnabled: boolean;
  oidcProvider?: {
    name: string;
    logo: string;
    loginUrl: string;
  };
};

const MOCK_USER: AuthUser = { provider: "mock", email: "mock@example.com", name: "Mock User" };

function sameUser(a: AuthUser | null, b: AuthUser | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.provider === b.provider && a.email === b.email && a.name === b.name;
}

type AuthState = {
  user: AuthUser | null;
  authModeConfig: AuthModeConfig | null;
  loading: boolean;
  refresh: () => Promise<AuthUser | null>;
  logout: () => Promise<void>;
  clear: () => void;
};

const AuthContext = React.createContext<AuthState | null>(null);

async function fetchMe(): Promise<AuthUser | null> {
  const res = await fetch("/api/auth/me", { credentials: "same-origin" });
  if (res.status === 401) return null;
  const body = await res.json().catch(() => null);
  if (!res.ok) return null;
  const u = body?.user;
  if (!u || typeof u !== "object") return null;
  return {
    provider: String(u.provider || "unknown"),
    email: u.email ? String(u.email) : null,
    name: u.name ? String(u.name) : null,
    picture: u.picture ? String(u.picture) : null,
    username: u.username ? String(u.username) : null,
  };
}

async function fetchAuthMode(): Promise<AuthModeConfig | null> {
  const res = await fetch("/api/auth/mode", { credentials: "same-origin" });
  const body = await res.json().catch(() => null);
  if (!body || typeof body !== "object") return null;

  const oidcEnabled = Boolean(body.oidcEnabled);
  const localEnabled = Boolean(body.localEnabled);

  const result: AuthModeConfig = {
    oidcEnabled,
    localEnabled,
  };

  if (oidcEnabled && body.oidcProvider && typeof body.oidcProvider === "object") {
    result.oidcProvider = {
      name: String(body.oidcProvider.name || "SSO"),
      logo: String(body.oidcProvider.logo || ""),
      loginUrl: String(body.oidcProvider.loginUrl || "/api/auth/login/oidc"),
    };
  }

  return result;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const mockMode = import.meta.env.VITE_MOCK_API === "1";
  const [user, setUser] = React.useState<AuthUser | null>(mockMode ? MOCK_USER : null);
  const [authModeConfig, setAuthModeConfig] = React.useState<AuthModeConfig | null>(null);
  const [modeLoading, setModeLoading] = React.useState(!mockMode);
  const [loading, setLoading] = React.useState(!mockMode);

  const refresh = React.useCallback(async () => {
    if (mockMode) return MOCK_USER;
    setLoading(true);
    try {
      const u = await fetchMe();
      setUser((prev) => (sameUser(prev, u) ? prev : u));
      return u;
    } finally {
      setLoading(false);
    }
  }, [mockMode]);

  const clear = React.useCallback(() => {
    if (mockMode) return;
    setUser(null);
  }, [mockMode]);

  const logout = React.useCallback(async () => {
    if (mockMode) {
      setUser(null);
      return;
    }
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      setUser(null);
    }
  }, [mockMode]);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  // Discover auth mode for correct login UX.
  React.useEffect(() => {
    if (mockMode) {
      setAuthModeConfig({ oidcEnabled: false, localEnabled: true });
      return;
    }
    let cancelled = false;
    setModeLoading(true);
    fetchAuthMode()
      .then((m) => {
        if (cancelled) return;
        setAuthModeConfig(m);
      })
      .catch(() => {
        if (cancelled) return;
        setAuthModeConfig(null);
      })
      .finally(() => {
        if (cancelled) return;
        setModeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mockMode]);

  // If the browser restores the page from back/forward cache (or session restore),
  // React state may include previously-rendered authenticated data. Force a re-check
  // and hide app content until auth is validated.
  React.useEffect(() => {
    if (mockMode) return;
    const onPageShow = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      setUser(null);
      setLoading(true);
      void refresh();
    };
    window.addEventListener("pageshow", onPageShow);
    return () => window.removeEventListener("pageshow", onPageShow);
  }, [mockMode, refresh]);

  return (
    <AuthContext.Provider
      value={{ user, authModeConfig, loading: loading || modeLoading, refresh, logout, clear }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
