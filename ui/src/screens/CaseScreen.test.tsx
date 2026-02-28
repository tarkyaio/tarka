import React from "react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { fireEvent, render, screen } from "@testing-library/react";

// Mock auth: always logged in.
vi.mock("../state/auth", () => {
  const USER = { provider: "mock", email: "mock@example.com", name: "Mock User" };
  const noop = () => {};
  return {
    useAuth: () => ({
      user: USER,
      loading: false,
      basicToken: null,
      clear: noop,
      logout: async () => {},
      refresh: async () => USER,
      setBasicToken: noop,
      authMode: "disabled",
    }),
  };
});

// Mock API: return just enough for Shell + CaseScreen + ChatHost to render and enable chat.
vi.mock("../lib/api", () => {
  class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(message: string, status: number, body: unknown) {
      super(message);
      this.status = status;
      this.body = body;
    }
  }

  const request = async (path: string, init?: RequestInit) => {
    // Shell inbox badge.
    if (path.startsWith("/api/v1/cases?status=all&limit=1")) {
      return { total: 1, counts: { open: 1, closed: 0, total: 1 }, items: [] };
    }
    if (path === "/api/v1/chat/config") {
      return {
        enabled: true,
        allow_promql: true,
        allow_k8s_read: true,
        allow_logs_query: true,
        allow_argocd_read: false,
        allow_report_rerun: true,
        allow_memory_read: true,
        max_steps: 4,
        max_tool_calls: 6,
      };
    }
    if (path === "/api/v1/actions/config") {
      return {
        enabled: false,
        require_approval: true,
        allow_execute: false,
        action_type_allowlist: [],
        max_actions_per_case: 25,
      };
    }
    if (path.startsWith("/api/v1/chat/threads?")) {
      return {
        ok: true,
        items: [
          { thread_id: "t-global", kind: "global", case_id: null, title: null },
          { thread_id: "t-case-123", kind: "case", case_id: "case-123", title: null },
        ],
      };
    }
    if (path === "/api/v1/chat/threads/t-case-123?limit=50") {
      return {
        ok: true,
        thread: { thread_id: "t-case-123", kind: "case", case_id: "case-123" },
        messages: [],
      };
    }
    if (path === "/api/v1/chat/threads/case/case-123" && init?.method === "POST") {
      return {
        ok: true,
        thread: { thread_id: "t-case-123", kind: "case", case_id: "case-123" },
        messages: [],
      };
    }
    if (path.startsWith("/api/v1/cases/") && path.includes("?runs_limit=1")) {
      return {
        case: { case_id: "case-123", created_at: new Date().toISOString(), status: "open" },
        runs: [{ run_id: "run-123" }],
      };
    }
    if (path === "/api/v1/investigation-runs/run-123") {
      return {
        run: {
          run_id: "run-123",
          case_id: "case-123",
          created_at: new Date().toISOString(),
          alertname: "TestAlert",
          severity: "warning",
          classification: "test",
          one_liner: "One liner",
          confidence_score: 50,
          impact_score: 50,
          noise_score: 10,
          analysis_json: {
            analysis: {
              hypotheses: [],
              features: { family: "test" },
              verdict: { one_liner: "One liner" },
            },
            target: { service: "svc" },
          },
          report_text: "## Triage\nok",
        },
      };
    }
    if (path.startsWith("/api/v1/cases/") && path.includes("/memory")) {
      return { ok: true, enabled: false, similar_cases: [], skills: [], errors: [] };
    }
    throw new ApiError("Unhandled mock route", 501, { path });
  };

  return { useApi: () => ({ request }), ApiError };
});

import { CaseScreen } from "./CaseScreen";
import { Shell } from "./Shell";

describe("CaseScreen chat docking", () => {
  it("does not vanish when switching to docked mode", async () => {
    render(
      <MemoryRouter initialEntries={["/cases/case-123"]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/cases/:caseId" element={<CaseScreen />} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

    // Bubble launcher should exist once configs load.
    const launcher = await screen.findByRole("button", { name: /open tarka chat/i });
    fireEvent.click(launcher);

    // Widget appears (floating).
    expect(await screen.findByLabelText("Tarka Assistant chat")).toBeInTheDocument();

    // Dock it; widget should remain visible (regression for the reported bug).
    fireEvent.click(screen.getByTitle("Expand (sidebar)"));
    expect(screen.getByLabelText("Tarka Assistant chat")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Ask follow-up questions…")).toBeInTheDocument();
  });
});
