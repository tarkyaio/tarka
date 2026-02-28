/**
 * Custom hook for consuming Server-Sent Events (SSE) from streaming chat endpoints.
 *
 * Handles progressive token streaming with contextual thinking indicators and tool execution visibility.
 *
 * Event types:
 * - thinking: Contextual thinking message ("Analyzing case evidence...")
 * - planning: Tool planning in progress
 * - tool_start: Tool execution begins
 * - tool_end: Tool execution completes
 * - token: Batched response tokens
 * - done: Final complete response
 * - error: Error occurred during streaming
 */

import type { ChatToolEvent } from "./types";

export interface StreamingChatCallbacks {
  onThinking?: (content: string) => void;
  onPlanning?: (content: string) => void;
  onToolStart?: (tool: string, content: string) => void;
  onToolEnd?: (tool: string, outcome: string, content: string) => void;
  onToken?: (token: string) => void;
  onDone?: (reply: string, toolEvents: ChatToolEvent[]) => void;
  onError?: (error: string) => void;
}

export function useStreamingChat() {
  /**
   * Send a streaming message and receive progressive updates via callbacks.
   *
   * Returns cleanup function to cancel the stream.
   */
  const sendStreamingMessage = async (
    threadId: string,
    message: string,
    runId: string | null,
    callbacks: StreamingChatCallbacks
  ): Promise<() => void> => {
    // Build URL
    const url = new URL(
      `/api/v1/chat/threads/${encodeURIComponent(threadId)}/send`,
      window.location.origin
    );

    // Use fetch with streaming body (EventSource doesn't support POST)
    const response = await fetch(url.toString(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message,
        run_id: runId,
        limit: 50,
      }),
    });

    if (!response.ok) {
      throw new Error(`Stream failed: ${response.statusText}`);
    }

    if (!response.body) {
      throw new Error("Response body is null");
    }

    // Parse SSE stream
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let replyBuffer = "";
    let aborted = false;

    const processChunk = async (): Promise<void> => {
      if (aborted) return;

      try {
        const { done, value } = await reader.read();
        if (done) return;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || ""; // Keep incomplete line

        let currentEvent = "";
        let currentData = "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            currentData = line.slice(6);
          } else if (line === "") {
            // Empty line = end of event
            if (currentEvent && currentData) {
              try {
                const data = JSON.parse(currentData);

                switch (currentEvent) {
                  case "thinking":
                    callbacks.onThinking?.(data.content || "");
                    break;

                  case "planning":
                    callbacks.onPlanning?.(data.content || "");
                    break;

                  case "tool_start":
                    callbacks.onToolStart?.(data.tool || "", data.content || "");
                    break;

                  case "tool_end":
                    callbacks.onToolEnd?.(
                      data.tool || "",
                      data.metadata?.outcome || "unknown",
                      data.content || ""
                    );
                    break;

                  case "token":
                    replyBuffer += data.content || "";
                    callbacks.onToken?.(data.content || "");
                    break;

                  case "done":
                    callbacks.onDone?.(
                      replyBuffer,
                      (data.metadata?.tool_events as ChatToolEvent[]) || []
                    );
                    break;

                  case "error":
                    callbacks.onError?.(data.error || "Unknown error");
                    break;
                }
              } catch (err) {
                console.error("Failed to parse SSE data:", err, currentData);
              }
            }
            currentEvent = "";
            currentData = "";
          }
        }

        // Continue reading
        await processChunk();
      } catch (err) {
        if (!aborted) {
          callbacks.onError?.((err as Error).message);
        }
      }
    };

    // Start processing
    processChunk().catch((err) => {
      if (!aborted) {
        callbacks.onError?.(err.message);
      }
    });

    // Return cleanup function
    return () => {
      aborted = true;
      reader.cancel().catch(() => {
        // Ignore cancel errors
      });
    };
  };

  return { sendStreamingMessage };
}
