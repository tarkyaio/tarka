# Chat Context & Thread Initialization Fix

## Summary

Fixed critical bug where chat threads were not initializing properly, causing "Select thread..." to appear in both global and case chat contexts.

**Status**: ✅ Fixed & Tested
**Date**: 2026-02-17
**Related PR**: UX Enhancement Plan (Phases 1-3)

---

## Root Cause

### Problem 1: Incorrect API Usage
- **Frontend** was calling streaming endpoints (`POST /api/v1/chat/threads/global`, `POST /api/v1/chat/threads/case/{case_id}`) with `message: null`
- **Backend** streaming endpoints expected non-empty messages and returned errors for empty messages
- Result: `threadId` stayed null → UI showed "Select thread..." error

### Problem 2: Context/Thread State Mismatch
- Frontend determined `context` prop from **route** (immediate, synchronous)
- Frontend determined `messages` from **thread state** (async, from backend)
- When thread failed to load or didn't match route, context and messages were mismatched
- Result: Case page showing global prompts, or vice versa

---

## The Fix

### Backend Changes (`agent/api/webhook.py`)

**File**: `agent/api/webhook.py:2199-2218`

Added support for empty message initialization in `_thread_send_stream`:

```python
msg = str(sreq.message or "").strip()
if not msg:
    # Empty message = just initialize/return thread info (for UI initialization)
    from agent.memory.chat import list_messages

    okm, _msgm, msgs = list_messages(user_key=user_key, thread_id=thread_id, limit=50)
    messages_out = []
    if okm:
        for m in msgs:
            messages_out.append(
                {"role": m.role, "content": m.content, "created_at": m.created_at, "seq": m.seq}
            )

    yield _format_sse_event(
        "init",
        {
            "thread": {
                "thread_id": thr.thread_id,
                "kind": thr.kind,
                "case_id": thr.case_id,
                "title": thr.title,
            },
            "messages": messages_out,
        },
    )
    return
```

**What it does**:
- Accepts `message: null` for thread initialization (doesn't send a real message)
- Returns thread metadata and message history via SSE "init" event
- Works for both global and case threads

---

### Frontend Changes (`ui/src/ui/ChatHost.tsx`)

#### 1. **Thread Initialization** (Lines 127-233)

Rewrote `openGlobalThread()` and `openCaseThread()` to properly parse SSE responses:

```typescript
async function openGlobalThread() {
  // Calls POST /api/v1/chat/threads/global with message: null
  // Listens for SSE "init" event from backend
  // Extracts thread_id and messages from init event
  // Sets state: threadId, threadKind="global", messages
}

async function openCaseThread(caseId: string, runId: string | null) {
  // Calls POST /api/v1/chat/threads/case/{caseId} with message: null
  // Listens for SSE "init" event from backend
  // Extracts thread_id and messages from init event
  // Sets state: threadId, threadKind="case", threadCaseId, messages
}
```

#### 2. **Context State Management** (Lines 178-210, 359-367)

Fixed context determination and state reset:

```typescript
// Reset thread state when route changes (prevents stale data)
React.useEffect(() => {
  // ...
  if (routeChanged) {
    setMode("bubble");

    // CRITICAL: Reset thread state
    setThreadId(null);
    setMessages([]);
    setToolEvents([]);

    // Set expected thread kind immediately (optimistic)
    if (isCase) {
      setThreadKind("case");
      setThreadCaseId(routeCaseId);
    } else {
      setThreadKind("global");
      setThreadCaseId(null);
    }
  }
  // ...
}, [/* deps */]);

// Context based on thread state (not route)
const context =
  threadKind === "case" && threadCaseId
    ? { kind: "case", caseId: threadCaseId, runId, analysisJson }
    : { kind: "global" };
```

**Key improvements**:
- Resets stale thread state when navigating between routes
- Sets expected `threadKind` optimistically (before async load completes)
- Context now matches thread state (ensures prompts/messages alignment)

---

## Test Coverage

### Backend Tests (`tests/test_chat_thread_init.py`)

✅ **3 tests, all passing**

1. **`test_global_thread_init_with_empty_message`**
   - Verifies global thread endpoint accepts `message: null`
   - Verifies "init" event is returned with thread metadata
   - Checks `thread_id`, `kind: "global"`, `messages` array

2. **`test_case_thread_init_with_empty_message`**
   - Verifies case thread endpoint accepts `message: null`
   - Verifies "init" event includes case-specific fields
   - Checks `thread_id`, `kind: "case"`, `case_id`

3. **`test_empty_message_returns_existing_messages`**
   - Verifies init event includes message history
   - Tests that existing messages are returned in correct format

### Frontend Tests (`ui/src/ui/AssistantChatWidget.test.tsx`)

✅ **13 tests, all passing** (including 6 new tests)

**New tests added**:

4. **`Collapsible footer sections > renders collapsible suggested prompts section`**
   - Verifies toggle button renders
   - Verifies expand/collapse functionality
   - Checks that prompts appear when expanded

5. **`Collapsible footer sections > persists collapsed state to localStorage`**
   - Verifies `localStorage` is updated on toggle
   - Checks both expanded and collapsed states persist

6. **`Collapsible footer sections > restores collapsed state from localStorage on mount`**
   - Verifies component reads from `localStorage` on mount
   - Checks expanded state is restored correctly

7. **`Collapsible footer sections > shows badge with count of items`**
   - Verifies badge displays correct count (e.g., "3")
   - Checks badge is visible on toggle button

8. **`Context display > shows case context for case chat`**
   - Verifies case chat shows "Context: #case-123"
   - Checks case ID is truncated to first 7 characters

9. **`Context display > shows global context for global chat`**
   - Verifies global chat shows "Context: All cases"
   - Checks text is user-friendly (not "Context: global")

---

## Verification Steps

### Manual Testing

1. **Global Chat Initialization**
   ```bash
   # Navigate to inbox
   # Open chat (click bubble or dock)
   # Expected: "Context: All cases" appears immediately
   # Expected: Can send messages (no "Select thread..." error)
   ```

2. **Case Chat Initialization**
   ```bash
   # Navigate to any case page
   # Open chat
   # Expected: "Context: #abc1234" appears immediately
   # Expected: Shows case-specific prompts
   # Expected: Can send messages
   ```

3. **Context Switching**
   ```bash
   # Navigate from case → inbox
   # Expected: Context switches to "All cases"
   # Expected: Messages clear (no stale case messages)

   # Navigate from inbox → case
   # Expected: Context switches to case ID
   # Expected: Messages clear (no stale global messages)
   ```

### Automated Testing

```bash
# Backend tests
poetry run pytest tests/test_chat_thread_init.py -v

# Frontend tests
cd ui && npm test -- AssistantChatWidget.test.tsx --run
```

---

## Impact

### Before Fix
- ❌ "Select thread..." error in both global and case chat
- ❌ Context and messages frequently mismatched
- ❌ Stale messages from previous context
- ❌ Chat unusable without manual refresh

### After Fix
- ✅ Threads initialize immediately on chat open
- ✅ Context always matches displayed messages
- ✅ Clean state when switching between routes
- ✅ Chat fully functional

---

## Related Changes

This fix was part of the larger **Chat UI/UX Enhancement Plan**:

- ✅ **Phase 1**: Collapsible footer sections (merged with this fix)
- ✅ **Phase 2**: Enhanced agent personality (merged with this fix)
- ✅ **Phase 3**: Thinking indicator polish (merged with this fix)
- 🚧 **Phase 4**: Maximize mode with sidebar collapse (pending)
- 🚧 **Phase 5**: Reset UI preferences (pending)

---

## Files Changed

**Backend**:
- `agent/api/webhook.py` - Added empty message initialization
- `tests/test_chat_thread_init.py` - Added 3 test cases (NEW)

**Frontend**:
- `ui/src/ui/ChatHost.tsx` - Fixed thread initialization and context management
- `ui/src/ui/AssistantChatWidget.tsx` - Added collapsible sections
- `ui/src/ui/AssistantChatWidget.module.css` - Added collapsible styles, polished thinking indicator
- `ui/src/ui/AssistantChatWidget.test.tsx` - Added 6 test cases

**Backend Agent Personality**:
- `agent/chat/runtime_streaming.py` - Enhanced conversational tone
- `agent/chat/runtime.py` - Enhanced conversational tone (consistency)
- `agent/chat/global_runtime_streaming.py` - Enhanced conversational tone
- `agent/chat/global_runtime.py` - Enhanced conversational tone (consistency)

---

## Deployment Notes

1. **Backend**: Restart required to pick up `webhook.py` changes
2. **Frontend**: Hard refresh required (Cmd+Shift+R) to clear cache
3. **Database**: No migrations needed
4. **Dependencies**: Added `pytest-asyncio` to dev dependencies

---

## Future Work

- Consider adding a dedicated non-streaming initialization endpoint
- Add metric/logging for thread initialization success rate
- Consider auto-retry on initialization failure
