# Eval Fixtures Guide

This directory contains evaluation fixtures for testing Tarka's investigation quality across different failure scenarios.

## Directory Structure

Fixtures support two organizational patterns:

### Pattern 1: Flat Structure (Legacy)
```
fixtures/
  my-scenario/
    investigation.json
    scenario.yaml
    README.md
```

### Pattern 2: Nested Structure (Comparison Mode)
```
fixtures/
  my-scenario/
    llm/
      investigation.json
      scenario.yaml
      README.md
    no-llm/
      investigation.json
      scenario.yaml
      README.md
    COMPARISON.md
    job-manifest.yaml  # Optional: K8s manifest to reproduce
```

The nested structure allows comparing LLM-enriched vs deterministic-only investigations for the same failure scenario.

## Fixture Components

### investigation.json
Complete investigation output in JSON format. Contains:
- Alert metadata
- Evidence (K8s context, metrics, logs)
- Analysis (hypotheses, decision, RCA)
- Diagnostics results

### scenario.yaml
Test configuration and expected outcomes:
```yaml
name: Scenario Name
family: alert_family
failure_type: specific_failure
description: Brief description

expected_outcomes:
  root_cause:
    patterns: ["regex patterns to match"]
    match_type: "regex" | "substring" | "exact"

  failure_mode:
    exact: "failure_category"

  proposed_fix:
    any_of:
      - patterns: ["expected fix elements"]
        match_type: "substring"

  hypotheses:
    any_of: ["expected", "hypothesis", "keywords"]

  next_steps:
    command_types: ["kubectl", "promql"]
    must_include: ["describe", "logs"]

scoring:
  root_cause_weight: 0.4
  fix_accuracy_weight: 0.3
  hypothesis_quality_weight: 0.2
  next_steps_weight: 0.1
  pass_threshold: 70

test_config:
  time_window: "1h"
  enable_llm: false
  enable_diagnostics: true
```

### README.md
Human-readable documentation:
- What happened (failure timeline)
- Expected RCA quality
- Scoring thresholds
- Special considerations

### COMPARISON.md (Nested Structure Only)
Auto-generated report comparing LLM vs No-LLM modes:
- Evidence quality metrics
- RCA quality comparison
- Remediation comparison
- Cost-benefit analysis

## Creating Fixtures

### Single Mode Capture

Capture a fixture from a live alert:

```bash
# Port-forward to Alertmanager
kubectl port-forward -n observability svc/prometheus-kube-prometheus-alertmanager 9093:9093

# Set environment
export ALERTMANAGER_URL=http://localhost:9093

# Capture fixture
poetry run python scripts/capture-fixture.py --filter KubeJobFailed
```

Follow the interactive prompts to select an alert and provide scenario details.

### Comparison Mode Capture (Recommended)

Capture both LLM and deterministic modes for side-by-side comparison:

```bash
# Port-forward to Alertmanager (if needed)
export ALERTMANAGER_URL=http://localhost:9093

# Configure LLM credentials (REQUIRED for comparison mode)
# 1. Install LLM provider SDK (REQUIRED - langchain-anthropic is optional dependency)
poetry install --extras anthropic  # For Anthropic Claude
# OR
poetry install --extras vertex     # For Google Vertex AI
# OR
poetry install --extras all-providers  # For both

# 2. Copy the template
cp .env.fixture.example .env.fixture

# 3. Edit .env.fixture with your LLM configuration
#    For Anthropic: Set LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY
#    For Vertex AI: Set LLM_PROVIDER=vertexai, GOOGLE_CLOUD_PROJECT, etc.
#    See .env.fixture.example for full options

# 4. Load the configuration
set -a && source .env.fixture && set +a

# 5. Capture both modes
poetry run python scripts/capture-fixture.py --filter KubeJobFailed --compare-modes
```

**SECURITY NOTE:** Never commit `.env.fixture` - it contains API keys. The file is already in `.gitignore`.

This will:
1. Validate LLM configuration (fails fast if missing required vars)
2. Run investigation with `LLM_ENABLED=true` → save to `llm/`
3. Run investigation with `LLM_ENABLED=false` → save to `no-llm/`
4. Generate `COMPARISON.md` with delta analysis
5. Automatically adjust pass thresholds (lower for no-llm mode)

**Benefits:**
- Proves LLM value quantitatively
- Shows graceful degradation
- Identifies which patterns benefit most from LLM
- Provides cost-benefit data

## Running Tests

### Test All Fixtures
```bash
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py -v -m eval
```

### Test Specific Fixture
```bash
# Flat structure
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py::test_scenario[my-scenario] -v

# Nested structure - both modes
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py -k "my-scenario" -v

# Nested structure - specific mode
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py::test_scenario[my-scenario/llm] -v
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py::test_scenario[my-scenario/no-llm] -v
```

### Filter by Pattern
```bash
# All LLM mode tests
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py -k "llm" -v

# All No-LLM mode tests
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py -k "no-llm" -v

# All Job failure tests
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py -k "kubejob" -v
```

## Fixture Guidelines

### When to Use Comparison Mode

**Use comparison mode when:**
- Testing a new alert family for the first time
- Evaluating LLM ROI for a specific failure pattern
- Unknown failure modes (not in pattern library)
- Documenting quality improvements

**Use single mode when:**
- Known patterns with existing test coverage
- Quick iteration on expected outcomes
- Baseline testing without LLM cost

### Scoring Thresholds

**LLM Mode:**
- Known patterns: 75-85 (high bar)
- Unknown patterns: 85-95 (LLM should excel)

**No-LLM Mode:**
- Known patterns: 70-75 (deterministic patterns work)
- Unknown patterns: 45-55 (graceful degradation)

**Delta:**
- Known patterns: +5-10 points (incremental)
- Unknown patterns: +30-40 points (transformative)

### Expected Outcomes

Be specific but not brittle:

**Good:**
```yaml
root_cause:
  patterns:
    - 'S3.*(?:access denied|403|forbidden)'
    - 'AWS.*credentials'
  match_type: regex
```

**Too Specific (Brittle):**
```yaml
root_cause:
  exact: "The Job failed because S3 bucket 'my-bucket' returned 403 Forbidden due to missing IAM role 'my-role'"
```

**Too Generic:**
```yaml
root_cause:
  patterns: ["failed"]
  match_type: substring
```

### Evidence Profiles

Document what evidence is available:

**Complete Evidence:**
- Logs available
- Metrics available
- K8s events available
- Example: Exit code 1 failures

**Minimal Evidence:**
- No logs (pod never ran)
- No metrics (pod never started)
- K8s events only (if captured quickly)
- Example: ImagePullBackOff

**Degraded Evidence:**
- Logs expired (pod deleted)
- Events expired (>1 hour old)
- Historical fallback required
- Example: Long-running Job failures

## Current Fixtures

No fixtures are included in the initial release. See the capture guide above to create your own fixtures from your cluster.

## Interpreting COMPARISON.md

The comparison report shows:

### Evidence Quality Table
Quantitative metrics:
- Hypothesis count (more = better exploration)
- Top confidence (higher = more certain)
- Root cause specificity (generic vs specific)

### RCA Quality Comparison
Side-by-side root cause text:
- No-LLM: Often generic ("Job failed with exit 1")
- LLM: Specific ("Missing DATABASE_URL environment variable")

### Key Improvements
Checklist of enhancements:
- Specific error extraction
- Cloud-specific commands (AWS CLI, gcloud, etc.)
- Evidence citations (log lines, metrics)

### Cost-Benefit
ROI analysis:
- API calls made
- Time delta (LLM is slower)
- Quality improvement (point delta)

## Best Practices

### Capturing Fixtures

1. **Capture quickly** - K8s events expire (default: 1 hour)
2. **Use TTL** - Set `ttlSecondsAfterFinished: 600` on Jobs
3. **Document timeline** - Note when pod started/failed/deleted
4. **Test both modes** - Always use `--compare-modes` for new scenarios

### Writing Expected Outcomes

1. **Start broad** - Use regex for flexibility
2. **Test iteratively** - Run tests, adjust patterns
3. **Check both modes** - Ensure deterministic mode has achievable goals
4. **Document anti-patterns** - Note what agent should NOT do

### Maintaining Fixtures

1. **Update on breaking changes** - If pipeline output changes, update fixtures
2. **Regenerate periodically** - Capture fresh data every 3-6 months
3. **Prune obsolete** - Remove fixtures for deprecated alert rules
4. **Version control** - Track changes to expected outcomes

## Troubleshooting

### "No scenarios found"
- Ensure `scenario.yaml` and `investigation.json` exist
- Check file permissions
- Verify nested structure (llm/no-llm subdirectories)

### "Investigation failed during capture"
- Check port-forwards (Alertmanager, Prometheus, Logs)
- Verify alert is still active (`kubectl port-forward ...`)
- Check credentials (K8s, AWS, etc.)

### "Score below threshold"
- Review expected outcomes (too strict?)
- Check investigation output (missing evidence?)
- Compare to COMPARISON.md (is LLM disabled?)
- Adjust scoring weights or threshold

### "Events missing"
- Capture sooner (K8s events expire quickly)
- Increase Job TTL (`ttlSecondsAfterFinished`)
- Use historical fallback (logs from deleted pods)

## Contributing New Fixtures

When adding a new fixture:

1. **Create Job manifest** - Reproducible failure scenario
2. **Capture with comparison mode** - `--compare-modes`
3. **Document failure mode** - README.md with timeline
4. **Set appropriate thresholds** - Based on evidence profile
5. **Test both modes** - Ensure both pass their thresholds
6. **Analyze COMPARISON.md** - Document the delta
7. **Submit PR** - Include all files + test results

## Questions?

- Eval framework: See `eval/runner.py`
- Scoring logic: See `eval/scoring/scorer.py`
- Capture logic: See `eval/tools/capture.py`
- Pipeline code: See `agent/pipeline/pipeline.py`
