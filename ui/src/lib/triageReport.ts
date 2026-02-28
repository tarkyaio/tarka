import type { AnalysisJson } from "./types";

export function impactLabel(score?: number | null): string {
  if (score == null) return "—";
  if (score >= 85) return "High (Service Degradation)";
  if (score >= 60) return "Medium";
  return "Low";
}

export function pickPromql(
  analysisJson: AnalysisJson | null | undefined,
  max: number = 3
): Array<{ name: string; query: string }> {
  const promql = analysisJson?.analysis?.debug?.promql || null;
  if (!promql || typeof promql !== "object") return [];
  const out: Array<{ name: string; query: string }> = [];
  for (const [k, v] of Object.entries(promql)) {
    if (typeof v !== "string") continue;
    const q = v.trim();
    if (!q) continue;
    out.push({ name: k, query: q });
  }
  out.sort((a, b) => a.name.localeCompare(b.name));
  return out.slice(0, Math.max(0, max));
}

export function scoreTone(score?: number | null): "low" | "mid" | "high" | "muted" {
  if (score == null) return "muted";
  if (score >= 85) return "high";
  if (score >= 60) return "mid";
  return "low";
}

export function extractMarkdownSection(reportText: string, sectionTitle: string): string {
  const src = (reportText || "").replace(/\r\n/g, "\n");
  if (!src.trim()) return "";

  const lines = src.split("\n");
  const wanted = sectionTitle.trim().toLowerCase();
  const isH2 = (ln: string) => /^\s*##\s+/.test(ln);
  const h2Title = (ln: string) =>
    ln
      .replace(/^\s*##\s+/, "")
      .trim()
      .toLowerCase();

  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    if (!isH2(ln)) continue;
    if (h2Title(ln) === wanted) {
      start = i + 1;
      break;
    }
  }
  if (start < 0) return "";

  let end = lines.length;
  for (let i = start; i < lines.length; i++) {
    if (isH2(lines[i])) {
      end = i;
      break;
    }
  }

  // Trim blank padding.
  while (start < end && !lines[start].trim()) start++;
  while (end > start && !lines[end - 1].trim()) end--;

  return lines.slice(start, end).join("\n").trim();
}
