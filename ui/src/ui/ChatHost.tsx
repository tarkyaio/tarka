import React from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useApi } from "../lib/api";
import type {
  ActionConfigResponse,
  ChatConfigResponse,
  ChatMessage,
  ChatToolEvent,
  ChatThreadGetResponse,
  ChatThreadItem,
  ChatThreadsListResponse,
  ChatThreadSendResponse,
  ChatStoredMessage,
} from "../lib/types";
import { useAuth } from "../state/auth";
import { useChatShell } from "../state/chat";
import { useStreamingChat } from "../lib/useStreamingChat";
import { AssistantChatWidget } from "./AssistantChatWidget";

function _isCasePath(pathname: string): boolean {
  return (pathname || "").startsWith("/cases/");
}

function _extractCaseId(pathname: string): string {
  if (!_isCasePath(pathname)) return "";
  return decodeURIComponent(pathname.slice("/cases/".length).split("/")[0] || "");
}

function _computeUserInitials(user: any): string {
  const email = String(user?.email || "").trim();
  if (email) {
    const head = email.split("@")[0] || "";
    const parts = head.split(/[._-]+/g).filter(Boolean);
    const a = (parts[0] || head).slice(0, 1).toUpperCase();
    const b = (parts[1] || "").slice(0, 1).toUpperCase();
    return (a + b).slice(0, 2) || "ME";
  }
  const name = String(user?.name || "").trim();
  if (!name) return "ME";
  const ps = name.split(/\s+/g).filter(Boolean);
  return ((ps[0]?.[0] || "") + (ps[1]?.[0] || "")).toUpperCase().slice(0, 2) || "ME";
}

function _toTranscriptMessages(msgs: ChatStoredMessage[]): ChatMessage[] {
  return (msgs || []).map((m) => ({
    role: m.role === "user" ? "user" : "assistant",
    content: String(m.content || ""),
  }));
}

function _detectCaseIdFromText(text: string): string | null {
  const s = String(text || "");
  const m1 = s.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
  if (m1 && m1[0]) return m1[0];
  const m2 = s.match(/\/cases\/([^?\s/]+)/i);
  if (m2 && m2[1]) return String(m2[1]);
  return null;
}

export function ChatHost() {
  const loc = useLocation();
  const nav = useNavigate();
  const { request } = useApi();
  const { user } = useAuth();
  const { mode, setMode, activeCase } = useChatShell();
  const { sendStreamingMessage } = useStreamingChat();

  const [chatCfg, setChatCfg] = React.useState<ChatConfigResponse | null>(null);
  const [actionCfg, setActionCfg] = React.useState<ActionConfigResponse | null>(null);

  const [threads, setThreads] = React.useState<ChatThreadItem[]>([]);
  const [threadId, setThreadId] = React.useState<string | null>(null);
  const [threadKind, setThreadKind] = React.useState<"global" | "case">("global");
  const [threadCaseId, setThreadCaseId] = React.useState<string | null>(null);
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [toolEvents, setToolEvents] = React.useState<ChatToolEvent[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [switchSuggestionCaseId, setSwitchSuggestionCaseId] = React.useState<string | null>(null);

  const pathname = loc.pathname || "";
  const isCase = _isCasePath(pathname);
  const routeCaseId = isCase ? _extractCaseId(pathname) : "";

  const userInitials = React.useMemo(() => _computeUserInitials(user), [user]);

  // Track previous route context to detect navigation
  const prevRouteRef = React.useRef<{ isCase: boolean; routeCaseId: string } | null>(null);

  // Fetch chat config once (policy-gated). If unavailable, chat remains hidden.
  React.useEffect(() => {
    if (!user) {
      setChatCfg(null);
      return;
    }
    request<ChatConfigResponse>("/api/v1/chat/config")
      .then((cfg) => setChatCfg(cfg))
      .catch(() => setChatCfg(null));
  }, [user, request]);

  // Fetch action config once (used for Suggested Actions button in chat).
  React.useEffect(() => {
    if (!user) {
      setActionCfg(null);
      return;
    }
    request<ActionConfigResponse>("/api/v1/actions/config")
      .then((cfg) => setActionCfg(cfg))
      .catch(() => setActionCfg(null));
  }, [user, request]);

  async function refreshThreadsList() {
    if (!user) return;
    try {
      const d = await request<ChatThreadsListResponse>("/api/v1/chat/threads?limit=50");
      setThreads((d && (d as any).items) || []);
    } catch {
      setThreads([]);
    }
  }

  // Keep the thread list warm (for switcher).
  React.useEffect(() => {
    if (!user) return;
    void refreshThreadsList();
  }, [user]); // eslint-disable-line react-hooks/exhaustive-deps

  async function openGlobalThread() {
    setLoading(true);
    try {
      // Call streaming endpoint with null message to initialize thread and get messages
      // Backend now returns thread info via SSE "init" event instead of error
      await new Promise<void>((resolve, reject) => {
        const url = new URL("/api/v1/chat/threads/global", window.location.origin);
        let threadInitialized = false;

        fetch(url.toString(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: null, limit: 50 }),
        })
          .then(async (response) => {
            if (!response.ok) {
              reject(new Error(`Failed to initialize thread: ${response.statusText}`));
              return;
            }
            if (!response.body) {
              reject(new Error("Response body is null"));
              return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            // eslint-disable-next-line no-constant-condition
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split("\n");
              buffer = lines.pop() || "";

              let currentEvent = "";
              let currentData = "";

              for (const line of lines) {
                if (line.startsWith("event: ")) {
                  currentEvent = line.slice(7).trim();
                } else if (line.startsWith("data: ")) {
                  currentData = line.slice(6);
                } else if (line === "") {
                  if (currentEvent === "init" && currentData) {
                    try {
                      const data = JSON.parse(currentData);
                      const t = data.thread;
                      const msgs = data.messages || [];

                      if (t?.thread_id) {
                        setThreadId(t.thread_id);
                        setThreadKind("global");
                        setThreadCaseId(null);
                        setMessages(_toTranscriptMessages(msgs));
                        setToolEvents([]);
                        threadInitialized = true;
                        resolve();
                        return;
                      } else {
                        reject(new Error("Thread initialization failed: no thread_id in response"));
                        return;
                      }
                    } catch (e) {
                      reject(e);
                      return;
                    }
                  } else if (currentEvent === "error") {
                    try {
                      const data = JSON.parse(currentData);
                      reject(new Error(data.error || "Unknown error"));
                      return;
                    } catch {
                      reject(new Error("Failed to parse error"));
                      return;
                    }
                  }
                  currentEvent = "";
                  currentData = "";
                }
              }
            }

            // Stream ended without init event
            if (!threadInitialized) {
              reject(new Error("Thread initialization failed: no init event received"));
            } else {
              resolve();
            }
          })
          .catch(reject);
      });
    } catch (e) {
      console.error("Failed to open global thread:", e);
    } finally {
      setLoading(false);
      void refreshThreadsList();
    }
  }

  async function openCaseThread(caseId: string, runId: string | null) {
    if (!caseId) return;
    setLoading(true);
    try {
      // Call streaming endpoint with null message to initialize thread and get messages
      await new Promise<void>((resolve, reject) => {
        const url = new URL(
          `/api/v1/chat/threads/case/${encodeURIComponent(caseId)}`,
          window.location.origin
        );
        let threadInitialized = false;

        fetch(url.toString(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: null, run_id: runId, limit: 50 }),
        })
          .then(async (response) => {
            if (!response.ok) {
              reject(new Error(`Failed to initialize thread: ${response.statusText}`));
              return;
            }
            if (!response.body) {
              reject(new Error("Response body is null"));
              return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            // eslint-disable-next-line no-constant-condition
            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split("\n");
              buffer = lines.pop() || "";

              let currentEvent = "";
              let currentData = "";

              for (const line of lines) {
                if (line.startsWith("event: ")) {
                  currentEvent = line.slice(7).trim();
                } else if (line.startsWith("data: ")) {
                  currentData = line.slice(6);
                } else if (line === "") {
                  if (currentEvent === "init" && currentData) {
                    try {
                      const data = JSON.parse(currentData);
                      const t = data.thread;
                      const msgs = data.messages || [];

                      if (t?.thread_id) {
                        setThreadId(t.thread_id);
                        setThreadKind("case");
                        setThreadCaseId(caseId);
                        setMessages(_toTranscriptMessages(msgs));
                        setToolEvents([]);
                        threadInitialized = true;
                        resolve();
                        return;
                      } else {
                        reject(new Error("Thread initialization failed: no thread_id in response"));
                        return;
                      }
                    } catch (e) {
                      reject(e);
                      return;
                    }
                  } else if (currentEvent === "error") {
                    try {
                      const data = JSON.parse(currentData);
                      reject(new Error(data.error || "Unknown error"));
                      return;
                    } catch {
                      reject(new Error("Failed to parse error"));
                      return;
                    }
                  }
                  currentEvent = "";
                  currentData = "";
                }
              }
            }

            // Stream ended without init event
            if (!threadInitialized) {
              reject(new Error("Thread initialization failed: no init event received"));
            } else {
              resolve();
            }
          })
          .catch(reject);
      });
    } catch (e) {
      console.error("Failed to open case thread:", e);
    } finally {
      setLoading(false);
      void refreshThreadsList();
    }
  }

  // Route-driven selection: inbox -> global thread, case -> that case thread.
  React.useEffect(() => {
    if (!user) return;
    if (!chatCfg?.enabled) return;

    // Reset to bubble mode and clear thread state when navigating between different route contexts
    const prev = prevRouteRef.current;
    const routeChanged = !prev || prev.isCase !== isCase || prev.routeCaseId !== routeCaseId;
    if (routeChanged) {
      setMode("bubble");
      prevRouteRef.current = { isCase, routeCaseId };

      // CRITICAL: Reset thread state to prevent stale data from previous route
      setThreadId(null);
      setMessages([]);
      setToolEvents([]);
      setSwitchSuggestionCaseId(null);

      // Set expected thread kind immediately (optimistic, before async load)
      if (isCase) {
        setThreadKind("case");
        setThreadCaseId(routeCaseId);
      } else {
        setThreadKind("global");
        setThreadCaseId(null);
      }
    }

    if (isCase) {
      const runId = activeCase?.caseId === routeCaseId ? activeCase.runId : null;
      void openCaseThread(routeCaseId, runId);
    } else {
      void openGlobalThread();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, chatCfg?.enabled, isCase, routeCaseId, activeCase?.caseId, activeCase?.runId]);

  async function selectThread(nextThreadId: string) {
    if (!user) return;
    if (!nextThreadId) return;
    setLoading(true);
    try {
      const d = await request<ChatThreadGetResponse>(
        `/api/v1/chat/threads/${encodeURIComponent(nextThreadId)}?limit=50`
      );
      const t = (d as any)?.thread as ChatThreadItem | undefined;
      const msgs = ((d as any)?.messages || []) as ChatStoredMessage[];
      if (t?.thread_id) {
        setThreadId(t.thread_id);
        setThreadKind((t.kind as any) === "case" ? "case" : "global");
        setThreadCaseId((t as any).case_id || null);
        setMessages(_toTranscriptMessages(msgs));
        setToolEvents([]);
      }
    } finally {
      setLoading(false);
      void refreshThreadsList();
    }
  }

  async function sendToThread(text: string) {
    if (!user) return;
    if (!chatCfg?.enabled) return;
    if (!threadId) return;
    const msg = (text || "").trim();
    if (!msg) return;
    const detectedCase = threadKind === "global" ? _detectCaseIdFromText(msg) : null;

    // Optimistic: add user message
    setMessages((prev) => [...prev, { role: "user", content: msg }]);

    // Add empty assistant message that will stream
    const assistantMsgIndex = messages.length + 1;
    setMessages((prev) => [...prev, { role: "assistant", content: "_Sending request..._" }]);

    setLoading(true);

    try {
      const runId =
        threadKind === "case" && activeCase?.caseId === threadCaseId ? activeCase.runId : null;

      await sendStreamingMessage(threadId, msg, runId, {
        onThinking: (content) => {
          // Update message with thinking indicator
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantMsgIndex] = {
              role: "assistant",
              content: `_${content}_`,
            };
            return updated;
          });
        },

        onPlanning: (content) => {
          // Update with planning indicator (optional - can be same as thinking)
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantMsgIndex] = {
              role: "assistant",
              content: `_${content}_`,
            };
            return updated;
          });
        },

        onToolStart: (tool, summary) => {
          // Append tool execution indicator
          setMessages((prev) => {
            const updated = [...prev];
            const current = updated[assistantMsgIndex].content;
            // Strip previous thinking/planning indicator and add tool indicator
            const stripped = current.replace(/^_.*?_$/, "");
            updated[assistantMsgIndex] = {
              role: "assistant",
              content: stripped ? `${stripped}\n\n_${summary}_` : `_${summary}_`,
            };
            return updated;
          });
        },

        onToolEnd: (tool, outcome, summary) => {
          // Tool completed - indicator will be replaced by next tool or final response
          // Optional: could show completion briefly
        },

        onToken: (token) => {
          // Accumulate tokens, replacing thinking/tool indicators
          setMessages((prev) => {
            const updated = [...prev];
            let current = updated[assistantMsgIndex].content;

            // Strip thinking/tool indicators on first token
            if (current.match(/^_.*_$/)) {
              current = "";
            } else {
              // Remove inline thinking/tool indicators
              current = current.replace(/_[^_]+_\n\n/g, "");
            }

            updated[assistantMsgIndex] = {
              role: "assistant",
              content: current + token,
            };
            return updated;
          });
        },

        onDone: (reply, toolEvents) => {
          // Ensure final message is correct
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantMsgIndex] = {
              role: "assistant",
              content: reply,
            };
            return updated;
          });
          setToolEvents(toolEvents || []);
          setLoading(false);
          if (detectedCase) setSwitchSuggestionCaseId(detectedCase);
          void refreshThreadsList();
        },

        onError: (error) => {
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantMsgIndex] = {
              role: "assistant",
              content: `Error: ${error}. Please try again.`,
            };
            return updated;
          });
          setLoading(false);
          void refreshThreadsList();
        },
      });
    } catch (err) {
      setMessages((prev) => {
        const updated = [...prev];
        updated[assistantMsgIndex] = {
          role: "assistant",
          content: `Send failed: ${(err as Error).message}. Please try again.`,
        };
        return updated;
      });
      setLoading(false);
      void refreshThreadsList();
    }
  }

  const enabled = Boolean(user && chatCfg?.enabled);
  if (!enabled) return null;

  // Context should match the actual thread state, not the route
  // This ensures the UI (prompts, context label) matches the messages being displayed
  const context =
    threadKind === "case" && threadCaseId
      ? {
          kind: "case" as const,
          caseId: threadCaseId,
          runId: activeCase?.caseId === threadCaseId ? activeCase.runId : null,
          analysisJson: activeCase?.caseId === threadCaseId ? activeCase.analysisJson : null,
        }
      : { kind: "global" as const };

  return (
    <AssistantChatWidget
      enabled={true}
      context={context}
      request={request}
      actionCfg={actionCfg}
      userInitials={userInitials}
      mode={mode}
      onModeChange={setMode}
      threads={threads}
      activeThreadId={threadId}
      onSelectThread={selectThread}
      messages={messages}
      toolEvents={toolEvents}
      sending={loading}
      loading={loading}
      onSend={sendToThread}
      switchSuggestion={
        switchSuggestionCaseId
          ? {
              caseId: switchSuggestionCaseId,
              onSwitch: () => {
                const cid = switchSuggestionCaseId;
                setSwitchSuggestionCaseId(null);
                void openCaseThread(cid, null);
                nav(`/cases/${encodeURIComponent(cid)}`);
              },
              onDismiss: () => setSwitchSuggestionCaseId(null),
            }
          : null
      }
    />
  );
}
