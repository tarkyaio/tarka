# CloudTrail Tool Fix: Naive Datetime Issue

## Issue

The `aws.cloudtrail_events` chat tool was failing with empty args `{}` and displaying "Error: network error. Please try again." in the UI. The logs showed no error details, making debugging difficult.

**Case ID**: `cfcd31c3-76d6-4356-b05b-781f9b710630`

## Root Cause

Two problems in `agent/chat/tools.py`:

### 1. Naive datetime (no timezone)

**Lines 598, 610, 617, 627** used `datetime.utcnow()` which returns a **naive datetime** (no timezone info):

```python
tw = parse_time_window(start_time_str, datetime.utcnow())  # ❌ Naive
start_time = datetime.utcnow() - timedelta(hours=1)        # ❌ Naive
```

However, the AWS CloudTrail provider (`aws_provider.py` line 538-539) expects **timezone-aware datetimes**:

```python
Args:
    start_time: Query start (UTC, timezone-aware)
    end_time: Query end (UTC, timezone-aware)
```

When boto3's CloudTrail `lookup_events` API received naive datetimes, it raised an exception (likely `TypeError` or `botocore.exceptions.ParamValidationError`).

### 2. Poor error handling

**Line 669** only returned the exception type name, hiding the actual error message:

```python
except Exception as e:
    return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}")  # ❌ No message
```

This made debugging impossible - neither logs nor UI showed the real error.

## Fix

### 1. Use timezone-aware datetimes

Replaced all `datetime.utcnow()` calls with `datetime.now(timezone.utc)`:

```python
tw = parse_time_window(start_time_str, datetime.now(timezone.utc))  # ✅ Timezone-aware
start_time = datetime.now(timezone.utc) - timedelta(hours=1)        # ✅ Timezone-aware
```

### 2. Improve error handling

Added error message to the exception handler with logging:

```python
except Exception as e:
    logger.warning(f"CloudTrail events query failed: region={region} error={str(e)[:400]}")
    return ToolResult(ok=False, error=f"aws_error:{type(e).__name__}:{str(e)[:200]}")  # ✅ Includes message
```

Now errors are:
- Logged to the server for debugging
- Returned to the UI with context

## Testing

Added comprehensive tests in `tests/test_aws_chat_tools.py`:

1. **`test_aws_cloudtrail_events_with_empty_args`**: Verifies empty args `{}` works with defaults
2. **`test_aws_cloudtrail_events_requires_policy`**: Verifies policy enforcement
3. **`test_aws_cloudtrail_events_respects_region_allowlist`**: Verifies region filtering

All tests pass ✅

## Impact

This fix resolves the issue where `aws.cloudtrail_events` failed silently with empty args. The tool now:

- Accepts empty args `{}` and auto-discovers defaults from investigation metadata
- Uses timezone-aware datetimes compatible with boto3
- Provides clear error messages when failures occur

## Related Files

- **Fixed**: `agent/chat/tools.py` (lines 590-670)
- **Tests**: `tests/test_aws_chat_tools.py`
- **Provider**: `agent/providers/aws_provider.py` (CloudTrail implementation)
