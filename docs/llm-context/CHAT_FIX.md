# Chat Fix: React Hook Usage Error

## Issue

Chat was completely broken - user messages appeared but no responses came back.

## Root Cause

**Incorrect Hook Usage:** The `useStreamingChat` hook was being dynamically imported and called inside an async function, which violates React's Rules of Hooks.

### What Was Wrong

```typescript
// ❌ WRONG - Inside async function
async function sendToThread(text: string) {
  // ...

  // This violates Rules of Hooks:
  // 1. Hooks can't be called conditionally
  // 2. Hooks can't be called inside async functions
  // 3. Hooks must be called at component top level
  const { sendStreamingMessage } = await import("../lib/useStreamingChat").then(
    (m) => m.useStreamingChat()  // ❌ Calling hook dynamically
  );

  await sendStreamingMessage(threadId, msg, runId, {...});
}
```

### Why This Failed

1. **Dynamic Import**: `await import()` loads the module asynchronously
2. **Hook Call**: `m.useStreamingChat()` calls the hook inside an async function
3. **React Rules Violation**: Hooks MUST be called at the top level of a component, not:
   - Inside loops
   - Inside conditions
   - Inside nested functions
   - Inside async functions

This caused the streaming functionality to never initialize, so no responses were received.

## Solution

### Step 1: Static Import

```typescript
// ✅ CORRECT - Static import at top of file
import { useStreamingChat } from "../lib/useStreamingChat";
```

### Step 2: Call Hook at Component Level

```typescript
// ✅ CORRECT - Call hook at component top level
export function ChatHost() {
  const loc = useLocation();
  const nav = useNavigate();
  const { request } = useApi();
  const { user } = useAuth();
  const { mode, setMode, activeCase } = useChatShell();
  const { sendStreamingMessage } = useStreamingChat();  // ✅ Hook called here

  // ... rest of component
```

### Step 3: Use Hook Result in Function

```typescript
// ✅ CORRECT - Use the function returned by the hook
async function sendToThread(text: string) {
  // ...

  // No dynamic import, just use the function
  await sendStreamingMessage(threadId, msg, runId, {
    onThinking: (content) => { /* ... */ },
    onToken: (token) => { /* ... */ },
    onDone: (reply, toolEvents) => { /* ... */ },
    onError: (error) => { /* ... */ },
  });
}
```

## Files Changed

**Modified:**
- `ui/src/ui/ChatHost.tsx`:
  - Added static import of `useStreamingChat`
  - Called hook at component level (line 66)
  - Removed dynamic import from `sendToThread` function (line 244-246)

## React Rules of Hooks

### ✅ Correct Usage

```typescript
function MyComponent() {
  // ✅ Top level of component
  const { data } = useMyHook();

  function handleClick() {
    // ✅ Use the data returned by the hook
    console.log(data);
  }

  return <button onClick={handleClick}>Click</button>;
}
```

### ❌ Incorrect Usage

```typescript
function MyComponent() {
  function handleClick() {
    // ❌ Hook called inside nested function
    const { data } = useMyHook();
    console.log(data);
  }

  if (condition) {
    // ❌ Hook called conditionally
    const { data } = useMyHook();
  }

  return <button onClick={handleClick}>Click</button>;
}

async function myAsyncFunction() {
  // ❌ Hook called in async function
  const { data } = useMyHook();
}
```

## Verification

**Build:** ✅ Success
```
✓ 225 modules transformed
✓ built in 3.21s
```

**Expected Behavior After Fix:**
1. User types message and presses Enter
2. User message appears immediately
3. Within 500ms, thinking indicator appears: "_Analyzing case evidence..._"
4. Tool execution shows: "_Querying Prometheus metrics..._"
5. Tokens stream progressively into the response
6. Final complete response displayed

## Testing Checklist

- [ ] Start backend: `poetry run python main.py --serve-webhook`
- [ ] Start frontend: `cd ui && npm run dev`
- [ ] Open http://localhost:5173
- [ ] Send a message in case chat
- [ ] Verify thinking indicator appears
- [ ] Verify response streams back progressively
- [ ] Verify complete response is displayed
- [ ] Test global chat as well

## Prevention

1. **Always call hooks at component top level**
2. **Never use dynamic imports for hooks**
3. **Use ESLint rule:** `eslint-plugin-react-hooks` catches these errors
4. **Code review:** Watch for hooks called in nested functions

## Resources

- [React Rules of Hooks](https://react.dev/reference/rules/rules-of-hooks)
- [ESLint Plugin: react-hooks](https://www.npmjs.com/package/eslint-plugin-react-hooks)
- [React Hook FAQ](https://react.dev/reference/react/hooks#rules-of-hooks)
