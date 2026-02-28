# RCA Evaluation Framework

Fixture-based evaluation system for testing Tarka's RCA quality.

## Overview

This framework enables **repeatable, CI-friendly testing** of the agent's root cause analysis (RCA) capabilities. Instead of running against live clusters, it captures complete Investigation objects from real incidents and replays them deterministically.

### Key Principles

1. **Investigation-Centric**: Capture the fully populated Investigation object (SSOT) rather than mocking individual provider calls
2. **CI-Friendly**: No live cluster dependencies, fast execution, deterministic results
3. **Quality-Focused**: Structured scoring against expected outcomes ("How close was the agent to finding the actual failure?")
4. **Extensible**: Easy to add new scenarios without code changes

## Quick Start

### 1. Capture a Fixture from Live Cluster

```bash
# Interactive capture (recommended for first time)
poetry run python -m eval.tools.capture \
  --interactive \
  --alert-index 0 \
  --output eval/fixtures/my_scenario

# Or non-interactive
poetry run python -m eval.tools.capture \
  --fingerprint abc123 \
  --output eval/fixtures/my_scenario \
  --scenario-name "Job ImagePullBackOff" \
  --failure-type image_pull
```

This creates:
- `investigation.json` - Captured Investigation object with all evidence
- `scenario.yaml` - Metadata + expected outcomes (template, needs editing)
- `README.md` - Human-readable documentation (template, needs editing)

### 2. Edit Expected Outcomes

Edit `eval/fixtures/my_scenario/scenario.yaml` to define:

```yaml
expected_outcomes:
  root_cause:
    patterns:
      - "ImagePullBackOff"
      - "image.*not found"
    match_type: "regex"  # exact, substring, regex, semantic

  proposed_fix:
    all_of:  # Must contain ALL of these
      - patterns: ["kubectl describe pod"]
        match_type: "substring"
    any_of:  # Must contain AT LEAST ONE
      - patterns: ["imagePullSecret", "ECR.*auth"]
        match_type: "regex"

  hypotheses:
    any_of: ["image not found", "authentication"]

  next_steps:
    command_types: ["kubectl", "aws"]
    must_include: ["describe pod"]

scoring:
  root_cause_weight: 0.4        # 40% of total score
  fix_accuracy_weight: 0.3      # 30%
  hypothesis_quality_weight: 0.2  # 20%
  next_steps_weight: 0.1        # 10%
  pass_threshold: 70            # Minimum score to pass
```

### 3. Run Evaluation

```bash
# Run all scenarios
poetry run pytest eval/runner.py -v

# Run specific scenario
poetry run pytest eval/runner.py::test_my_scenario -v

# Run with keyword filter
poetry run pytest eval/runner.py -k "image" -v

# Generate HTML report
poetry run pytest eval/runner.py --html=eval_report.html --self-contained-html

# Enable LLM enrichment (optional, slower)
poetry run pytest eval/runner.py --enable-llm -v
```

## Architecture

### Why Investigation-Centric?

Instead of mocking dozens of individual provider calls (complex request-response matching), we capture the **fully populated Investigation object** after evidence collection:

```
Real Cluster (once):
  Alert → run_investigation() → Investigation (with all evidence) → Save as fixture

CI/Local (many times):
  Load Investigation from fixture → Run analysis → Score RCA quality
```

**Benefits**:
- Investigation is already the SSOT (Single Source of Truth)
- Simpler capture: One JSON file vs. tracking dozens of provider calls
- No request-response matching complexity
- Better for LLM evaluation: LLM gets full Investigation context
- Easier to version and diff: Compare fixtures across agent versions

### What Gets Tested?

The framework tests **analysis and reasoning**, not evidence collection:

✅ **Tested** (replayed from fixture):
- Diagnostic module analysis
- Base triage decision building
- Hypothesis generation and scoring
- Feature computation
- Family enrichment
- LLM RCA (optional)

❌ **Not tested** (already captured in fixture):
- Prometheus queries
- Kubernetes API calls
- Log fetching
- Evidence collection logic

## Fixture Format

Each scenario directory contains:

```
eval/fixtures/my_scenario/
├── investigation.json    # Captured Investigation object (SSOT)
├── scenario.yaml        # Metadata + expected outcomes + scoring config
└── README.md           # Human-readable documentation
```

### scenario.yaml Structure

```yaml
name: "Human-readable scenario name"
family: "pod_not_healthy"
failure_type: "image_pull"
description: "Detailed description of failure"

captured_at: "2026-02-18T10:05:00Z"
cluster: "prod-cluster"

expected_outcomes:
  # What root cause should be identified?
  root_cause:
    patterns: ["ImagePullBackOff", "image.*not found"]
    match_type: "regex"

  # What failure mode should be classified?
  failure_mode:
    exact: "image_pull"

  # What should the proposed fix include?
  proposed_fix:
    all_of: [...]  # Must have ALL
    any_of: [...]  # Must have AT LEAST ONE

  # What should hypotheses mention?
  hypotheses:
    any_of: ["image not found", "authentication"]

  # What should next steps include?
  next_steps:
    command_types: ["kubectl", "aws"]
    must_include: ["describe pod"]

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

### investigation.json Structure

This is the complete Investigation object serialized to JSON:

```json
{
  "alert": {...},
  "time_window": {...},
  "target": {...},
  "evidence": {
    "k8s": {...},
    "metrics": {...},
    "logs": {...}
  },
  "analysis": {
    "decision": {...},
    "hypotheses": [...],
    "rca": {...}
  }
}
```

## Scoring System

RCA quality is scored on four components:

### 1. Root Cause Identification (40% default)

**What it checks**: Does the agent correctly identify the failure?

**Scoring logic**:
- 100 pts: Found in RCA root_cause field
- 90 pts: Mentioned in base decision why bullets
- 80 pts: Found in high-confidence hypothesis (≥80%)
- 0 pts: Not found

**Pass threshold**: ≥70

### 2. Fix Accuracy (30% default)

**What it checks**: Would the proposed fix actually work?

**Scoring logic**:
- 100 pts: All required elements present (all_of) AND at least one optional element (any_of)
- 50 pts: Some optional elements present but missing required elements
- 30 pts: Has fix content but missing key elements
- 0 pts: No fix proposed

**Pass threshold**: ≥60

### 3. Hypothesis Quality (20% default)

**What it checks**: Are hypotheses relevant to the actual failure?

**Scoring logic**:
- 100 pts: At least one hypothesis mentions expected failure mode
- 40 pts: Hypotheses exist but don't mention expected failure mode
- 0 pts: No hypotheses

**Pass threshold**: ≥50

### 4. Next Steps (10% default)

**What it checks**: Are next steps actionable?

**Scoring logic**:
- 100 pts: All expected command types present
- Proportional: N/M * 100 where N = found command types, M = expected
- 50 pts: Missing must-include patterns

**Pass threshold**: ≥50

### Total Score

```
Total = (root_cause × 0.4) + (fix_accuracy × 0.3) + (hypothesis_quality × 0.2) + (next_steps × 0.1)
```

Test passes if: `Total ≥ pass_threshold` (default: 70)

## Pattern Matching

Four match types available:

### 1. exact
```yaml
patterns: ["ImagePullBackOff"]
match_type: "exact"
```
Text must exactly match pattern (case-insensitive).

### 2. substring
```yaml
patterns: ["kubectl describe pod"]
match_type: "substring"
```
Text must contain pattern (case-insensitive).

### 3. regex
```yaml
patterns: ["image.*not found", "registry.*(unavailable|unreachable)"]
match_type: "regex"
```
Text must match regex pattern (case-insensitive, DOTALL).

### 4. semantic
```yaml
patterns: ["container startup failure"]
match_type: "semantic"
```
Future: Use embeddings for semantic similarity. Currently falls back to substring matching.

## CI Integration

### GitHub Actions

Add to `.github/workflows/eval.yml`:

```yaml
name: RCA Evaluation Tests

on:
  pull_request:
  push:
    branches: [main]

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install poetry
          poetry install

      - name: Run evaluation tests
        run: |
          poetry run pytest eval/runner.py \
            --junitxml=eval_results.xml \
            --html=eval_report.html \
            --self-contained-html

      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: eval-results
          path: |
            eval_results.xml
            eval_report.html

      - name: Comment PR with results
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v6
        with:
          script: |
            // Parse eval_results.xml and post summary comment
```

## Best Practices

### Capturing Fixtures

1. **Capture from real incidents**: Don't synthesize; capture actual failures
2. **Wait for evidence**: Let investigation complete before capturing
3. **Document context**: Fill in README.md with failure details
4. **Verify completeness**: Check that investigation.json has all expected evidence

### Defining Expected Outcomes

1. **Start broad**: Use regex patterns for flexibility
2. **Focus on outcomes**: "What should be found?" not "How should it be found?"
3. **Be realistic**: Agent won't always get 100%; set thresholds appropriately
4. **Test incrementally**: Run test after defining each outcome to verify scoring

### Organizing Scenarios

```
eval/fixtures/
├── job_failure_imagepullbackoff/
├── job_failure_oom/
├── pod_not_healthy_crashloop/
├── pod_not_healthy_liveness/
├── cpu_throttling_high/
└── http_5xx_spike/
```

Group by alert family and failure type. Use descriptive directory names.

## Troubleshooting

### Test fails with "Eval test tried to make live cluster call!"

This means the replay mechanism is broken. Check:
- Is `investigation.json` complete?
- Is evidence collection being triggered during replay?
- Check `eval/tools/replay.py` - should call diagnostics with `do_collect=False`

### Score is too low

1. Run with verbose output: `pytest eval/runner.py::test_scenario -vv`
2. Check "Diagnostic Information" section in output
3. Compare agent's output to expected outcomes
4. Adjust expected outcomes or improve agent

### Fixture capture fails

- Check that alert is still firing
- Verify cluster access (kubectl, Prometheus)
- Check that time window is appropriate
- Review agent logs for errors

## Development

### Adding New Scenarios

```bash
# 1. Capture fixture
poetry run python -m eval.tools.capture --interactive --alert-index 0 --output eval/fixtures/new_scenario

# 2. Edit scenario.yaml
vim eval/fixtures/new_scenario/scenario.yaml

# 3. Document in README.md
vim eval/fixtures/new_scenario/README.md

# 4. Run test
poetry run pytest eval/runner.py::test_new_scenario -v

# 5. Iterate until passing
```

### Extending Scoring Logic

Edit `eval/scoring/scorer.py` to add new scoring components or modify existing logic.

### Adding New Match Types

Edit `eval/scoring/matchers.py` to add new pattern matching strategies (e.g., semantic similarity with embeddings).

## Future Enhancements

- [ ] Semantic matching with embeddings
- [ ] Diff tool to compare expected vs actual
- [ ] Trend tracking: score changes over time
- [ ] LLM-as-judge for subjective quality assessment
- [ ] Automated fixture capture from production alerts
- [ ] Regression detection: alert if scores drop

## Related Documentation

- [Investigation Model](../agent/core/models.py) - Investigation SSOT structure
- [Pipeline](../agent/pipeline/pipeline.py) - Investigation orchestration
- [Diagnostics](../agent/diagnostics/) - Diagnostic modules
- [Triage Methodology](../docs/triage_methodology.md) - RCA philosophy
