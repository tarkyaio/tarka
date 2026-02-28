# Chat Runtime Exception Handling Fix

## Issue

The `rerun.investigation` tool (and potentially other tools) was causing the entire chat stream to crash when exceptions were raised during tool execution. This resulted in "Stream error" logs with incomplete tracebacks:

```
2026-02-18 19:35:03,462 - agent.api.webhook - ERROR - Stream error
Traceback (most recent call last):
  File "/app/agent/api/webhook.py", line 2302, in _thread_send_stream
    async for event in run_chat_stream(
  File "/app/agent/chat/runtime_streaming.py", line 471, in run_chat_stream
    res = trace_tool_call(
          ^^^^^^^^^^^^^^^^
  File "/app/agent/graphs/tracing.py", line 155, in trace_tool_call
    return fn()
           ^^^^
```

**Case ID**: `63569f2a-3943-43f6-845f-a88e10372fb2`

## Root Cause

All chat runtime modules had **no exception handling** around tool execution:

```python
res = trace_tool_call(
    tool=tool,
    args=args,
    fn=lambda: run_tool(...),
)
```

If `run_tool` raised an exception (instead of returning a `ToolResult` with an error), the exception would propagate up and crash the entire chat stream. This broke the user experience and provided no helpful error message.

## Affected Files

- `agent/chat/runtime_streaming.py` (line 471)
- `agent/chat/runtime.py` (line 316)
- `agent/chat/global_runtime.py` (line 195)
- `agent/chat/global_runtime_streaming.py` (line 344)

## Fix

Added defensive exception handling around all tool execution calls:

```python
try:
    res = trace_tool_call(
        tool=tool,
        args=args,
        fn=lambda: run_tool(...),
    )
except Exception as e:
    # Catch any unhandled exceptions from tool execution
    logger.exception(f"Tool {tool} raised unhandled exception")
    res = ToolResult(ok=False, error=f"tool_exception:{type(e).__name__}:{str(e)[:200]}")
```

This ensures:
1. **No stream crashes**: Exceptions are caught and converted to error ToolResults
2. **Full error logging**: `logger.exception()` captures the complete traceback
3. **User-friendly errors**: Error message includes exception type and details
4. **Graceful degradation**: Chat continues even if a single tool fails

## Impact

This defensive fix prevents tool failures from crashing the entire chat session. Users will now see:
- Clear error messages when tools fail
- The chat stream continues working
- Full error details in server logs for debugging

## Testing

All existing tests pass:
- `tests/test_chat_runtime_streaming.py` - 11 tests ✅
- `tests/test_aws_chat_tools.py` - 19 tests ✅

## Best Practices

This fix follows the principle of **defensive programming**:
- Never trust external dependencies (tool implementations) to handle all edge cases
- Always wrap potentially failing operations in try/except
- Convert exceptions to user-friendly error messages
- Log full exception details for debugging

## Related Fixes

This is part of a series of chat tool reliability improvements:
1. CloudTrail naive datetime fix (fixed timezone issues)
2. **This fix** (added exception handling)
3. Future: Add parameter validation at tool planning stage
