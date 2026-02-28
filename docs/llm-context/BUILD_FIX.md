# Build Fix: TypeScript Type Conflict

## Issue

```
error TS2345: Argument of type 'ChatToolEvent[]' is not assignable to parameter of type 'SetStateAction<ChatToolEvent[]>'.
  Type 'import("/app/src/lib/useStreamingChat").ChatToolEvent[]' is not assignable to type 'import("/app/src/lib/types").ChatToolEvent[]'.
    Type 'import("/app/src/lib/useStreamingChat").ChatToolEvent' is not assignable to type 'import("/app/src/lib/types").ChatToolEvent'.
      Types of property 'outcome' are incompatible.
        Type 'string | undefined' is not assignable to type '"error" | "ok" | "empty" | "unavailable" | "skipped_duplicate" | null | undefined'.
          Type 'string' is not assignable to type '"error" | "ok" | "empty" | "unavailable" | "skipped_duplicate" | null | undefined'.
```

## Root Cause

The `useStreamingChat.ts` file defined its own `ChatToolEvent` interface, which conflicted with the existing `ChatToolEvent` type in `types.ts`:

**New definition (incorrect):**
```typescript
export interface ChatToolEvent {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  error?: string;
  outcome?: string;  // ❌ Too broad - any string
  summary?: string;
}
```

**Existing definition (correct):**
```typescript
export type ChatToolEvent = {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  result?: unknown;
  error?: string | null;
  outcome?: "ok" | "empty" | "unavailable" | "error" | "skipped_duplicate" | null;  // ✅ Specific literals
  summary?: string | null;
  key?: string | null;
};
```

## Solution

**Before:**
```typescript
// ui/src/lib/useStreamingChat.ts
export interface ChatToolEvent {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  error?: string;
  outcome?: string;
  summary?: string;
}

export interface StreamingChatCallbacks {
  onDone?: (reply: string, toolEvents: ChatToolEvent[]) => void;
  // ...
}
```

**After:**
```typescript
// ui/src/lib/useStreamingChat.ts
import type { ChatToolEvent } from "./types";

export interface StreamingChatCallbacks {
  onDone?: (reply: string, toolEvents: ChatToolEvent[]) => void;
  // ...
}
```

## Files Changed

- `ui/src/lib/useStreamingChat.ts` - Removed duplicate type definition, added import

## Build Verification

```bash
cd ui && npm run build
```

**Result:**
```
✓ 226 modules transformed.
✓ built in 3.50s
```

## Lessons Learned

1. **Check for existing types**: Always search for existing type definitions before creating new ones
2. **Use stricter types**: String literals (`"ok" | "error"`) are better than generic `string`
3. **Import shared types**: Share types across modules via imports to maintain consistency

## Prevention

To prevent similar issues:

1. **Search before defining**: `grep -r "export type TypeName" ui/src/lib/`
2. **Use linting**: Configure ESLint to warn about duplicate type definitions
3. **Review imports**: Check if types can be imported from existing files
4. **TypeScript strict mode**: Use strict mode to catch type mismatches early
