export function shortId(id: string, n: number = 8): string {
  const s = (id || "").trim();
  if (!s) return "—";
  return s.length <= n ? s : s.slice(0, n);
}

export function formatAge(iso: string): string {
  const s = (iso || "").trim();
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return "—";
  const now = Date.now();
  const ms = Math.max(0, now - d.getTime());
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) {
    const rem = m % 60;
    return rem ? `${h}h ${rem}m` : `${h}h`;
  }
  const days = Math.floor(h / 24);
  return `${days}d`;
}

export type Classification = "actionable" | "informational" | "noisy" | "artifact" | string;

export function classificationLabel(c?: string | null): string {
  const v = (c || "").toLowerCase();
  if (v === "actionable") return "Actionable";
  if (v === "informational") return "Informational";
  if (v === "noisy") return "Noise";
  if (v === "artifact") return "Artifact";
  return c || "Unknown";
}

export function fingerprint7(s: string): string {
  const digits = (s || "").replace(/\D/g, "");
  if (digits.length >= 7) return digits.slice(0, 7);
  const alnum = (s || "").replace(/[^a-zA-Z0-9]/g, "");
  if (alnum.length >= 7) return alnum.slice(0, 7);
  return alnum || "—";
}
