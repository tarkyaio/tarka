# Chat Tool Description Improvements

**Date**: 2026-02-19
**Status**: ✅ Implemented

## Problem

Multiple chat tools had insufficient parameter documentation in their descriptions, causing the LLM to:
- Call tools with empty args `{}`
- Not know which parameters are required vs optional
- Miss opportunities to use helpful optional parameters

## Root Cause

Tool descriptions in `TOOL_DESCRIPTIONS` dict were too vague, only describing WHAT the tool does, not HOW to use it.

**Example (before)**:
```python
"rerun.investigation": "Re-run investigation pipeline with current data"
# ❌ No parameter info, LLM doesn't know what args to pass
```

## Solution

Updated tool descriptions to include parameter information following this pattern:
```
<what-it-does> (args: <required-params>; <optional-params>)
```

## Tools Fixed

### 1. ✅ `rerun.investigation`

**Before**:
```python
"rerun.investigation": "Re-run investigation pipeline with current data"
```

**After**:
```python
"rerun.investigation": "Re-run investigation with different time window (args: time_window required e.g. '30m', '1h', '2h'; reference_time optional: 'original' uses alert time (default), 'now' uses current time)"
```

**Impact**:
- LLM now knows `time_window` is required
- Understands the format (examples: '30m', '1h', '2h')
- Knows about `reference_time` option for historical vs current investigation

---

### 2. ✅ `aws.cloudtrail_events`

**Before**:
```python
"aws.cloudtrail_events": "Query AWS CloudTrail infrastructure change events"
```

**After**:
```python
"aws.cloudtrail_events": "Query AWS CloudTrail for infrastructure changes (all args optional: start_time, end_time, resource_ids, region auto-discovered from investigation)"
```

**Impact**:
- LLM knows all parameters are optional
- Understands tool has smart defaults (auto-discovery)
- Can call with empty args `{}` and tool will auto-discover context

**Note**: If CloudTrail calls fail with "network error", check:
- AWS credentials are configured (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or IAM role)
- `AWS_EVIDENCE_ENABLED=true` in environment
- Required IAM permission: `cloudtrail:LookupEvents`
- Alert has AWS resources associated (for Kubernetes-only alerts, CloudTrail may not be relevant)

---

## Pattern for Future Tools

When adding new tools or updating descriptions, follow this format:

```python
"tool.name": "<brief-description> (args: <required> e.g. 'examples'; <optional> defaults to X)"
```

**Template**:
```python
"tool.name": "<action> <target> (args: param1 required e.g. 'value1', 'value2'; param2 optional default='auto', param3 optional)"
```

**Examples**:

Good descriptions:
```python
"logs.tail": "Fetch recent logs (args: pod, namespace, container optional, start_time optional, limit optional default=200)"
"promql.instant": "Query Prometheus metrics (args: query required e.g. 'rate(http_requests_total[5m])', at optional)"
"k8s.pod_context": "Get pod status and events (args: pod optional auto-discovered, namespace optional auto-discovered)"
```

Bad descriptions:
```python
"logs.tail": "Fetch logs"  # ❌ No parameter info
"promql.instant": "Query Prometheus"  # ❌ No examples
"k8s.pod_context": "Get pod information (requires pod name)"  # ❌ Doesn't mention it's optional/auto-discovered
```

---

## Verification

### Test Tool Descriptions

Added test to verify tool descriptions include parameter information:

```python
def test_rerun_investigation_tool_description_includes_parameters():
    """Test that the tool description mentions required parameters."""
    from agent.chat.runtime import TOOL_DESCRIPTIONS

    description = TOOL_DESCRIPTIONS.get("rerun.investigation")
    assert description is not None
    assert "time_window" in description.lower()
    assert "required" in description.lower() or "args:" in description.lower()
    assert any(example in description for example in ["30m", "1h", "2h"])
```

### Manual Testing

Before fix:
```
User: "Rerun investigation"
LLM: [calls rerun.investigation(args={})]
Result: ❌ Error: time_window_required
```

After fix:
```
User: "Rerun investigation with 2h window"
LLM: [calls rerun.investigation(args={"time_window": "2h"})]
Result: ✅ Success
```

---

## Recommendations

### For Existing Tools

Review all tool descriptions in `TOOL_DESCRIPTIONS` and update any that are missing parameter information:

```bash
# Find tools with short descriptions (likely missing params)
grep -E '^\s+"[^"]+": "[^"]+"' agent/chat/runtime.py | awk -F'"' '{print $2, $4}' | while read tool desc; do
  if [ ${#desc} -lt 50 ]; then
    echo "Short description for $tool: $desc"
  fi
done
```

### For New Tools

When adding a new tool:

1. **Implementation** (`agent/chat/tools.py`):
   - Extract and validate required parameters
   - Provide sensible defaults for optional parameters
   - Auto-discover from investigation context when possible
   - Return clear error messages: `param_name_required`, `invalid_param_value`, etc.

2. **Description** (`agent/chat/runtime.py`):
   - List required parameters with examples
   - List optional parameters with defaults
   - Mention auto-discovery if applicable
   - Keep under 150 characters if possible

3. **Documentation** (`docs/chat_tools.md`):
   - Full parameter reference with types
   - Usage examples for common scenarios
   - Error cases and troubleshooting

4. **Tests** (`tests/test_*_tool.py`):
   - Test with required parameters only
   - Test with optional parameters
   - Test with empty args if all params are optional
   - Test with invalid parameters
   - Test parameter validation

---

## Impact

### Before
- LLMs called tools with incorrect or missing parameters
- Users saw cryptic errors: "time_window_required", "args={}"
- Tools were underutilized because LLM didn't know how to call them

### After
- LLMs call tools with correct parameters
- Clear parameter documentation in descriptions
- Better tool utilization in chat conversations
- Users get helpful results instead of errors

---

## Related Issues

- ✅ `rerun.investigation` called with empty args → Fixed with parameter documentation
- ✅ `rerun.investigation` using wrong time reference → Fixed with timestamp preservation
- ✅ `aws.cloudtrail_events` called with empty args → Fixed with "all optional" clarification

---

## Files Modified

- ✅ `agent/chat/runtime.py` - Updated `TOOL_DESCRIPTIONS` dict
- ✅ `agent/chat/runtime_streaming.py` - Updated loading messages
- ✅ `docs/chat_tools.md` - Comprehensive documentation
- ✅ `tests/test_rerun_tool.py` - Test for tool description quality
