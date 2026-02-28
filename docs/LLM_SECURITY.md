# LLM Security & Log Redaction

## Overview

When `LLM_INCLUDE_LOGS=true`, log messages are sent to the LLM for deeper analysis. To prevent secret leakage, automatic redaction is applied.

## Redaction Strategy

### Tier 1: Always Redacted 🔴

These patterns are **always** redacted before sending logs to the LLM:

| Pattern | Example | Redacted Output |
|---------|---------|-----------------|
| API Keys | `api_key=sk-ant-1234567890` | `api_key=[REDACTED]` |
| Passwords | `password="secret123"` | `password="[REDACTED]"` |
| AWS Access Keys | `AKIAIOSFODNN7EXAMPLE` | `[REDACTED]` |
| AWS Session Tokens | `ASIAIOSFODNN7EXAMPLE` | `[REDACTED]` |
| Bearer Tokens | `Authorization: Bearer eyJ...` | `Authorization: Bearer [REDACTED]` |
| JWT Tokens | `eyJhbGc...` | `[REDACTED]` |
| Database Passwords | `postgres://user:pass@host` | `postgres://user:[REDACTED]@host` |
| Private Keys | `-----BEGIN RSA PRIVATE KEY-----` | `[REDACTED]` |
| GitHub Tokens | `ghp_1234567890abcdef` | `[REDACTED]` |
| Anthropic Keys | `sk-ant-1234567890` | `[REDACTED]` |

### Tier 2: Optionally Redacted 🟡

Controlled by `LLM_REDACT_INFRASTRUCTURE=true` (default: **false**)

| Pattern | Example | Why Optional? |
|---------|---------|---------------|
| Email addresses | `user@company.com` | Needed for user-related errors |
| Private IPs | `10.0.1.5`, `192.168.1.1` | Needed for network diagnostics |
| S3 bucket names | `s3://my-bucket` | Needed for S3 access denied errors |

**Default behavior:** Keep these for diagnostic value.

### Tier 3: Never Redacted ✅

These are always preserved because they're essential for diagnosis:

- ✅ Kubernetes resource names (`batch-etl-job-57819`)
- ✅ Namespaces (`production`, `prod`)
- ✅ HTTP status codes (`403`, `500`)
- ✅ Error types (`Forbidden`, `AccessDenied`)
- ✅ Service names (`S3`, `RDS`, `DynamoDB`)
- ✅ Operations (`HeadBucket`, `GetObject`)
- ✅ Stack traces and exception types

## Configuration

### Option 1: Include Logs Without Infrastructure Redaction (Recommended)

```bash
# .env.fixture
LLM_INCLUDE_LOGS=true
LLM_REDACT_INFRASTRUCTURE=false  # Keep bucket names, IPs for diagnostics
```

**Security:** High (secrets redacted)
**Diagnostics:** Excellent (infrastructure visible)
**Best for:** Production investigations where bucket names/IPs are needed

### Option 2: Maximum Security (Redact Everything)

```bash
# .env.fixture
LLM_INCLUDE_LOGS=true
LLM_REDACT_INFRASTRUCTURE=true  # Also redact IPs, emails, buckets
```

**Security:** Maximum
**Diagnostics:** Reduced (may lose context)
**Best for:** Highly sensitive environments, compliance requirements

### Option 3: Metadata Only (Default)

```bash
# .env.fixture
LLM_INCLUDE_LOGS=false  # Only send log count, no content
```

**Security:** Maximum (no log content sent)
**Diagnostics:** Limited (LLM can't see error details)
**Best for:** When logs contain highly sensitive business data

## Example: S3 Access Denied

### Original Log
```
ERROR:root:Failed to get bucket region for my-company-data-prod:
An error occurred (403) when calling the HeadBucket operation: Forbidden
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
```

### With `LLM_INCLUDE_LOGS=true` (default)
```
ERROR:root:Failed to get bucket region for my-company-data-prod:
An error occurred (403) when calling the HeadBucket operation: Forbidden
AWS_ACCESS_KEY_ID=[REDACTED]
```

✅ Bucket name preserved (needed for diagnosis)
✅ Error details preserved
✅ Access key redacted

### With `LLM_REDACT_INFRASTRUCTURE=true`
```
ERROR:root:Failed to get bucket region for [REDACTED]:
An error occurred (403) when calling the HeadBucket operation: Forbidden
AWS_ACCESS_KEY_ID=[REDACTED]
```

✅ Access key redacted
⚠️ Bucket name redacted (reduces diagnostic value)

## Testing Redaction

```python
from agent.authz.policy import redact_text

# Test secret redaction
assert redact_text("password=secret123") == "password=[REDACTED]"
assert redact_text("AKIAIOSFODNN7EXAMPLE") == "[REDACTED]"

# Test infrastructure preservation (default)
assert "my-bucket" in redact_text("s3://my-bucket/key")
assert "10.0.1.5" in redact_text("connecting to 10.0.1.5")

# Test infrastructure redaction (when enabled)
assert "[REDACTED]" in redact_text("s3://my-bucket", redact_infrastructure=True)
```

## Recommendations

### For S3 Access Denied Scenarios

**Recommended:** `LLM_INCLUDE_LOGS=true`, `LLM_REDACT_INFRASTRUCTURE=false`

**Rationale:**
- Bucket name is **not** a secret (it's in IAM policies, CloudFormation, etc.)
- Bucket name is **essential** for diagnosis (which bucket is failing?)
- LLM can provide specific remediation: "Grant `s3:GetObject` on `arn:aws:s3:::my-bucket/*`"

### For Database Errors

**Recommended:** `LLM_INCLUDE_LOGS=true`, `LLM_REDACT_INFRASTRUCTURE=false`

**Rationale:**
- Connection strings redacted smartly: `postgres://user:[REDACTED]@db.example.com`
- Database host preserved for diagnostics
- Passwords always redacted

### For Compliance-Heavy Environments

**Recommended:** `LLM_INCLUDE_LOGS=false` OR `LLM_REDACT_INFRASTRUCTURE=true`

**Rationale:**
- Minimize data sent to LLM
- Rely on diagnostic pattern matching (deterministic)
- Use RCA graph tools instead of log content

## Limitations

⚠️ **Redaction is best-effort, not perfect:**
- Novel secret formats may not be caught
- Secrets in unusual encodings (hex, base32) may leak
- Multi-line secrets might bypass regex
- Context-dependent secrets (sequential numbers that happen to be keys) are hard to detect

**Mitigation:**
- Never log secrets (fix at source)
- Use structured logging with secret-safe fields
- Regularly audit LLM prompts in LangSmith traces
- Consider PII detection tools for additional layer

## FAQ

**Q: Does this apply to production investigations?**
A: Yes, the same redaction runs in webhook mode (production).

**Q: Are redacted logs stored in investigation.json?**
A: No, redaction only applies to LLM prompts. Raw logs in `investigation.json` are unmodified.

**Q: Can I customize redaction patterns?**
A: Yes, edit `agent/authz/policy.py` `_ALWAYS_REDACT_PATTERNS` and `_INFRASTRUCTURE_PATTERNS`.

**Q: What about PII (phone numbers, SSNs)?**
A: Not currently redacted. Add patterns to `_INFRASTRUCTURE_PATTERNS` if needed.

**Q: Does this slow down investigations?**
A: No, regex redaction adds <10ms overhead.
