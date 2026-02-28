# Chat Streaming Runtime - Missing Tool Descriptions

## Issue

The LLM was calling `rerun.investigation` with empty args `{}`, not understanding that `time_window` is a required parameter. This caused the tool to fail with:

```
Tool call: rerun.investigation args={} case_id=63569f2a-3943-43f6-845f-a88e10372fb2
```

## Root Cause

The **streaming runtime** (`runtime_streaming.py`) was providing the LLM with only tool **names**, not tool **descriptions**:

```python
tools = _allowed_tools(policy, action_policy)
...
f"{json.dumps(tools)}\n\n"
```

This gave the LLM:
```json
["promql.instant", "k8s.pod_context", "rerun.investigation", ...]
```

The LLM had **no information about**:
- What each tool does
- What arguments are required
- What arguments are optional
- Default values for arguments

Meanwhile, the **non-streaming runtime** (`runtime.py`) correctly provided descriptions:

```python
tool_list = "\n".join([f"- {t}: {TOOL_DESCRIPTIONS.get(t, 'No description')}" for t in tools])
...
f"{tool_list}\n\n"
```

This gave the LLM:
```
- rerun.investigation: Re-run investigation with different time window (args: time_window required e.g. '30m', '1h', '2h'; reference_time optional: 'original' uses alert time (default), 'now' uses current time)
```

## Fix

Updated `runtime_streaming.py` to:

1. **Import `TOOL_DESCRIPTIONS`** from `runtime.py`:
   ```python
   from agent.chat.runtime import TOOL_DESCRIPTIONS
   ```

2. **Build tool list with descriptions** (line 169-170):
   ```python
   tools = _allowed_tools(policy, action_policy)
   tool_list = "\n".join([f"- {t}: {TOOL_DESCRIPTIONS.get(t, 'No description')}" for t in tools])
   ```

3. **Use descriptions in prompt** (line 188):
   ```python
   f"{tool_list}\n\n"
   ```

Now the LLM receives full tool descriptions including:
- Purpose of each tool
- Required vs optional arguments
- Example values
- Default behaviors

## Tool Descriptions Include

For `rerun.investigation`:
```
Re-run investigation with different time window (args: time_window required e.g. '30m', '1h', '2h'; reference_time optional: 'original' uses alert time (default), 'now' uses current time)
```

This clearly tells the LLM:
- ✅ `time_window` is **required**
- ✅ Examples: '30m', '1h', '2h'
- ✅ `reference_time` is **optional**
- ✅ Default is 'original' (uses alert time)
- ✅ Alternative is 'now' (uses current time)

## Impact

With this fix:
- **LLM knows what arguments to provide** for each tool
- **No more empty args calls** - LLM will include required parameters
- **Consistent behavior** between streaming and non-streaming runtimes
- **Better user experience** - tools are called correctly the first time

## Testing

All streaming runtime tests pass:
- `tests/test_chat_runtime_streaming.py` - 11 tests ✅

## Related Fixes

This is part of a series of chat tool reliability improvements:
1. CloudTrail naive datetime fix (fixed timezone issues)
2. Chat runtime exception handling (prevents stream crashes)
3. **This fix** (LLM now understands tool parameters)

## Future Work

Consider:
- Structured tool schemas (JSON Schema) instead of string descriptions
- Parameter validation at planning stage (before execution)
- Tool usage examples in prompt for complex tools
