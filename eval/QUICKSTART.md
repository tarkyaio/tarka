# Quick Start Guide

## Capture a New Scenario

```bash
# Interactive mode (recommended)
poetry run python -m eval.tools.capture \
  --interactive \
  --alert-index 0 \
  --output eval/fixtures/my_scenario

# Non-interactive mode
poetry run python -m eval.tools.capture \
  --fingerprint abc123def \
  --scenario-name "Job ImagePullBackOff" \
  --failure-type image_pull \
  --output eval/fixtures/my_scenario
```

## Edit Expected Outcomes

Edit `eval/fixtures/my_scenario/scenario.yaml`:

```yaml
expected_outcomes:
  root_cause:
    patterns: ["ImagePullBackOff", "image.*not found"]
    match_type: "regex"

  proposed_fix:
    all_of:
      - patterns: ["kubectl describe"]
        match_type: "substring"
    any_of:
      - patterns: ["imagePullSecret", "registry.*auth"]
        match_type: "regex"
```

## Run Tests

```bash
# All scenarios
poetry run pytest eval/runner.py -v

# Specific scenario
poetry run pytest eval/runner.py::test_my_scenario -v

# With LLM enrichment (slower)
poetry run pytest eval/runner.py --enable-llm -v
```

## Pattern Match Types

- **exact**: `"ImagePullBackOff"` - Exact match (case-insensitive)
- **substring**: `"kubectl describe"` - Contains text (case-insensitive)
- **regex**: `"image.*not found"` - Regex pattern
- **semantic**: Future embedding similarity (currently falls back to substring)

## Scoring Components

| Component | Weight | Pass Threshold | What It Checks |
|-----------|--------|----------------|----------------|
| Root Cause | 40% | ≥70 | Did it find the right failure? |
| Fix Accuracy | 30% | ≥60 | Would the fix work? |
| Hypotheses | 20% | ≥50 | Are hypotheses relevant? |
| Next Steps | 10% | ≥50 | Are next steps actionable? |

**Total pass threshold**: ≥70

## Common Tasks

### List all scenarios
```bash
ls -1 eval/fixtures/
```

### View a fixture
```bash
cat eval/fixtures/my_scenario/scenario.yaml
cat eval/fixtures/my_scenario/investigation.json | jq .
```

### View investigation report (same as Agent UI)
```bash
# View the full investigation report (opens in default viewer)
poetry run python -m eval.tools.view_report \
  --fixture eval/fixtures/kubejobfailed

# Save to specific file
poetry run python -m eval.tools.view_report \
  --fixture eval/fixtures/kubejobfailed \
  --output /tmp/my_report.md

# View in terminal
poetry run python -m eval.tools.view_report \
  --fixture eval/fixtures/kubejobfailed \
  --output /tmp/report.md \
  --no-open && cat /tmp/report.md
```

**What this report contains:**
- Full triage summary (base decision)
- All hypotheses with evidence and next steps
- Verdict classification and scores
- Complete appendix (logs, events, metrics)
- This is the **exact same report** shown in the Agent UI

### Debug a failing test
```bash
pytest eval/runner.py::test_my_scenario -vv --tb=short
```

## File Structure

```
eval/fixtures/my_scenario/
├── investigation.json     # Captured Investigation (SSOT)
├── scenario.yaml         # Expected outcomes & scoring config
└── README.md            # Human-readable documentation
```

## Tips

1. **Start broad**: Use regex patterns for flexibility
2. **Focus on outcomes**: "What should be found?" not "How?"
3. **Adjust thresholds**: Not every scenario needs 100% on every component
4. **Document well**: Future you will thank you
5. **Test incrementally**: Run test after each expected outcome

## See Also

- [Full Documentation](README.md) - Comprehensive guide
- [Implementation Summary](IMPLEMENTATION_SUMMARY.md) - Architecture details
