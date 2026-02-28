# Fix: rerun.investigation Tool Improvements

**Date**: 2026-02-19
**Status**: ✅ Implemented and Tested

## Problems Fixed

### Problem 1: Missing Parameter Documentation

**Issue**: The LLM was calling `rerun.investigation` with empty args `{}` because the tool description didn't mention the required `time_window` parameter.

**Symptoms**:
```
agent.chat.tools - INFO - Tool call: rerun.investigation args={} case_id=...
Error: time_window_required
```

**Root Cause**: Tool description was too vague:
```python
"rerun.investigation": "Re-run investigation pipeline with current data"
```

The LLM didn't know it needed to pass a `time_window` parameter.

**Fix**: Updated tool description to include parameter details:
```python
"rerun.investigation": "Re-run investigation with different time window (args: time_window required e.g. '30m', '1h', '2h'; reference_time optional: 'original' uses alert time (default), 'now' uses current time)"
```

---

### Problem 2: Wrong Time Reference (Critical Bug)

**Issue**: Reruns were using "now" as the reference time instead of the original alert timestamp.

**Symptoms**:
- Investigation with `time_window="1h"` for an alert that fired yesterday would investigate "now-1h to now" instead of "alert_time-1h to alert_time"
- Evidence collected was from wrong time period
- Logs/metrics didn't match when alert actually fired

**Root Cause**: The code was not preserving the original `starts_at` timestamp when reconstructing the alert:
```python
# BEFORE (broken)
alert = {
    "fingerprint": inv0.alert.fingerprint,
    "labels": inv0.alert.labels or {},
    "annotations": inv0.alert.annotations or {},
    "status": {"state": "active"},  # ← Missing starts_at!
}
```

**Fix**: Preserve original alert timestamps:
```python
# AFTER (fixed)
alert = {
    "fingerprint": inv0.alert.fingerprint,
    "labels": inv0.alert.labels or {},
    "annotations": inv0.alert.annotations or {},
    "starts_at": inv0.alert.starts_at,  # ← Preserved
    "ends_at": inv0.alert.ends_at,
    "generator_url": inv0.alert.generator_url,
    "status": {"state": inv0.alert.state or "active"},
}
```

---

### Enhancement: Support Both Historical and Current State Investigation

**Motivation**: Sometimes you want to investigate what happened when the alert fired (historical), other times you want to check current system state.

**Implementation**: Added optional `reference_time` parameter with two modes:

#### 1. Historical Mode (Default: `reference_time="original"`)
- Investigates system state at the time the alert originally fired
- Time window calculated backward from original alert timestamp
- **Use case**: Post-mortem analysis, understanding root cause

**Example**:
```
Alert fired: 2026-02-19 10:00:00Z
time_window: "2h"
Investigation window: 2026-02-19 08:00:00Z → 10:00:00Z
```

#### 2. Current State Mode (`reference_time="now"`)
- Investigates current system state using "now" as reference
- Time window calculated backward from current time
- **Use case**: Check if issue persists, verify resolution

**Example**:
```
Alert fired: 2026-02-18 10:00:00Z (yesterday)
Current time: 2026-02-19 12:00:00Z
time_window: "30m", reference_time: "now"
Investigation window: 2026-02-19 11:30:00Z → 12:00:00Z
```

---

## Implementation Details

### Files Modified

**Core Implementation**:
- ✅ `agent/chat/tools.py` - Added `reference_time` parameter handling and timestamp preservation
- ✅ `agent/chat/runtime.py` - Updated tool description with parameter details
- ✅ `agent/chat/runtime_streaming.py` - Updated loading message

**Documentation**:
- ✅ `docs/chat_tools.md` - Comprehensive documentation of both modes with examples
- ✅ `docs/fixes/rerun-tool-improvements.md` - This document

**Tests**:
- ✅ `tests/test_rerun_tool.py` - 9 comprehensive tests covering all scenarios

### Test Coverage

```bash
$ poetry run pytest tests/test_rerun_tool.py -v
============================== 9 passed in 0.11s ===============================
```

**Tests added**:
1. ✅ `test_rerun_investigation_requires_time_window` - Validates parameter requirement
2. ✅ `test_rerun_investigation_with_valid_time_window` - Basic functionality
3. ✅ `test_rerun_investigation_rejects_too_large_window` - Policy enforcement
4. ✅ `test_rerun_investigation_tool_description_includes_parameters` - Documentation quality
5. ✅ `test_rerun_investigation_disabled_by_policy` - Policy gating
6. ✅ `test_rerun_investigation_preserves_original_alert_timestamp` - Timestamp preservation
7. ✅ `test_rerun_investigation_with_reference_time_original` - Historical mode
8. ✅ `test_rerun_investigation_with_reference_time_now` - Current state mode
9. ✅ `test_rerun_investigation_invalid_reference_time` - Input validation

---

## Usage Examples

### Historical Investigation (Default)

**Scenario**: Alert fired yesterday. Want to understand what caused it.

```
User: "Rerun with 2 hour window to see earlier symptoms"
Agent: [calls rerun.investigation(time_window="2h")]
Agent: "Re-ran investigation with 2h window from when alert fired at 10:00am.
        CPU throttling started at 8:45am, 75 minutes before alert."
```

### Current State Investigation

**Scenario**: Alert fired yesterday. Want to check if issue is resolved.

```
User: "Has this issue been resolved?"
Agent: [calls rerun.investigation(time_window="30m", reference_time="now")]
Agent: "Checked current state over last 30m. CPU usage is normal (45%),
        no throttling detected. Issue appears resolved."
```

### Check for Recurring Issues

**Scenario**: Want to see if problem is still happening.

```
User: "Is the job still failing?"
Agent: [calls rerun.investigation(time_window="1h", reference_time="now")]
Agent: "Checked recent job runs in last hour. Found 2 more failures:
        - batch-etl-job-57439-0 failed 15m ago (same SQLAlchemy error)
        - batch-etl-job-57440-0 failed 5m ago (same SQLAlchemy error)
        Issue is recurring and not resolved."
```

---

## Before/After Comparison

### Before (Broken)

**User**: "Rerun investigation"
**Agent**: `rerun.investigation(args={})`
**Result**: ❌ Error: `time_window_required`

**User**: "Rerun with 2h window"
**Agent**: `rerun.investigation(time_window="2h")`
**Result**: ❌ Investigates wrong time period (now-2h to now instead of alert_time-2h to alert_time)

---

### After (Fixed)

**User**: "Rerun investigation with 2h window"
**Agent**: `rerun.investigation(time_window="2h")`
**Result**: ✅ Investigates correct historical time period (alert_time-2h to alert_time)

**User**: "Show me current state with 30m window"
**Agent**: `rerun.investigation(time_window="30m", reference_time="now")`
**Result**: ✅ Investigates current state (now-30m to now)

---

## API Reference

### Tool: `rerun.investigation`

**Description**: Re-run investigation with different time window and reference time

**Arguments**:
| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `time_window` | string | Yes | - | Time window (e.g., "30m", "1h", "2h") |
| `reference_time` | string | No | "original" | Time reference: "original" or "now" |

**Returns**:
```python
ToolResult(
    ok=True,
    result={"status": "ok"},
    updated_analysis={...}  # Updated analysis from rerun
)
```

**Errors**:
- `time_window_required` - Missing required time_window parameter
- `reference_time_must_be_original_or_now` - Invalid reference_time value
- `time_window_too_large` - Exceeds policy max (default: 2h)
- `tool_not_allowed` - Policy gate `CHAT_ALLOW_REPORT_RERUN=false`

---

## Policy Configuration

**Environment Variable**: `CHAT_ALLOW_REPORT_RERUN`
**Default**: `true`
**Max Time Window**: `CHAT_MAX_TIME_WINDOW_SECONDS` (default: 7200 = 2 hours)

To disable reruns:
```bash
export CHAT_ALLOW_REPORT_RERUN=false
```

To increase max time window:
```bash
export CHAT_MAX_TIME_WINDOW_SECONDS=14400  # 4 hours
```

---

## Impact

### Benefits

1. **LLM can now use the tool correctly** - Clear parameter documentation prevents `args={}` errors
2. **Accurate historical investigation** - Reruns investigate the correct time period
3. **Flexible investigation modes** - Support both historical analysis and current state checking
4. **Better post-mortem analysis** - Can expand time window to find earlier symptoms
5. **Incident resolution verification** - Can check if issue persists after mitigation

### Risk Assessment

- **Risk**: Low
- **Breaking changes**: None (new parameter is optional with sensible default)
- **Backward compatibility**: Fully compatible (default behavior is historical mode)
- **Test coverage**: Comprehensive (9 tests covering all scenarios)

---

## Related Issues

- **Original issue**: LLM calling `rerun.investigation` with empty args due to missing parameter docs
- **Critical bug**: Reruns using "now" instead of original alert time
- **Enhancement request**: Support both historical and current state investigation modes

All issues resolved in this implementation.
