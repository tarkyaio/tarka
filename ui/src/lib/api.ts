import { useAuth } from "../state/auth";
import { mockCaseDetail, mockInbox, mockRunDetail } from "../mocks/data";
import type { InboxRow } from "./types";
import React from "react";

// Feature flag: enable the unfinished "learning loop" screens/links.
// Off by default unless explicitly enabled via env.
export const ENABLE_LEARNING_LOOP = import.meta.env.VITE_ENABLE_LEARNING_LOOP === "1";

// --- Mock-mode server-persisted chat (in-memory, per browser tab) ---
type _MockStoredMessage = {
  message_id: string;
  seq: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};
type _MockThread = {
  thread_id: string;
  kind: "global" | "case";
  case_id: string | null;
  created_at: string;
  updated_at: string;
  messages: _MockStoredMessage[];
};
const _mockChat = {
  nextThread: 1,
  nextMsg: 1,
  threads: new Map<string, _MockThread>(),
  byKey: new Map<string, string>(), // `${kind}:${case_id||""}` -> thread_id
};

function _mockNowIso() {
  return new Date().toISOString();
}

function _mockEnsureThread(kind: "global" | "case", caseId: string | null): _MockThread {
  const key = `${kind}:${caseId || ""}`;
  const existingId = _mockChat.byKey.get(key);
  if (existingId) {
    const t = _mockChat.threads.get(existingId);
    if (t) return t;
  }
  const id = `thread-${_mockChat.nextThread++}`;
  const now = _mockNowIso();
  const t: _MockThread = {
    thread_id: id,
    kind,
    case_id: caseId,
    created_at: now,
    updated_at: now,
    messages: [],
  };
  _mockChat.threads.set(id, t);
  _mockChat.byKey.set(key, id);
  return t;
}

function _mockThreadToItem(t: _MockThread) {
  const last = t.messages.length ? t.messages[t.messages.length - 1] : null;
  return {
    thread_id: t.thread_id,
    kind: t.kind,
    case_id: t.case_id,
    title: null,
    created_at: t.created_at,
    updated_at: t.updated_at,
    last_message_at: last ? last.created_at : null,
    last_message: last
      ? { seq: last.seq, role: last.role, content: last.content, created_at: last.created_at }
      : null,
  };
}

function _mockAppendMessage(
  t: _MockThread,
  role: "user" | "assistant",
  content: string
): _MockStoredMessage {
  const now = _mockNowIso();
  const m: _MockStoredMessage = {
    message_id: `msg-${_mockChat.nextMsg++}`,
    seq: t.messages.length + 1,
    role,
    content,
    created_at: now,
  };
  t.messages.push(m);
  t.updated_at = now;
  return m;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export function useApi() {
  const { clear } = useAuth();

  const request = React.useCallback(
    async function request<T>(path: string, init?: RequestInit): Promise<T> {
      const mockMode = import.meta.env.VITE_MOCK_API === "1";
      if (mockMode) {
        // Light delay so loading states are visible while designing UX.
        await new Promise((r) => setTimeout(r, 180));
        const u = new URL(path, window.location.origin);
        const p = u.pathname;
        if (p === "/api/v1/cases") {
          const qp = u.searchParams;
          const status = (qp.get("status") || "open").trim().toLowerCase();
          const service = (qp.get("service") || "").trim();
          const classification = (qp.get("classification") || "").trim();
          const family = (qp.get("family") || "").trim();
          const team = (qp.get("team") || "").trim();
          const q = (qp.get("q") || "").trim();
          const limit = Math.max(1, parseInt(qp.get("limit") || "50", 10) || 50);
          const offset = Math.max(0, parseInt(qp.get("offset") || "0", 10) || 0);

          const qlc = q.toLowerCase();
          const svcLc = service.toLowerCase();
          const clsLc = classification.toLowerCase();
          const famLc = family.toLowerCase();
          const teamLc = team.toLowerCase();

          const filtered = (mockInbox.items || []).filter((it: InboxRow) => {
            const itStatus = String(it.case_status || "").toLowerCase();
            if (status && status !== "all" && itStatus !== status) return false;

            const itService = String(it.service || "");
            if (service && itService.toLowerCase() !== svcLc) return false;

            const itClassification = String(it.classification || "");
            if (classification && itClassification.toLowerCase() !== clsLc) return false;

            const itFamily = String(it.family || "");
            if (family && itFamily.toLowerCase() !== famLc) return false;

            const itTeam = String((it as any).team || "");
            if (team && itTeam.toLowerCase() !== teamLc) return false;

            if (qlc) {
              const hay = [
                it.case_id,
                it.one_liner,
                it.alertname,
                it.service,
                it.family,
                it.primary_driver,
              ]
                .map((x) => String(x || "").toLowerCase())
                .join(" ");
              if (!hay.includes(qlc)) return false;
            }

            return true;
          });

          const counts: Record<string, number> = { open: 0, closed: 0, total: filtered.length };
          for (const it of filtered) {
            const itStatus = String(it.case_status || "").toLowerCase();
            if (itStatus === "open") counts.open += 1;
            else if (itStatus === "closed") counts.closed += 1;
          }

          const items = filtered.slice(offset, offset + limit);

          return { total: filtered.length, counts, items } as unknown as T;
        }
        if (p === "/api/v1/cases/facets") {
          const qp = u.searchParams;
          const status = (qp.get("status") || "open").trim().toLowerCase();
          const service = (qp.get("service") || "").trim();
          const classification = (qp.get("classification") || "").trim();
          const family = (qp.get("family") || "").trim();
          const q = (qp.get("q") || "").trim();

          const qlc = q.toLowerCase();
          const svcLc = service.toLowerCase();
          const clsLc = classification.toLowerCase();
          const famLc = family.toLowerCase();

          const teams = new Set<string>();
          for (const it of mockInbox.items || []) {
            const itStatus = String(it.case_status || "").toLowerCase();
            if (status && status !== "all" && itStatus !== status) continue;

            const itService = String(it.service || "");
            if (service && itService.toLowerCase() !== svcLc) continue;

            const itClassification = String(it.classification || "");
            if (classification && itClassification.toLowerCase() !== clsLc) continue;

            const itFamily = String(it.family || "");
            if (family && itFamily.toLowerCase() !== famLc) continue;

            if (qlc) {
              const hay = [
                it.case_id,
                it.one_liner,
                it.alertname,
                it.service,
                it.family,
                it.primary_driver,
              ]
                .map((x) => String(x || "").toLowerCase())
                .join(" ");
              if (!hay.includes(qlc)) continue;
            }

            const t = String((it as any).team || "")
              .trim()
              .toLowerCase();
            if (t) teams.add(t);
          }

          return { teams: Array.from(teams).sort() } as unknown as T;
        }
        if (p === "/api/v1/chat/config") {
          // Default mock: chat disabled unless explicitly enabled in env (matches backend default).
          return {
            enabled: import.meta.env.VITE_CHAT_ENABLED === "1",
            allow_promql: true,
            allow_k8s_read: true,
            allow_logs_query: true,
            allow_argocd_read: false,
            allow_report_rerun: true,
            allow_memory_read: true,
            max_steps: 4,
            max_tool_calls: 6,
          } as unknown as T;
        }
        if (p === "/api/v1/actions/config") {
          return {
            enabled: import.meta.env.VITE_ACTIONS_ENABLED === "1",
            require_approval: true,
            allow_execute: false,
            action_type_allowlist: [
              "restart_pod",
              "rollout_restart",
              "scale_workload",
              "rollback_workload",
            ],
            max_actions_per_case: 25,
          } as unknown as T;
        }

        // --- Threaded chat endpoints (mock) ---
        if (p === "/api/v1/chat/threads") {
          _mockEnsureThread("global", null);
          const items = Array.from(_mockChat.threads.values())
            .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
            .map(_mockThreadToItem);
          return { ok: true, items } as unknown as T;
        }
        const threadGetMatch = p.match(/^\/api\/v1\/chat\/threads\/([^/]+)$/);
        if (threadGetMatch && init?.method !== "POST") {
          const tid = decodeURIComponent(threadGetMatch[1]);
          const t = _mockChat.threads.get(tid) || null;
          if (!t) throw new ApiError("Thread not found", 404, { path });
          return { ok: true, thread: _mockThreadToItem(t), messages: t.messages } as unknown as T;
        }
        const threadSendMatch = p.match(/^\/api\/v1\/chat\/threads\/([^/]+)\/send$/);
        if (threadSendMatch && init?.method === "POST") {
          const tid = decodeURIComponent(threadSendMatch[1]);
          const t = _mockChat.threads.get(tid) || null;
          if (!t) throw new ApiError("Thread not found", 404, { path });
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          const msg = String((body as any)?.message || "").trim();

          // Return SSE stream for /send endpoint (used by streaming chat)
          if (msg) {
            _mockAppendMessage(t, "user", msg);
            const reply =
              t.kind === "case"
                ? `Mock case chat: received "${msg.slice(0, 120)}"`
                : `Mock global chat: received "${msg.slice(0, 120)}"`;
            _mockAppendMessage(t, "assistant", reply);

            // Create SSE-formatted response
            const sseEvents = [
              `event: thinking\ndata: ${JSON.stringify({ content: "Thinking..." })}\n\n`,
              `event: token\ndata: ${JSON.stringify({ content: reply })}\n\n`,
              `event: done\ndata: ${JSON.stringify({ content: reply, metadata: { tool_events: [] } })}\n\n`,
            ].join("");

            // Return a Response with SSE stream
            const stream = new ReadableStream({
              start(controller) {
                controller.enqueue(new TextEncoder().encode(sseEvents));
                controller.close();
              },
            });

            return new Response(stream, {
              headers: {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                Connection: "keep-alive",
              },
            }) as unknown as T;
          }
          return { ok: true, thread: _mockThreadToItem(t), messages: t.messages } as unknown as T;
        }
        if (p === "/api/v1/chat/threads/global" && init?.method === "POST") {
          const t = _mockEnsureThread("global", null);
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          const msg = String((body as any)?.message || "").trim();
          if (msg) {
            _mockAppendMessage(t, "user", msg);
            const reply = `Mock global chat: received "${msg.slice(0, 120)}"`;
            _mockAppendMessage(t, "assistant", reply);

            // Return SSE stream with "init" event for thread initialization
            const sseEvents = [
              `event: init\ndata: ${JSON.stringify({ thread: _mockThreadToItem(t), messages: t.messages })}\n\n`,
              `event: thinking\ndata: ${JSON.stringify({ content: "Thinking..." })}\n\n`,
              `event: token\ndata: ${JSON.stringify({ content: reply })}\n\n`,
              `event: done\ndata: ${JSON.stringify({ content: reply, metadata: { tool_events: [] } })}\n\n`,
            ].join("");

            const stream = new ReadableStream({
              start(controller) {
                controller.enqueue(new TextEncoder().encode(sseEvents));
                controller.close();
              },
            });

            return new Response(stream, {
              headers: {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                Connection: "keep-alive",
              },
            }) as unknown as T;
          }
          // Empty message = init only (for thread initialization)
          const sseEvents = `event: init\ndata: ${JSON.stringify({ thread: _mockThreadToItem(t), messages: t.messages })}\n\n`;
          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode(sseEvents));
              controller.close();
            },
          });
          return new Response(stream, {
            headers: {
              "Content-Type": "text/event-stream",
              "Cache-Control": "no-cache",
              Connection: "keep-alive",
            },
          }) as unknown as T;
        }
        const caseThreadMatch = p.match(/^\/api\/v1\/chat\/threads\/case\/(.+)$/);
        if (caseThreadMatch && init?.method === "POST") {
          const caseId = decodeURIComponent(caseThreadMatch[1]);
          const t = _mockEnsureThread("case", caseId);
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          const msg = String((body as any)?.message || "").trim();
          if (msg) {
            _mockAppendMessage(t, "user", msg);
            const reply = `Mock case chat: received "${msg.slice(0, 120)}"`;
            _mockAppendMessage(t, "assistant", reply);

            // Return SSE stream with "init" event for thread initialization
            const sseEvents = [
              `event: init\ndata: ${JSON.stringify({ thread: _mockThreadToItem(t), messages: t.messages })}\n\n`,
              `event: thinking\ndata: ${JSON.stringify({ content: "Thinking..." })}\n\n`,
              `event: token\ndata: ${JSON.stringify({ content: reply })}\n\n`,
              `event: done\ndata: ${JSON.stringify({ content: reply, metadata: { tool_events: [] } })}\n\n`,
            ].join("");

            const stream = new ReadableStream({
              start(controller) {
                controller.enqueue(new TextEncoder().encode(sseEvents));
                controller.close();
              },
            });

            return new Response(stream, {
              headers: {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                Connection: "keep-alive",
              },
            }) as unknown as T;
          }
          // Empty message = init only (for thread initialization)
          const sseEvents = `event: init\ndata: ${JSON.stringify({ thread: _mockThreadToItem(t), messages: t.messages })}\n\n`;
          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(new TextEncoder().encode(sseEvents));
              controller.close();
            },
          });
          return new Response(stream, {
            headers: {
              "Content-Type": "text/event-stream",
              "Cache-Control": "no-cache",
              Connection: "keep-alive",
            },
          }) as unknown as T;
        }

        const caseChatMatch = p.match(/^\/api\/v1\/cases\/(.+)\/chat$/);
        if (caseChatMatch) {
          // Minimal deterministic mock reply.
          const body = init?.body ? JSON.parse(String(init.body)) : {};
          const msg = String((body as any)?.message || "");
          return { reply: `Mock chat: received "${msg.slice(0, 120)}"` } as unknown as T;
        }
        const caseMemoryMatch = p.match(/^\/api\/v1\/cases\/(.+)\/memory$/);
        if (caseMemoryMatch) {
          return {
            ok: true,
            enabled: true,
            similar_cases: [
              {
                case_id: "mock-case-2",
                run_id: "mock-run-9",
                created_at: new Date().toISOString(),
                one_liner: "Similar incident (mock): resolved by rollback.",
                resolution_category: "deploy",
                resolution_summary: "Rolled back to last known good.",
                postmortem_link: null,
              },
            ],
            skills: [],
            errors: [],
          } as unknown as T;
        }
        const caseActionsMatch = p.match(/^\/api\/v1\/cases\/(.+)\/actions$/);
        if (caseActionsMatch) {
          return { ok: true, items: [] } as unknown as T;
        }
        const proposeActionMatch = p.match(/^\/api\/v1\/cases\/(.+)\/actions\/propose$/);
        if (proposeActionMatch) {
          return { ok: true, action_id: "mock-action-1" } as unknown as T;
        }
        const approveActionMatch = p.match(/^\/api\/v1\/cases\/(.+)\/actions\/(.+)\/approve$/);
        if (approveActionMatch) {
          return { ok: true } as unknown as T;
        }
        const rejectActionMatch = p.match(/^\/api\/v1\/cases\/(.+)\/actions\/(.+)\/reject$/);
        if (rejectActionMatch) {
          return { ok: true } as unknown as T;
        }
        const executeActionMatch = p.match(/^\/api\/v1\/cases\/(.+)\/actions\/(.+)\/execute$/);
        if (executeActionMatch) {
          return { ok: true } as unknown as T;
        }
        const resolveMatch = p.match(/^\/api\/v1\/cases\/(.+)\/resolve$/);
        if (resolveMatch) {
          return { ok: true } as unknown as T;
        }
        const reopenMatch = p.match(/^\/api\/v1\/cases\/(.+)\/reopen$/);
        if (reopenMatch) {
          return { ok: true } as unknown as T;
        }
        const caseMatch = p.match(/^\/api\/v1\/cases\/(.+)$/);
        if (caseMatch) {
          return mockCaseDetail(decodeURIComponent(caseMatch[1])) as unknown as T;
        }
        const runMatch = p.match(/^\/api\/v1\/investigation-runs\/(.+)$/);
        if (runMatch) {
          return mockRunDetail(decodeURIComponent(runMatch[1])) as unknown as T;
        }
        throw new ApiError("Mock route not implemented", 501, { path });
      }

      const headers = new Headers(init?.headers || undefined);
      headers.set("Accept", "application/json");

      const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
      const text = await res.text();
      let body: unknown = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        body = text;
      }

      if (res.status === 401) {
        // force re-login UX
        clear();
      }
      if (!res.ok) {
        const msg =
          typeof body === "object" && body && "detail" in (body as any)
            ? String((body as any).detail)
            : res.statusText;
        throw new ApiError(msg || "Request failed", res.status, body);
      }
      return body as T;
    },
    [clear]
  );

  return React.useMemo(() => ({ request }), [request]);
}
