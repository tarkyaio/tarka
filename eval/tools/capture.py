#!/usr/bin/env python3
"""
Fixture capture tool.

Usage:
    python -m eval.tools.capture --alert-index 0 --output eval/fixtures/my_scenario
    python -m eval.tools.capture --fingerprint abc123 --output eval/fixtures/my_scenario
    python -m eval.tools.capture --interactive --alert-index 0 --output eval/fixtures/my_scenario
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import click
import yaml

from agent.pipeline.pipeline import run_investigation
from agent.providers.alertmanager_provider import fetch_active_alerts


def capture_investigation(alert: Dict[str, Any], time_window: str) -> Dict[str, Any]:
    """
    Run investigation and serialize to dict.

    Args:
        alert: Alert dictionary from Alertmanager
        time_window: Time window string (e.g., '1h', '30m')

    Returns:
        Investigation data as dictionary
    """
    investigation = run_investigation(alert=alert, time_window=time_window)

    # Add RCA graph enrichment (matches production webhook behavior)
    # This populates investigation.analysis.rca when LLM is enabled
    try:
        from agent.graphs.rca import maybe_attach_rca

        maybe_attach_rca(
            alert=alert,
            time_window=time_window,
            investigation=investigation,
            parent_callbacks=None,
        )
    except Exception:
        # If RCA graph fails (langgraph not installed, LLM disabled, etc.), continue.
        # The investigation.analysis.rca field will remain null or show error status.
        pass

    return investigation.model_dump(mode="json", exclude_none=False)


def create_scenario_template(investigation_data: Dict, scenario_name: str, failure_type: str, captured_by: str) -> Dict:
    """
    Generate scenario.yaml template.

    Args:
        investigation_data: Captured investigation data
        scenario_name: Human-readable scenario name
        failure_type: Failure type classification
        captured_by: Name of person capturing fixture

    Returns:
        Scenario configuration dictionary
    """
    alert = investigation_data.get("alert", {})
    labels = alert.get("labels", {})

    return {
        "name": scenario_name,
        "family": investigation_data.get("family", "unknown"),
        "failure_type": failure_type,
        "description": f"TODO: Add description of {scenario_name}",
        "captured_at": datetime.utcnow().isoformat() + "Z",
        "captured_by": captured_by,
        "cluster": os.getenv("CLUSTER_NAME", "unknown"),
        "alert_labels": {
            "alertname": labels.get("alertname"),
            "namespace": labels.get("namespace"),
            "severity": labels.get("severity"),
        },
        "expected_outcomes": {
            "root_cause": {"patterns": ["TODO: Add expected root cause patterns"], "match_type": "regex"},
            "failure_mode": {"exact": failure_type},
            "proposed_fix": {
                "all_of": [{"patterns": ["TODO: Add required fix elements"], "match_type": "substring"}],
                "any_of": [{"patterns": ["TODO: Add optional fix elements"], "match_type": "regex"}],
            },
            "hypotheses": {"any_of": ["TODO: Add expected hypothesis keywords"]},
            "next_steps": {"command_types": ["kubectl"], "must_include": ["describe pod"]},
        },
        "scoring": {
            "root_cause_weight": 0.4,
            "fix_accuracy_weight": 0.3,
            "hypothesis_quality_weight": 0.2,
            "next_steps_weight": 0.1,
            "pass_threshold": 70,
        },
        "test_config": {"time_window": "1h", "enable_llm": False, "enable_diagnostics": True},
    }


def create_readme_template(scenario_name: str, failure_type: str) -> str:
    """Generate README.md template for scenario."""
    return f"""# {scenario_name}

## Overview

**Failure Type**: `{failure_type}`

TODO: Add detailed description of this failure scenario.

## What Happened

TODO: Describe the failure sequence:
- What triggered the alert?
- What was the root cause?
- What fixed it?

## Expected RCA Quality

This fixture tests whether the agent can:
- [ ] Identify the root cause correctly
- [ ] Propose a working fix
- [ ] Generate relevant hypotheses
- [ ] Provide actionable next steps

## Scoring Thresholds

- **Root Cause** (40%): Must mention TODO
- **Fix Accuracy** (30%): Must include TODO
- **Hypotheses** (20%): Should mention TODO
- **Next Steps** (10%): Should include kubectl commands

Total passing score: ≥70/100

## Notes

TODO: Add any special considerations or context.
"""


def generate_comparison_report(investigation_llm: Dict, investigation_no_llm: Dict, scenario_name: str) -> str:
    """
    Generate a comparison report between LLM and No-LLM investigations.

    Args:
        investigation_llm: Investigation data with LLM enrichment
        investigation_no_llm: Investigation data without LLM (deterministic only)
        scenario_name: Name of the scenario

    Returns:
        Markdown-formatted comparison report
    """
    # Extract analysis from both investigations
    analysis_llm = investigation_llm.get("analysis", {})
    analysis_no_llm = investigation_no_llm.get("analysis", {})

    # Extract hypotheses
    hypotheses_llm = analysis_llm.get("hypotheses", [])
    hypotheses_no_llm = analysis_no_llm.get("hypotheses", [])

    # Extract RCA
    rca_llm = analysis_llm.get("rca", {})
    rca_no_llm = analysis_no_llm.get("rca", {})

    # Extract decision
    decision_llm = analysis_llm.get("decision", {})
    decision_no_llm = analysis_no_llm.get("decision", {})

    # Calculate metrics
    hyp_count_llm = len(hypotheses_llm)
    hyp_count_no_llm = len(hypotheses_no_llm)
    hyp_delta = hyp_count_llm - hyp_count_no_llm

    top_conf_llm = hypotheses_llm[0].get("confidence_0_100", 0) if hypotheses_llm else 0
    top_conf_no_llm = hypotheses_no_llm[0].get("confidence_0_100", 0) if hypotheses_no_llm else 0
    conf_delta = top_conf_llm - top_conf_no_llm

    # Root causes
    root_cause_llm = rca_llm.get("root_cause", "") if rca_llm else ""
    root_cause_no_llm = rca_no_llm.get("root_cause", "") if rca_no_llm else ""

    # Remediation
    remediation_llm = rca_llm.get("remediation", "") if rca_llm else ""
    remediation_no_llm = rca_no_llm.get("remediation", "") if rca_no_llm else ""

    # Next steps
    next_steps_llm = decision_llm.get("next", []) if decision_llm else []
    next_steps_no_llm = decision_no_llm.get("next", []) if decision_no_llm else []

    # Build report
    report = f"""# LLM vs Deterministic Comparison

## Scenario: {scenario_name}

This document compares investigation results between LLM-enriched and deterministic-only modes for the same failure scenario.

## Evidence Quality

| Metric | No-LLM | LLM | Delta |
|--------|--------|-----|-------|
| Hypotheses count | {hyp_count_no_llm} | {hyp_count_llm} | {hyp_delta:+d} |
| Top hypothesis confidence | {top_conf_no_llm}% | {top_conf_llm}% | {conf_delta:+.0f}% |
| Root cause specificity | {'Generic' if not root_cause_no_llm or len(root_cause_no_llm) < 50 else 'Specific'} | {'Generic' if not root_cause_llm or len(root_cause_llm) < 50 else 'Specific'} | {'✅ Improved' if len(root_cause_llm) > len(root_cause_no_llm) else '➖ Similar'} |
| Remediation steps | {len(next_steps_no_llm)} | {len(next_steps_llm)} | {len(next_steps_llm) - len(next_steps_no_llm):+d} |

## RCA Quality

**No-LLM (Deterministic):**
```
{root_cause_no_llm or 'Not provided'}
```

**LLM (Enriched):**
```
{root_cause_llm or 'Not provided'}
```

## Remediation Comparison

**No-LLM:**
```
{remediation_no_llm or 'Not provided'}
```

**LLM:**
```
{remediation_llm or 'Not provided'}
```

## Top Hypotheses

### No-LLM Mode
"""

    if hypotheses_no_llm:
        for i, hyp in enumerate(hypotheses_no_llm[:3], 1):
            report += f"\n{i}. [{hyp.get('confidence_0_100', 0)}%] {hyp.get('title', 'N/A')}"
            if hyp.get("evidence"):
                report += f"\n   - Evidence: {hyp['evidence'][:100]}..."
    else:
        report += "\n(No hypotheses generated)"

    report += "\n\n### LLM Mode\n"

    if hypotheses_llm:
        for i, hyp in enumerate(hypotheses_llm[:3], 1):
            report += f"\n{i}. [{hyp.get('confidence_0_100', 0)}%] {hyp.get('title', 'N/A')}"
            if hyp.get("evidence"):
                report += f"\n   - Evidence: {hyp['evidence'][:100]}..."
    else:
        report += "\n(No hypotheses generated)"

    report += """

## Key Improvements

Compare the two investigations and note:
- [ ] Specific error extraction from logs
- [ ] Cloud-specific remediation commands
- [ ] Evidence citations (log lines, metrics)
- [ ] Confidence scoring improvements
- [ ] Hypothesis quality

## Cost-Benefit

- **LLM API calls:** TBD (check logs)
- **Time delta:** Compare timestamps
- **Quality improvement:** Compare scores when running tests

## Notes

This comparison was auto-generated during capture. To re-run both modes:
```bash
poetry run python scripts/capture-fixture.py --filter <AlertName> --compare-modes
```

To test both modes:
```bash
# Test LLM mode
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py -k "llm" -v

# Test No-LLM mode
EVAL_REPLAY_MODE=true poetry run pytest eval/runner.py -k "no-llm" -v
```
"""

    return report


@click.command()
@click.option("--alert-index", type=int, help="Alert index from list-alerts")
@click.option("--fingerprint", type=str, help="Alert fingerprint")
@click.option("--output", type=click.Path(), required=True, help="Output directory for fixture")
@click.option("--time-window", default="1h", help="Time window for investigation")
@click.option("--scenario-name", help="Human-readable scenario name")
@click.option("--failure-type", help="Failure type classification")
@click.option("--interactive", is_flag=True, help="Prompt for inputs interactively")
def capture(alert_index, fingerprint, output, time_window, scenario_name, failure_type, interactive):
    """Capture fixture from live cluster."""
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Fetch alert
    click.echo("Fetching active alerts...")
    alerts = fetch_active_alerts()

    if not alerts:
        click.echo("Error: No active alerts found", err=True)
        return 1

    # Select alert
    alert = None
    if alert_index is not None:
        if alert_index >= len(alerts):
            click.echo(f"Error: Alert index {alert_index} out of range (0-{len(alerts)-1})", err=True)
            return 1
        alert = alerts[alert_index]
    elif fingerprint:
        alert = next((a for a in alerts if a.get("fingerprint") == fingerprint), None)
        if not alert:
            click.echo(f"Error: Alert with fingerprint {fingerprint} not found", err=True)
            return 1
    else:
        click.echo("Error: Must provide --alert-index or --fingerprint", err=True)
        return 1

    # Get scenario details
    alertname = alert["labels"].get("alertname", "unknown")

    if interactive or not scenario_name:
        scenario_name = click.prompt("Scenario name", default=alertname)
    elif not scenario_name:
        scenario_name = alertname

    if interactive or not failure_type:
        failure_type = click.prompt("Failure type", default="unknown")
    elif not failure_type:
        failure_type = "unknown"

    captured_by = (
        click.prompt("Your name", default=os.getenv("USER", "unknown")) if interactive else os.getenv("USER", "unknown")
    )

    click.echo(f"\n{'='*60}")
    click.echo(f"Capturing: {alertname}")
    click.echo(f"Fingerprint: {alert.get('fingerprint', 'N/A')}")
    click.echo(f"Namespace: {alert['labels'].get('namespace', 'N/A')}")
    click.echo(f"Time window: {time_window}")
    click.echo(f"{'='*60}\n")

    # Run investigation
    click.echo("Running investigation (this may take 30-60 seconds)...")
    try:
        investigation_data = capture_investigation(alert, time_window)
    except Exception as e:
        click.echo(f"Error running investigation: {e}", err=True)
        return 1

    # Write files
    click.echo(f"Writing fixture to {output_dir}...")

    investigation_path = output_dir / "investigation.json"
    investigation_path.write_text(json.dumps(investigation_data, indent=2))

    scenario_path = output_dir / "scenario.yaml"
    scenario_path.write_text(
        yaml.dump(
            create_scenario_template(investigation_data, scenario_name, failure_type, captured_by),
            default_flow_style=False,
            sort_keys=False,
        )
    )

    readme_path = output_dir / "README.md"
    readme_path.write_text(create_readme_template(scenario_name, failure_type))

    click.echo("\n✓ Fixture captured successfully!")
    click.echo("\nFiles created:")
    click.echo(f"  - {investigation_path}")
    click.echo(f"  - {scenario_path}")
    click.echo(f"  - {readme_path}")
    click.echo("\nNext steps:")
    click.echo(f"  1. Edit {scenario_path} to fill in expected outcomes")
    click.echo(f"  2. Edit {readme_path} to document the scenario")
    click.echo(f"  3. Run: pytest eval/runner.py::test_{output_dir.name} -v")


if __name__ == "__main__":
    capture()
