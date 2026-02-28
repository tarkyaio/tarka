import React from "react";
import type { AnalysisJson } from "../lib/types";

export type ChatMode = "bubble" | "floating" | "docked";

export type ActiveCaseChatContext = {
  caseId: string;
  runId: string | null;
  analysisJson: AnalysisJson | null;
};

type ChatShellState = {
  mode: ChatMode;
  setMode: (m: ChatMode) => void;

  activeCase: ActiveCaseChatContext | null;
  setActiveCase: (c: ActiveCaseChatContext | null) => void;
};

const Ctx = React.createContext<ChatShellState | null>(null);

export function ChatShellProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = React.useState<ChatMode>("bubble");
  const [activeCase, setActiveCase] = React.useState<ActiveCaseChatContext | null>(null);

  const v = React.useMemo(() => ({ mode, setMode, activeCase, setActiveCase }), [mode, activeCase]);
  return <Ctx.Provider value={v}>{children}</Ctx.Provider>;
}

export function useChatShell() {
  const v = React.useContext(Ctx);
  if (!v) throw new Error("useChatShell must be used within ChatShellProvider");
  return v;
}
