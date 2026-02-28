import React from "react";
import ReactMarkdown from "react-markdown";
import styles from "./AssistantChatWidget.module.css";
import type {
  ActionConfigResponse,
  AnalysisActionProposal,
  AnalysisJson,
  ChatMessage,
  ChatThreadItem,
  ChatToolEvent,
} from "../lib/types";

function MaterialIcon({ name, filled }: { name: string; filled?: boolean }) {
  return (
    <span
      className="material-symbols-outlined"
      style={{
        fontSize: 18,
        fontVariationSettings: filled
          ? '"FILL" 1, "wght" 600, "GRAD" 0, "opsz" 24'
          : '"FILL" 0, "wght" 600, "GRAD" 0, "opsz" 24',
      }}
      aria-hidden="true"
    >
      {name}
    </span>
  );
}

type SuggestedAction = { hypothesis_id: string; action: AnalysisActionProposal };

export function AssistantChatWidget({
  enabled,
  context,
  request,
  actionCfg,
  userInitials,
  mode,
  onModeChange,
  threads,
  activeThreadId,
  onSelectThread,
  messages,
  toolEvents,
  sending,
  loading,
  onSend,
  switchSuggestion,
}: {
  enabled: boolean;
  context:
    | { kind: "global" }
    | { kind: "case"; caseId: string; runId: string | null; analysisJson: AnalysisJson | null };
  request: <T>(path: string, init?: RequestInit) => Promise<T>;
  actionCfg: ActionConfigResponse | null;
  userInitials: string;
  mode: "bubble" | "floating" | "docked";
  onModeChange: (mode: "bubble" | "floating" | "docked") => void;
  threads: ChatThreadItem[];
  activeThreadId: string | null;
  onSelectThread: (threadId: string) => void;
  messages: ChatMessage[];
  toolEvents?: ChatToolEvent[] | null;
  sending: boolean;
  loading?: boolean;
  onSend: (text: string) => Promise<void>;
  switchSuggestion: null | {
    caseId: string;
    onSwitch: () => void;
    onDismiss: () => void;
  };
}) {
  const [minimized, setMinimized] = React.useState(false);

  const [input, setInput] = React.useState("");
  const [pendingFiles, setPendingFiles] = React.useState<File[]>([]);

  // Collapsible footer sections - start collapsed for more message space
  const [actionsExpanded, setActionsExpanded] = React.useState(() => {
    const saved = localStorage.getItem("chat-actions-expanded");
    return saved === "true";
  });

  const [promptsExpanded, setPromptsExpanded] = React.useState(() => {
    const saved = localStorage.getItem("chat-prompts-expanded");
    return saved === "true";
  });

  // Persist collapse state to localStorage
  React.useEffect(() => {
    localStorage.setItem("chat-actions-expanded", String(actionsExpanded));
  }, [actionsExpanded]);

  React.useEffect(() => {
    localStorage.setItem("chat-prompts-expanded", String(promptsExpanded));
  }, [promptsExpanded]);

  // Dragging (floating mode only)
  const [pos, setPos] = React.useState<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const dragRef = React.useRef<{ dx: number; dy: number } | null>(null);

  // Dragging for bubble mode
  const [bubblePos, setBubblePos] = React.useState<{ x: number; y: number } | null>(null);
  const [bubbleDragging, setBubbleDragging] = React.useState(false);
  const bubbleDragRef = React.useRef<{ dx: number; dy: number } | null>(null);
  const bubbleRef = React.useRef<HTMLDivElement | null>(null);
  const bubbleDidMove = React.useRef(false);

  const widgetRef = React.useRef<HTMLDivElement | null>(null);
  const bodyRef = React.useRef<HTMLDivElement | null>(null);
  const textRef = React.useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  const imageInputRef = React.useRef<HTMLInputElement | null>(null);

  const suggested: SuggestedAction[] = React.useMemo(() => {
    if (context.kind !== "case") return [];
    const analysisJson = context.analysisJson;
    const out: SuggestedAction[] = [];
    try {
      const hyps = analysisJson?.analysis?.hypotheses || [];
      for (const h of hyps || []) {
        const hid = String(h?.hypothesis_id || "").trim();
        const acts = (h as any)?.proposed_actions as AnalysisActionProposal[] | null | undefined;
        if (!hid || !acts || !Array.isArray(acts)) continue;
        for (const a of acts) out.push({ hypothesis_id: hid, action: a });
      }
    } catch {
      // ignore
    }
    return out;
  }, [context]);

  // Keep scrolled to bottom.
  React.useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages.length, minimized, toolEvents?.length]);

  function startDrag(e: React.MouseEvent) {
    if (mode !== "floating") return;
    const target = e.target as HTMLElement | null;
    // Don't start a drag when interacting with controls in the header (thread selector, buttons, etc).
    if (target && target.closest("button,select,input,textarea")) return;
    const node = widgetRef.current;
    if (!node) return;
    const r = node.getBoundingClientRect();
    const dx = e.clientX - r.left;
    const dy = e.clientY - r.top;
    dragRef.current = { dx, dy };
    setDragging(true);
    // Initialize position from current rect the first time we drag.
    if (!pos) setPos({ x: r.left, y: r.top });
    e.preventDefault();
  }

  React.useEffect(() => {
    function onMove(ev: MouseEvent) {
      if (!dragging) return;
      if (mode !== "floating") return;
      const d = dragRef.current;
      if (!d) return;
      const w = widgetRef.current;
      if (!w) return;
      const width = w.offsetWidth || 400;
      const height = w.offsetHeight || 640;
      const maxX = Math.max(0, window.innerWidth - width - 8);
      const maxY = Math.max(0, window.innerHeight - height - 8);
      const x = Math.min(maxX, Math.max(8, ev.clientX - d.dx));
      const y = Math.min(maxY, Math.max(8, ev.clientY - d.dy));
      setPos({ x, y });
    }
    function onUp() {
      if (!dragging) return;
      setDragging(false);
      dragRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging, mode]);

  // Bubble dragging effect
  React.useEffect(() => {
    function onMove(ev: MouseEvent) {
      if (!bubbleDragging) return;
      const d = bubbleDragRef.current;
      if (!d) return;
      const bubble = bubbleRef.current;
      if (!bubble) return;
      const width = 56; // bubble size from CSS
      const height = 56;
      const maxX = Math.max(0, window.innerWidth - width - 8);
      const maxY = Math.max(0, window.innerHeight - height - 8);
      const x = Math.min(maxX, Math.max(8, ev.clientX - d.dx));
      const y = Math.min(maxY, Math.max(8, ev.clientY - d.dy));
      setBubblePos({ x, y });
      bubbleDidMove.current = true;
    }
    function onUp() {
      if (!bubbleDragging) return;
      setBubbleDragging(false);
      bubbleDragRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [bubbleDragging]);

  async function send(text: string) {
    if (!enabled) return;
    const msg = text.trim();
    if (!msg && !pendingFiles.length) return;
    setInput("");
    const fileNote = pendingFiles.length
      ? `\n\nAttachments (local, not uploaded):\n${pendingFiles
          .slice(0, 8)
          .map((f) => `- ${f.name}${f.type ? ` (${f.type})` : ""}`)
          .join("\n")}`
      : "";
    const composed = `${msg || "(no text)"}${fileNote}`.trim();
    try {
      await onSend(composed);
    } catch {
      // Host handles errors; keep UI resilient.
    } finally {
      setPendingFiles([]);
    }
  }

  async function proposeAction(x: SuggestedAction) {
    if (!actionCfg?.enabled) return;
    if (context.kind !== "case") return;
    const caseId = context.caseId;
    const runId = context.runId;
    const at = String(x.action.action_type || "").trim();
    const title = String(x.action.title || "").trim();
    if (!at || !title) return;
    try {
      await request(`/api/v1/cases/${encodeURIComponent(caseId)}/actions/propose`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: runId,
          hypothesis_id: x.hypothesis_id,
          action_type: at,
          title,
          risk: x.action.risk || null,
          preconditions: x.action.preconditions || [],
          execution_payload: x.action.execution_payload || {},
          actor: "ui",
        }),
      });
    } catch {
      // no-op; actions are best-effort
    }
  }

  const chips =
    context.kind === "case"
      ? [
          {
            icon: "bolt",
            label: "Explain this graph",
            text: "Explain the most important signal in the evidence and why it matters.",
          },
          {
            icon: "history",
            label: "Compare to last week",
            text: "Compare current signals to last week (same time) and highlight what's different.",
          },
          {
            icon: "memory",
            label: "Similar cases",
            text: "Find similar incidents and summarize how they were resolved.",
          },
        ]
      : [
          {
            icon: "inbox",
            label: "How many CPU throttling?",
            text: "How many cases are family=cpu_throttling for team <team>?",
          },
          {
            icon: "graphic_eq",
            label: "How many noise?",
            text: "How many cases are classified as noisy?",
          },
          { icon: "groups", label: "Top teams", text: "Which teams have the most open cases?" },
        ];

  function onPickFiles(files: FileList | null) {
    if (!files || !files.length) return;
    const arr = Array.from(files);
    setPendingFiles((prev) => [...prev, ...arr].slice(0, 8));
  }

  function insertCodeBlock() {
    const el = textRef.current;
    const snippet = "```\\n// paste code here\\n```";
    if (!el) {
      setInput((v) => (v ? `${v}\n\n${snippet}` : snippet));
      return;
    }
    const start = el.selectionStart ?? input.length;
    const end = el.selectionEnd ?? input.length;
    const next = input.slice(0, start) + snippet + input.slice(end);
    setInput(next);
    requestAnimationFrame(() => {
      try {
        el.focus();
        const pos = start + 4;
        el.setSelectionRange(pos, pos);
      } catch {
        // ignore
      }
    });
  }

  if (!enabled) return null;

  if (mode === "bubble") {
    const bubbleStyle: React.CSSProperties = {};
    if (bubblePos) {
      bubbleStyle.left = bubblePos.x;
      bubbleStyle.top = bubblePos.y;
      bubbleStyle.right = "auto";
      bubbleStyle.bottom = "auto";
    }

    return (
      <div ref={bubbleRef} className={styles.launcher} style={bubbleStyle}>
        <button
          className={styles.launcherBtn}
          type="button"
          aria-label="Open Tarka chat"
          title="Open Tarka chat"
          onMouseDown={(e) => {
            const bubble = bubbleRef.current;
            if (!bubble) return;
            const r = bubble.getBoundingClientRect();
            const dx = e.clientX - r.left;
            const dy = e.clientY - r.top;
            bubbleDragRef.current = { dx, dy };
            bubbleDidMove.current = false;
            setBubbleDragging(true);
            // Initialize position from current rect the first time we drag.
            if (!bubblePos) setBubblePos({ x: r.left, y: r.top });
            e.preventDefault();
          }}
          onClick={() => {
            // Only open if we didn't actually drag
            if (!bubbleDidMove.current) {
              setMinimized(false);
              onModeChange("floating");
            }
            bubbleDidMove.current = false;
          }}
          style={{ cursor: bubbleDragging ? "grabbing" : "grab" }}
        >
          <MaterialIcon name="smart_toy" />
          <span className={styles.launcherDot} aria-hidden="true" />
        </button>
      </div>
    );
  }

  const cls = [
    styles.widget,
    mode === "floating" ? styles.widgetFixed : "",
    mode === "floating" ? styles.widgetResizable : "",
    mode === "docked" ? styles.widgetDocked : "",
    minimized ? styles.widgetMinimized : "",
  ]
    .filter(Boolean)
    .join(" ");

  const inlineStyle: React.CSSProperties = {};
  if (mode === "floating" && pos) {
    inlineStyle.left = pos.x;
    inlineStyle.top = pos.y;
    inlineStyle.right = "auto";
    inlineStyle.bottom = "auto";
  }

  return (
    <div
      ref={widgetRef}
      className={cls}
      style={inlineStyle}
      aria-label="Tarka Assistant chat"
      aria-busy={loading ? "true" : "false"}
      data-loading={loading ? "true" : "false"}
    >
      <div
        className={`${styles.header} ${dragging ? styles.headerDragging : ""}`}
        onMouseDown={startDrag}
        role="toolbar"
        aria-label="Chat header"
      >
        <div className={styles.headerLeft}>
          <div className={styles.botBadge} aria-hidden="true">
            <MaterialIcon name="smart_toy" />
            <span className={styles.onlineDot} />
          </div>
          <div className={styles.headerTitle}>
            <div className={styles.title}>Tarka</div>
            <div className={styles.subtitle}>
              {context.kind === "case"
                ? `Context: #${String(context.caseId || "").slice(0, 7)}`
                : "Context: All cases"}
            </div>
          </div>
        </div>

        <div className={styles.headerRight}>
          {threads && threads.length && context.kind === "global" ? (
            <select
              className={styles.threadSelect}
              value={activeThreadId || ""}
              onChange={(e) => {
                const v = e.target.value;
                if (v) onSelectThread(v);
              }}
              aria-label="Select chat thread"
              disabled={sending}
            >
              <option value="" disabled>
                Select thread…
              </option>
              {threads.map((t) => {
                const kind = t.kind === "case" ? "case" : "global";
                const cid = String(t.case_id || "");
                const label = kind === "global" ? "Global" : `Case #${cid.slice(0, 7)}`;
                return (
                  <option key={t.thread_id} value={t.thread_id}>
                    {label}
                  </option>
                );
              })}
            </select>
          ) : null}
          <button
            className={styles.iconBtn}
            type="button"
            title={mode === "docked" ? "Pop out (floating)" : "Expand (sidebar)"}
            onClick={() => {
              setMinimized(false);
              setPos(null);
              onModeChange(mode === "docked" ? "floating" : "docked");
            }}
          >
            <MaterialIcon name={mode === "docked" ? "open_in_new" : "dock_to_left"} />
          </button>
          <span className={styles.divider} aria-hidden="true" />
          <button
            className={styles.iconBtn}
            type="button"
            title="Minimize"
            onClick={() => {
              // Minimize to the launcher bubble.
              setMinimized(false);
              onModeChange("bubble");
            }}
          >
            <MaterialIcon name="remove" />
          </button>
          <button
            className={styles.iconBtn}
            type="button"
            title="Close"
            onClick={() => {
              setMinimized(false);
              onModeChange("bubble");
            }}
          >
            <MaterialIcon name="close" />
          </button>
        </div>
      </div>

      {minimized ? null : (
        <>
          <div ref={bodyRef} className={styles.body} role="log" aria-label="Chat transcript">
            <div className={styles.dayPill}>Today</div>
            {!messages.length ? (
              <div className={styles.emptySuggested}>
                {context.kind === "case"
                  ? "Ask a question about this case."
                  : "Ask a question about cases in the inbox."}
              </div>
            ) : null}
            {messages.map((m, idx) => {
              const isUser = m.role === "user";
              const content = String(m.content || "");
              const isThinking = !isUser && content.startsWith("_") && content.endsWith("_");

              return (
                <div
                  key={`${idx}-${m.role}`}
                  className={`${styles.row} ${isUser ? styles.rowUser : ""}`}
                >
                  {isUser ? (
                    <div className={styles.avatarUser} aria-hidden="true">
                      {userInitials}
                    </div>
                  ) : (
                    <div className={styles.avatarBot} aria-hidden="true">
                      <MaterialIcon name="smart_toy" />
                    </div>
                  )}
                  <div className={`${styles.msgCol} ${isUser ? styles.msgColUser : ""}`}>
                    <div className={styles.msgMeta}>{isUser ? "You" : "Tarka"}</div>
                    <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : ""}`}>
                      {isThinking ? (
                        <div className={styles.thinkingIndicator}>
                          <span className={styles.thinkingDots}>
                            <span>•</span>
                            <span>•</span>
                            <span>•</span>
                          </span>
                          <span className={styles.thinkingText}>
                            {content.replace(/^_|_$/g, "")}
                          </span>
                        </div>
                      ) : isUser ? (
                        content
                      ) : (
                        <ReactMarkdown className={styles.markdown}>{content}</ReactMarkdown>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}

            {toolEvents && toolEvents.length ? (
              <details className={styles.toolEvents} aria-label="Tool events">
                <summary className={styles.toolEventsSummary}>Tools ({toolEvents.length})</summary>
                <ul className={styles.toolEventsList}>
                  {toolEvents.slice(0, 8).map((ev, idx) => {
                    const outcome = String((ev as any)?.outcome || "").trim();
                    const summary = String((ev as any)?.summary || "").trim();
                    const label = summary || `${String(ev.tool)}${outcome ? ` • ${outcome}` : ""}`;
                    const muted =
                      outcome === "skipped_duplicate" ||
                      outcome === "empty" ||
                      outcome === "unavailable";
                    return (
                      <li
                        key={`${String(ev.tool)}-${idx}`}
                        className={`${styles.toolEventItem} ${muted ? styles.toolEventItemMuted : ""}`}
                      >
                        <span className={styles.toolEventName}>{String(ev.tool)}</span>
                        <span className={styles.toolEventSummary}>{label}</span>
                      </li>
                    );
                  })}
                </ul>
                {toolEvents.length > 8 ? (
                  <div className={styles.toolEventsMore}>Showing latest 8 tool events.</div>
                ) : null}
              </details>
            ) : null}
          </div>

          <div className={styles.footer}>
            {/* Composer - ALWAYS AT TOP, visible */}
            <div className={styles.composer}>
              <textarea
                className={styles.input}
                value={input}
                placeholder="Ask follow-up questions…"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(input);
                  }
                }}
                disabled={sending}
                ref={textRef}
              />
              <div className={styles.composerTools} aria-label="Composer tools">
                <button
                  className={styles.toolBtn}
                  type="button"
                  title="Attach file"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={sending}
                >
                  <MaterialIcon name="attach_file" />
                </button>
                <button
                  className={styles.toolBtn}
                  type="button"
                  title="Insert code snippet"
                  onClick={insertCodeBlock}
                  disabled={sending}
                >
                  <MaterialIcon name="code" />
                </button>
                <button
                  className={styles.toolBtn}
                  type="button"
                  title="Add screenshot"
                  onClick={() => imageInputRef.current?.click()}
                  disabled={sending}
                >
                  <MaterialIcon name="add_a_photo" />
                </button>
              </div>
              <button
                className={styles.sendBtn}
                type="button"
                disabled={sending || (!input.trim() && !pendingFiles.length)}
                onClick={() => void send(input)}
                title="Send"
              >
                <MaterialIcon name="arrow_upward" />
              </button>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              style={{ display: "none" }}
              multiple
              onChange={(e) => onPickFiles(e.target.files)}
            />
            <input
              ref={imageInputRef}
              type="file"
              style={{ display: "none" }}
              accept="image/*"
              onChange={(e) => onPickFiles(e.target.files)}
            />

            {pendingFiles.length ? (
              <div className={styles.pendingFiles} aria-label="Pending attachments">
                {pendingFiles.map((f, idx) => (
                  <span key={`${f.name}-${idx}`} className={styles.fileChip}>
                    <span className={styles.fileChipName} title={f.name}>
                      {f.name}
                    </span>
                    <button
                      className={styles.fileChipX}
                      type="button"
                      aria-label={`Remove ${f.name}`}
                      onClick={() => setPendingFiles((prev) => prev.filter((_, i) => i !== idx))}
                    >
                      <MaterialIcon name="close" />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}

            {switchSuggestion && context.kind === "global" ? (
              <div className={styles.switchSuggest} role="status" aria-label="Switch suggestion">
                <div className={styles.switchSuggestText}>
                  Switch to case{" "}
                  <span className="mono">#{String(switchSuggestion.caseId).slice(0, 7)}</span>?
                </div>
                <div className={styles.switchSuggestActions}>
                  <button
                    className={styles.switchSuggestBtnPrimary}
                    type="button"
                    onClick={switchSuggestion.onSwitch}
                    disabled={sending}
                  >
                    Switch
                  </button>
                  <button
                    className={styles.switchSuggestBtn}
                    type="button"
                    onClick={switchSuggestion.onDismiss}
                    disabled={sending}
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            ) : null}

            {/* Status row */}
            <div className={styles.statusRow}>
              <div className={styles.statusLeft}>
                <span className={styles.pulseDot} />
                <span>Tarka • Online</span>
              </div>
              <div>Markdown supported</div>
            </div>

            {/* Collapsible Suggested Prompts - BELOW STATUS */}
            {chips && chips.length > 0 && (
              <div className={styles.collapsibleSection}>
                <button
                  className={styles.sectionToggle}
                  onClick={() => setPromptsExpanded(!promptsExpanded)}
                  aria-expanded={promptsExpanded}
                  type="button"
                >
                  <MaterialIcon name={promptsExpanded ? "expand_less" : "expand_more"} />
                  <span className={styles.sectionToggleText}>
                    {promptsExpanded ? "Hide" : "Show"} quick prompts
                  </span>
                  <span className={styles.sectionToggleBadge}>{chips.length}</span>
                </button>
                {promptsExpanded && (
                  <div className={styles.chips} aria-label="Suggested prompts">
                    {chips.map((c) => (
                      <button
                        key={c.label}
                        className={styles.chip}
                        type="button"
                        onClick={() => {
                          setInput(c.text);
                        }}
                      >
                        <MaterialIcon name={c.icon} />
                        <span>{c.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Collapsible Suggested Actions - BELOW PROMPTS */}
            {context.kind === "case" && suggested.length > 0 && (
              <div className={styles.collapsibleSection}>
                <button
                  className={styles.sectionToggle}
                  onClick={() => setActionsExpanded(!actionsExpanded)}
                  aria-expanded={actionsExpanded}
                  type="button"
                >
                  <MaterialIcon name={actionsExpanded ? "expand_less" : "expand_more"} />
                  <span className={styles.sectionToggleText}>
                    {actionsExpanded ? "Hide" : "Show"} suggested actions
                  </span>
                  <span className={styles.sectionToggleBadge}>{suggested.length}</span>
                </button>
                {actionsExpanded && (
                  <div className={styles.suggestedList}>
                    {suggested.slice(0, 3).map((x, idx) => {
                      const isPrimary = idx === 0;
                      const icon = idx === 0 ? "terminal" : idx === 1 ? "history" : "edit_document";
                      return (
                        <button
                          key={`${x.hypothesis_id}-${idx}`}
                          className={`${styles.suggestedBtn} ${isPrimary ? styles.suggestedBtnPrimary : ""}`}
                          type="button"
                          onClick={() => void proposeAction(x)}
                          disabled={!actionCfg?.enabled || !context.runId}
                          title={
                            actionCfg?.enabled
                              ? "Propose this action (approval required)"
                              : "Actions are disabled by policy"
                          }
                        >
                          <span
                            className={`${styles.suggestedIconBox} ${isPrimary ? styles.suggestedIconBoxPrimary : ""}`}
                            aria-hidden="true"
                          >
                            <MaterialIcon name={icon} />
                          </span>
                          <span>{String(x.action.title || x.action.action_type || "Action")}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}

      <div className={styles.resizeHandle} aria-hidden="true">
        <svg
          width="12"
          height="12"
          viewBox="0 0 10 10"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <path d="M8 2L2 8" stroke="#94a3b8" strokeLinecap="round" strokeWidth="1.5" />
          <path d="M8 6L6 8" stroke="#94a3b8" strokeLinecap="round" strokeWidth="1.5" />
        </svg>
      </div>
    </div>
  );
}
