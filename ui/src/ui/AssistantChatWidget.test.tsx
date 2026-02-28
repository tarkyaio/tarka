import React from "react";
import { describe, expect, it, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AssistantChatWidget } from "./AssistantChatWidget";

function mkRequest() {
  return async function request<T>(_path: string, _init?: RequestInit): Promise<T> {
    return { reply: "ok", tool_events: [] } as unknown as T;
  };
}

function Host() {
  const [mode, setMode] = React.useState<"bubble" | "floating" | "docked">("bubble");
  const [messages, setMessages] = React.useState<{ role: "user" | "assistant"; content: string }[]>(
    []
  );
  const toolEvents = React.useMemo(
    () => [
      {
        tool: "logs.tail",
        args: {},
        ok: true,
        outcome: "empty",
        summary: "logs: empty (0 entries)",
        key: "logs.tail:deadbeef",
      },
    ],
    []
  );
  const threads = React.useMemo(
    () => [
      { thread_id: "t-global", kind: "global" as const, case_id: null },
      { thread_id: "t-case-123", kind: "case" as const, case_id: "case-123" },
    ],
    []
  );
  const [activeThreadId, setActiveThreadId] = React.useState<string | null>("t-case-123");

  return (
    <AssistantChatWidget
      enabled={true}
      context={{
        kind: "case",
        caseId: "case-123",
        runId: "run-123",
        analysisJson: {
          analysis: {
            hypotheses: [],
            features: { family: "test" },
            verdict: { one_liner: "x" },
          } as any,
          target: { service: "svc" } as any,
        } as any,
      }}
      request={mkRequest()}
      actionCfg={
        {
          enabled: false,
          require_approval: true,
          allow_execute: false,
          max_actions_per_case: 25,
        } as any
      }
      userInitials="ME"
      mode={mode}
      onModeChange={setMode}
      threads={threads as any}
      activeThreadId={activeThreadId}
      onSelectThread={(tid) => setActiveThreadId(tid)}
      messages={messages as any}
      toolEvents={toolEvents as any}
      sending={false}
      onSend={async (text) => {
        setMessages((prev) => [
          ...prev,
          { role: "user", content: text },
          { role: "assistant", content: "ok" },
        ]);
      }}
      switchSuggestion={null}
    />
  );
}

describe("AssistantChatWidget", () => {
  it("supports bubble → floating → docked → floating without unmounting content", async () => {
    render(<Host />);

    // Bubble stage renders launcher only.
    fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));

    // Floating stage.
    expect(screen.getByLabelText("Tarka Assistant chat")).toBeInTheDocument();
    expect(screen.getByLabelText("Chat header")).toBeInTheDocument();
    // Tool events panel (collapsed by default) should exist.
    expect(screen.getByLabelText("Tool events")).toBeInTheDocument();
    const input = screen.getByPlaceholderText("Ask follow-up questions…");
    // Draft should persist across mode transitions.
    fireEvent.change(input, { target: { value: "draft message" } });

    // Dock it (draft should still be present).
    fireEvent.click(screen.getByTitle("Expand (sidebar)"));
    expect(screen.getByLabelText("Tarka Assistant chat")).toBeInTheDocument();
    expect(screen.getByDisplayValue("draft message")).toBeInTheDocument();

    // Undock it (draft should still be present).
    fireEvent.click(screen.getByTitle("Pop out (floating)"));
    expect(screen.getByLabelText("Tarka Assistant chat")).toBeInTheDocument();
    expect(screen.getByDisplayValue("draft message")).toBeInTheDocument();

    // Minimize to bubble and re-open (draft should still be present).
    fireEvent.click(screen.getByTitle("Minimize"));
    fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));
    expect(screen.getByDisplayValue("draft message")).toBeInTheDocument();

    // Now send.
    fireEvent.change(screen.getByPlaceholderText("Ask follow-up questions…"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByTitle("Send"));

    // Our user message should appear.
    expect(await screen.findByText("hello")).toBeInTheDocument();

    // Minimize returns to bubble.
    fireEvent.click(screen.getByTitle("Minimize"));
    expect(screen.getByRole("button", { name: /open tarka chat/i })).toBeInTheDocument();
  });

  describe("Bubble dragging", () => {
    beforeEach(() => {
      cleanup();
    });

    it("allows bubble to be dragged to a new position", () => {
      render(<Host />);

      const button = screen.getByRole("button", { name: /open tarka chat/i });
      const launcher = button.parentElement;
      expect(launcher).toBeInTheDocument();

      // Get initial position (should be default bottom-right)
      const initialStyle = window.getComputedStyle(launcher!);
      expect(initialStyle.position).toBe("fixed");

      // Simulate drag: mousedown, mousemove, mouseup
      fireEvent.mouseDown(button, { clientX: 100, clientY: 100 });

      // Simulate mouse movement
      fireEvent.mouseMove(window, { clientX: 200, clientY: 150 });
      fireEvent.mouseUp(window);

      // After drag, the bubble should have custom positioning
      const launcherElement = launcher as HTMLElement;
      expect(launcherElement.style.left).toBeTruthy();
      expect(launcherElement.style.top).toBeTruthy();
    });

    it("does not open chat when bubble is dragged", () => {
      render(<Host />);

      const button = screen.getByRole("button", { name: /open tarka chat/i });

      // Simulate drag with movement
      fireEvent.mouseDown(button, { clientX: 100, clientY: 100 });
      fireEvent.mouseMove(window, { clientX: 200, clientY: 150 });
      fireEvent.mouseUp(window);

      // Click after drag (bubble moved)
      fireEvent.click(button);

      // Chat should NOT open because we dragged
      expect(screen.queryByLabelText("Tarka Assistant chat")).not.toBeInTheDocument();
    });

    it("opens chat when clicked without dragging", () => {
      render(<Host />);

      const button = screen.getByRole("button", { name: /open tarka chat/i });

      // Simulate click without movement: mousedown, mouseup (no mousemove)
      fireEvent.mouseDown(button, { clientX: 100, clientY: 100 });
      fireEvent.mouseUp(window);
      fireEvent.click(button);

      // Chat should open because we didn't drag
      expect(screen.getByLabelText("Tarka Assistant chat")).toBeInTheDocument();
    });

    it("constrains bubble position within viewport bounds", () => {
      // Mock window dimensions
      Object.defineProperty(window, "innerWidth", {
        writable: true,
        configurable: true,
        value: 1024,
      });
      Object.defineProperty(window, "innerHeight", {
        writable: true,
        configurable: true,
        value: 768,
      });

      render(<Host />);

      const button = screen.getByRole("button", { name: /open tarka chat/i });
      const launcher = button.parentElement as HTMLElement;

      // Try to drag beyond viewport bounds (far right and down)
      fireEvent.mouseDown(button, { clientX: 100, clientY: 100 });
      fireEvent.mouseMove(window, { clientX: 2000, clientY: 2000 });
      fireEvent.mouseUp(window);

      // Position should be constrained
      const left = parseInt(launcher.style.left || "0", 10);
      const top = parseInt(launcher.style.top || "0", 10);

      // Should not exceed viewport (accounting for bubble size of 56px and 8px padding)
      expect(left).toBeLessThanOrEqual(1024 - 56 - 8);
      expect(top).toBeLessThanOrEqual(768 - 56 - 8);
      expect(left).toBeGreaterThanOrEqual(8);
      expect(top).toBeGreaterThanOrEqual(8);
    });

    it("supports multiple drags without errors", () => {
      render(<Host />);

      const button = screen.getByRole("button", { name: /open tarka chat/i });
      const launcher = button.parentElement as HTMLElement;

      // First drag
      fireEvent.mouseDown(button, { clientX: 100, clientY: 100 });
      fireEvent.mouseMove(window, { clientX: 200, clientY: 150 });
      fireEvent.mouseUp(window);

      // Verify position was set
      expect(launcher.style.left).toBeTruthy();
      expect(launcher.style.top).toBeTruthy();

      // Second drag should work without errors
      fireEvent.mouseDown(button, { clientX: 150, clientY: 120 });
      fireEvent.mouseMove(window, { clientX: 250, clientY: 180 });
      fireEvent.mouseUp(window);

      // Verify position is still set
      expect(launcher.style.left).toBeTruthy();
      expect(launcher.style.top).toBeTruthy();

      // Third drag for good measure
      fireEvent.mouseDown(button, { clientX: 180, clientY: 140 });
      fireEvent.mouseMove(window, { clientX: 280, clientY: 200 });
      fireEvent.mouseUp(window);

      // Position should still be valid
      expect(launcher.style.left).toBeTruthy();
      expect(launcher.style.top).toBeTruthy();
    });

    it("changes cursor style during drag", () => {
      render(<Host />);

      const button = screen.getByRole("button", { name: /open tarka chat/i });

      // Initial cursor should be grab
      expect(button.style.cursor).toBe("grab");

      // During drag, cursor should change to grabbing
      fireEvent.mouseDown(button, { clientX: 100, clientY: 100 });
      expect(button.style.cursor).toBe("grabbing");

      // After drag, cursor should return to grab
      fireEvent.mouseMove(window, { clientX: 200, clientY: 150 });
      fireEvent.mouseUp(window);
      expect(button.style.cursor).toBe("grab");
    });
  });

  describe("Collapsible footer sections", () => {
    beforeEach(() => {
      cleanup();
      // Clear localStorage before each test
      localStorage.clear();
    });

    it("renders collapsible suggested prompts section", () => {
      render(<Host />);

      // Open chat
      fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));

      // Should show toggle button for prompts (collapsed by default)
      expect(screen.getByText(/show quick prompts/i)).toBeInTheDocument();

      // Click to expand
      fireEvent.click(screen.getByText(/show quick prompts/i));

      // Should now show "Hide" and the actual prompts
      expect(screen.getByText(/hide quick prompts/i)).toBeInTheDocument();
      expect(screen.getByText(/explain this graph/i)).toBeInTheDocument();
    });

    it("persists collapsed state to localStorage", () => {
      render(<Host />);
      fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));

      // Expand prompts
      fireEvent.click(screen.getByText(/show quick prompts/i));

      // Check localStorage
      expect(localStorage.getItem("chat-prompts-expanded")).toBe("true");

      // Collapse prompts
      fireEvent.click(screen.getByText(/hide quick prompts/i));

      // Check localStorage updated
      expect(localStorage.getItem("chat-prompts-expanded")).toBe("false");
    });

    it("restores collapsed state from localStorage on mount", () => {
      // Pre-set localStorage to expanded
      localStorage.setItem("chat-prompts-expanded", "true");

      render(<Host />);
      fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));

      // Should immediately show "Hide" (expanded state)
      expect(screen.getByText(/hide quick prompts/i)).toBeInTheDocument();
    });

    it("shows badge with count of items", () => {
      render(<Host />);
      fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));

      // Badge should show count (3 default prompts for case context)
      const toggle = screen.getByText(/show quick prompts/i).closest("button");
      expect(toggle).toHaveTextContent("3");
    });
  });

  describe("Context display", () => {
    beforeEach(() => {
      cleanup();
    });

    it("shows case context for case chat", () => {
      render(<Host />);
      fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));

      // Should show case ID in context
      expect(screen.getByText(/context: #case-12/i)).toBeInTheDocument();
    });

    it("shows global context for global chat", () => {
      function GlobalHost() {
        const [mode, setMode] = React.useState<"bubble" | "floating" | "docked">("bubble");
        const [messages] = React.useState<{ role: "user" | "assistant"; content: string }[]>([]);

        return (
          <AssistantChatWidget
            enabled={true}
            context={{ kind: "global" }}
            request={mkRequest()}
            actionCfg={{ enabled: false } as any}
            userInitials="ME"
            mode={mode}
            onModeChange={setMode}
            threads={[{ thread_id: "t-global", kind: "global", case_id: null }] as any}
            activeThreadId="t-global"
            onSelectThread={() => {}}
            messages={messages as any}
            toolEvents={[]}
            sending={false}
            onSend={async () => {}}
            switchSuggestion={null}
          />
        );
      }

      render(<GlobalHost />);
      fireEvent.click(screen.getByRole("button", { name: /open tarka chat/i }));

      // Should show "All cases" for global context
      expect(screen.getByText(/context: all cases/i)).toBeInTheDocument();
    });
  });
});
