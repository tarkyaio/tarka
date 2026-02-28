/**
 * Vite middleware to mock SSE streaming endpoints when VITE_MOCK_API=1.
 *
 * This intercepts /api/v1/chat/threads/* endpoints and returns Server-Sent Events
 * formatted responses, since these endpoints are called via fetch() directly
 * and bypass the useApi() mock system.
 */

import type { Connect } from 'vite';

interface MockThread {
  thread_id: string;
  kind: "global" | "case";
  case_id: string | null;
  created_at: string;
  updated_at: string;
  messages: MockMessage[];
}

interface MockMessage {
  message_id: string;
  seq: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

// In-memory mock chat store (resets on server restart)
let mockChat = {
  nextThread: 1,
  nextMsg: 1,
  threads: new Map<string, MockThread>(),
  byKey: new Map<string, string>(),
};

// Reset function for tests
function resetMockChat() {
  mockChat = {
    nextThread: 1,
    nextMsg: 1,
    threads: new Map<string, MockThread>(),
    byKey: new Map<string, string>(),
  };
}

function mockNowIso() {
  return new Date().toISOString();
}

function mockEnsureThread(kind: "global" | "case", caseId: string | null): MockThread {
  const key = `${kind}:${caseId || ""}`;
  const existingId = mockChat.byKey.get(key);
  if (existingId) {
    const t = mockChat.threads.get(existingId);
    if (t) return t;
  }
  const id = `thread-${mockChat.nextThread++}`;
  const now = mockNowIso();
  const t: MockThread = {
    thread_id: id,
    kind,
    case_id: caseId,
    created_at: now,
    updated_at: now,
    messages: [],
  };
  mockChat.threads.set(id, t);
  mockChat.byKey.set(key, id);
  return t;
}

function mockAppendMessage(t: MockThread, role: "user" | "assistant", content: string): MockMessage {
  const now = mockNowIso();
  const m: MockMessage = {
    message_id: `msg-${mockChat.nextMsg++}`,
    seq: t.messages.length + 1,
    role,
    content,
    created_at: now,
  };
  t.messages.push(m);
  t.updated_at = now;
  return m;
}

function mockThreadToItem(t: MockThread) {
  return {
    thread_id: t.thread_id,
    kind: t.kind,
    case_id: t.case_id,
    title: null,
    created_at: t.created_at,
    updated_at: t.updated_at,
  };
}

export function createMockMiddleware(): Connect.NextHandleFunction {
  return (req, res, next) => {
    // Only intercept in mock mode
    if (process.env.VITE_MOCK_API !== "1") {
      return next();
    }

    const url = req.url || "";

    // Test helper: reset mock chat store between tests
    if (url === "/__test__/reset-mock-chat" && req.method === "POST") {
      mockChat = {
        nextThread: 1,
        nextMsg: 1,
        threads: new Map(),
        byKey: new Map(),
      };
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }

    // Handle POST /api/v1/chat/threads/global (with or without message)
    if (url === "/api/v1/chat/threads/global" && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => {
        body += chunk.toString();
      });
      req.on("end", () => {
        const data = body ? JSON.parse(body) : {};
        const msg = (typeof data.message === "string" && data.message.trim() !== "") ? data.message.trim() : "";
        const t = mockEnsureThread("global", null);

        if (msg) {
          // For thread init with a message, add to storage and return full response
          mockAppendMessage(t, "user", msg);
          const reply = `Mock global chat: received the message`;
          mockAppendMessage(t, "assistant", reply);

          // Return SSE stream with init (includes messages) + response events
          const sseEvents = [
            `event: init\ndata: ${JSON.stringify({ thread: mockThreadToItem(t), messages: t.messages })}\n\n`,
            `event: thinking\ndata: ${JSON.stringify({ content: "Thinking..." })}\n\n`,
            `event: token\ndata: ${JSON.stringify({ content: reply })}\n\n`,
            `event: done\ndata: ${JSON.stringify({ content: reply, metadata: { tool_events: [] } })}\n\n`,
          ].join("");

          res.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
          });
          res.write(sseEvents);
          res.end();
          return;
        }

        // Empty message = init only
        const sseEvents = `event: init\ndata: ${JSON.stringify({ thread: mockThreadToItem(t), messages: t.messages })}\n\n`;
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        });
        res.write(sseEvents);
        res.end();
      });
      return;
    }

    // Handle POST /api/v1/chat/threads/case/{caseId}
    const caseThreadMatch = url.match(/^\/api\/v1\/chat\/threads\/case\/([^?]+)/);
    if (caseThreadMatch && req.method === "POST") {
      const caseId = decodeURIComponent(caseThreadMatch[1]);
      let body = "";
      req.on("data", (chunk) => {
        body += chunk.toString();
      });
      req.on("end", () => {
        const data = body ? JSON.parse(body) : {};
        const msg = (typeof data.message === "string" && data.message.trim() !== "") ? data.message.trim() : "";
        const t = mockEnsureThread("case", caseId);

        if (msg) {
          mockAppendMessage(t, "user", msg);
          const reply = `Mock case chat: received the message`;
          mockAppendMessage(t, "assistant", reply);

          const sseEvents = [
            `event: init\ndata: ${JSON.stringify({ thread: mockThreadToItem(t), messages: t.messages })}\n\n`,
            `event: thinking\ndata: ${JSON.stringify({ content: "Thinking..." })}\n\n`,
            `event: token\ndata: ${JSON.stringify({ content: reply })}\n\n`,
            `event: done\ndata: ${JSON.stringify({ content: reply, metadata: { tool_events: [] } })}\n\n`,
          ].join("");

          res.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
          });
          res.write(sseEvents);
          res.end();
          return;
        }

        const sseEvents = `event: init\ndata: ${JSON.stringify({ thread: mockThreadToItem(t), messages: t.messages })}\n\n`;
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        });
        res.write(sseEvents);
        res.end();
      });
      return;
    }

    // Handle POST /api/v1/chat/threads/{threadId}/send
    const threadSendMatch = url.match(/^\/api\/v1\/chat\/threads\/([^/]+)\/send/);
    if (threadSendMatch && req.method === "POST") {
      const tid = decodeURIComponent(threadSendMatch[1]);
      let body = "";
      req.on("data", (chunk) => {
        body += chunk.toString();
      });
      req.on("end", () => {
        const data = body ? JSON.parse(body) : {};
        const msg = (typeof data.message === "string" && data.message.trim() !== "") ? data.message.trim() : "";
        const t = mockChat.threads.get(tid);

        if (!t) {
          res.writeHead(404, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Thread not found" }));
          return;
        }

        if (msg) {
          // Add to storage for persistence across navigation
          mockAppendMessage(t, "user", msg);
          const reply =
            t.kind === "case"
              ? `Mock case chat: received the message`
              : `Mock global chat: received the message`;
          mockAppendMessage(t, "assistant", reply);

          const sseEvents = [
            `event: thinking\ndata: ${JSON.stringify({ content: "Thinking..." })}\n\n`,
            `event: token\ndata: ${JSON.stringify({ content: reply })}\n\n`,
            `event: done\ndata: ${JSON.stringify({ content: reply, metadata: { tool_events: [] } })}\n\n`,
          ].join("");

          res.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
          });
          res.write(sseEvents);
          res.end();
          return;
        }

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
      });
      return;
    }

    next();
  };
}
