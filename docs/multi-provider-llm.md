# Multi-Provider LLM Support

The Tarka now supports multiple LLM providers through a unified interface leveraging LangChain's abstractions.

## Supported Providers

### 1. Vertex AI (Google Cloud)
- **Provider**: `vertexai` (default)
- **Models**: Gemini 2.5 Flash, Gemini 2.0 Pro, etc.
- **SDK**: `langchain-google-vertexai`

**Required Configuration:**
```bash
LLM_ENABLED=true
LLM_PROVIDER=vertexai
GOOGLE_CLOUD_PROJECT=my-gcp-project
GOOGLE_CLOUD_LOCATION=us-central1
LLM_MODEL=gemini-2.5-flash
LLM_TEMPERATURE=0.2
LLM_MAX_OUTPUT_TOKENS=4096
```

**Authentication:**
- Application Default Credentials (ADC)
- Workload Identity (for in-cluster deployments)
- Service account JSON key (via `GOOGLE_APPLICATION_CREDENTIALS`)

### 2. Anthropic Claude
- **Provider**: `anthropic`
- **Models**: Claude 3.5 Sonnet, Claude 3 Opus, etc.
- **SDK**: `langchain-anthropic`
- **Extended Thinking**: Always enabled with 1024 token budget

**Required Configuration:**
```bash
LLM_ENABLED=true
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-...
LLM_MODEL=claude-3-5-sonnet-20241022
LLM_TEMPERATURE=0.2
LLM_MAX_OUTPUT_TOKENS=4096
```

**Authentication:**
- API key from Anthropic Console

## Installation

### Install All Providers
```bash
poetry install -E all-providers
```

### Install Specific Provider
```bash
# Vertex AI only
poetry install -E vertex

# Anthropic only
poetry install -E anthropic
```

## Architecture

### Factory Pattern
The implementation uses a lightweight factory function (`_get_llm_instance()`) that returns the appropriate LangChain `BaseChatModel` implementation based on `LLM_PROVIDER`:

```
Application → generate_json() → _get_llm_instance() → ChatVertexAI | ChatAnthropic
```

### Backward Compatibility
- **Zero changes** to existing application code
- `generate_json(prompt, schema=Schema)` API unchanged
- Existing Vertex AI configurations work without modification
- Error codes standardized across providers

### Extended Thinking (Anthropic)
Claude's extended thinking feature is **always enabled** with a 1024 token budget. This provides:
- Deeper reasoning on complex incidents
- Better RCA synthesis
- More accurate hypothesis confidence scoring
- Improved multi-step debugging chains

Thinking tokens are tracked separately in response metadata.

## Provider Switching

### Development Environment
Switch providers by changing environment variables:

```bash
# Switch to Anthropic
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
unset GOOGLE_CLOUD_PROJECT
unset GOOGLE_CLOUD_LOCATION

# Switch back to Vertex AI
export LLM_PROVIDER=vertexai
export GOOGLE_CLOUD_PROJECT=my-project
export GOOGLE_CLOUD_LOCATION=us-central1
unset ANTHROPIC_API_KEY
```

### Production Deployment
Configure via Kubernetes ConfigMap/Secret:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tarka-config
data:
  LLM_ENABLED: "true"
  LLM_PROVIDER: "anthropic"
  LLM_MODEL: "claude-3-5-sonnet-20241022"
  LLM_TEMPERATURE: "0.2"
  LLM_MAX_OUTPUT_TOKENS: "4096"
---
apiVersion: v1
kind: Secret
metadata:
  name: tarka-secrets
type: Opaque
stringData:
  ANTHROPIC_API_KEY: "sk-ant-api03-..."
```

## Error Handling

### Standardized Error Codes
All providers return consistent error codes:

| Error Code | Description | Provider-Specific Hints |
|------------|-------------|-------------------------|
| `missing_api_key` | API key not configured | Set `ANTHROPIC_API_KEY` |
| `missing_gcp_project` | GCP project not set | Set `GOOGLE_CLOUD_PROJECT` |
| `missing_gcp_location` | GCP location not set | Set `GOOGLE_CLOUD_LOCATION` |
| `missing_adc_credentials` | ADC not configured | Configure Workload Identity |
| `unauthenticated` | Credentials rejected | Check API key or ADC |
| `permission_denied` | Insufficient permissions | Check project/model access |
| `sdk_import_failed:SDK` | Provider SDK not installed | `poetry install -E PROVIDER` |
| `model_not_found:MODEL` | Model not available | Check `LLM_MODEL` setting |
| `rate_limited` | Rate limit exceeded | Wait or increase quota |
| `max_tokens_truncated` | Context length exceeded | Reduce input or increase limit |
| `provider_not_configured` | Unknown provider | Set to `vertexai` or `anthropic` |

### Graceful Degradation
When LLM calls fail:
1. Agent continues with deterministic analysis
2. Error code and hint shown to user
3. Base triage report generated without LLM enrichment
4. Investigation remains fully functional

## Testing

### Run All Provider Tests
```bash
poetry run pytest tests/test_llm_client*.py -v
```

### Run Specific Provider Tests
```bash
# Vertex AI tests
poetry run pytest tests/test_llm_client_vertex.py -v

# Anthropic tests
poetry run pytest tests/test_llm_client_anthropic.py -v

# Factory/selection tests
poetry run pytest tests/test_llm_client_factory.py -v
```

### Mock Mode
Test without external API calls:
```bash
export LLM_MOCK=1
poetry run python main.py --alert 0 --llm
```

## Verification

### Vertex AI
```bash
export LLM_ENABLED=true
export LLM_PROVIDER=vertexai
export GOOGLE_CLOUD_PROJECT=my-project
export GOOGLE_CLOUD_LOCATION=us-central1
export LLM_MODEL=gemini-2.5-flash

poetry run python main.py --alert 0 --llm
# Should generate enriched report using Gemini
```

### Anthropic Claude
```bash
export LLM_ENABLED=true
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_MODEL=claude-3-5-sonnet-20241022

poetry run python main.py --alert 0 --llm
# Should generate enriched report using Claude with extended thinking
```

### Chat Interface
```bash
# Start investigation
poetry run python main.py --alert 0

# In chat, verify provider-specific features:
# - Vertex: Fast responses, good JSON adherence
# - Anthropic: Extended thinking visible in logs, deeper reasoning
```

## Provider Comparison

| Feature | Vertex AI (Gemini) | Anthropic (Claude) |
|---------|-------------------|-------------------|
| JSON Mode | ✅ with_structured_output | ✅ with_structured_output |
| Streaming | ✅ LangGraph | ✅ LangGraph |
| Token Tracking | ✅ prompt + completion | ✅ prompt + completion + thinking |
| Extended Thinking | ❌ | ✅ Always on (1024 tokens) |
| Rate Limits | High (project quota) | Medium (tier-based) |
| Latency | Low (GCP regions) | Medium (US/EU) |
| Cost | $0.075/$0.30 per 1M tokens | $3.00/$15.00 per 1M tokens |
| Authentication | ADC/Workload Identity | API Key |

## Future Extensions

### Adding New Providers
To add a new provider (e.g., OpenAI, Bedrock):

1. Install LangChain SDK:
   ```toml
   langchain-openai = {version = "^0.2.0", optional = true}
   ```

2. Add to `_get_llm_instance()` in `agent/llm/client.py`:
   ```python
   elif provider == "openai":
       api_key = os.getenv("OPENAI_API_KEY", "").strip()
       if not api_key:
           return None, "missing_api_key"

       from langchain_openai import ChatOpenAI

       llm = ChatOpenAI(
           model=cfg.model,
           temperature=cfg.temperature,
           max_tokens=cfg.max_output_tokens,
           openai_api_key=api_key,
       )
       return llm, None
   ```

3. Add error patterns to `_classify_error()`

4. Add tests in `tests/test_llm_client_openai.py`

**Estimated effort: ~30 lines of code per provider**

## Troubleshooting

### "sdk_import_failed" Error
```bash
# Install provider SDK
poetry install -E anthropic  # or -E vertex
```

### "missing_api_key" Error
```bash
# Set API key
export ANTHROPIC_API_KEY=sk-ant-...
```

### "unauthenticated" Error (Vertex AI)
```bash
# Configure ADC
gcloud auth application-default login
# or
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

### "permission_denied" Error
```bash
# Vertex AI: Enable APIs
gcloud services enable aiplatform.googleapis.com

# Anthropic: Check API key permissions
```

### Provider Not Switching
```bash
# Clear Python module cache
rm -rf agent/__pycache__ agent/*/__pycache__

# Restart application
poetry run python main.py --alert 0 --llm
```

## Implementation Details

The implementation leverages LangChain's `BaseChatModel` abstraction:
- **Factory pattern**: `_get_llm_instance()` selects provider based on `LLM_PROVIDER`
- **Zero breaking changes**: Existing `generate_json()` API unchanged
- **Lazy loading**: Only imports required provider SDK
- **Backward compatible**: All existing Vertex AI configs work unchanged

## References

- [LangChain ChatAnthropic Documentation](https://python.langchain.com/docs/integrations/chat/anthropic)
- [LangChain ChatVertexAI Documentation](https://python.langchain.com/docs/integrations/chat/google_vertex_ai_palm)
- [Anthropic Extended Thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking)
- [CLAUDE.md Development Commands](../CLAUDE.md)
