#!/usr/bin/env python3
"""
Simple fixture capture script with interactive alert selection.

Usage:
    # Single-mode capture (no LLM)
    python scripts/capture-fixture.py
    python scripts/capture-fixture.py --filter KubeJobFailed

    # Comparison mode (LLM vs No-LLM)
    # First, set up .env.fixture with LLM credentials (see .env.fixture.example)
    set -a && source .env.fixture && set +a
    python scripts/capture-fixture.py --filter KubeJobFailed --compare-modes
"""

import click

from agent.providers.alertmanager_provider import fetch_active_alerts


def select_alert(alerts, filter_name=None):
    """Interactively select an alert."""
    if filter_name:
        alerts = [a for a in alerts if a["labels"].get("alertname") == filter_name]
        if not alerts:
            click.echo(f"No alerts found matching: {filter_name}", err=True)
            return None

    click.echo("\n" + "=" * 80)
    click.echo(f"Found {len(alerts)} active alerts")
    click.echo("=" * 80 + "\n")

    for i, alert in enumerate(alerts[:20]):  # Show first 20
        labels = alert["labels"]
        alertname = labels.get("alertname", "N/A")
        namespace = labels.get("namespace", "N/A")
        severity = labels.get("severity", "N/A")

        # Extra info depending on alert type
        extra = ""
        if "job_name" in labels:
            extra = f" | Job: {labels['job_name']}"
        elif "pod" in labels:
            extra = f" | Pod: {labels['pod']}"

        click.echo(f"[{i:3d}] {alertname:30s} | NS: {namespace:20s} | {severity:8s}{extra}")

    if len(alerts) > 20:
        click.echo(f"\n... and {len(alerts) - 20} more. Use --filter to narrow down.")

    click.echo()
    return alerts


@click.command()
@click.option("--filter", help="Filter by alert name (e.g., KubeJobFailed)")
@click.option("--compare-modes", is_flag=True, help="Capture both with and without LLM for comparison")
def main(filter, compare_modes):
    """Capture a fixture from active alerts."""

    click.echo("Fetching active alerts...")
    try:
        alerts = fetch_active_alerts()
    except Exception as e:
        click.echo(f"\nError: {e}", err=True)
        click.echo("\nMake sure you have port-forwarded Alertmanager:", err=True)
        click.echo(
            "  kubectl port-forward -n observability svc/prometheus-kube-prometheus-alertmanager 9093:9093", err=True
        )
        click.echo("\nAnd set the environment variable:", err=True)
        click.echo("  export ALERTMANAGER_URL=http://localhost:9093", err=True)
        return 1

    if not alerts:
        click.echo("No active alerts found.", err=True)
        return 1

    alerts = select_alert(alerts, filter)
    if not alerts:
        return 1

    # Get user selection
    while True:
        try:
            index = click.prompt("\nSelect alert index", type=int)
            if 0 <= index < len(alerts):
                break
            click.echo(f"Invalid index. Must be between 0 and {len(alerts)-1}")
        except (ValueError, click.Abort):
            return 1

    alert = alerts[index]
    labels = alert["labels"]

    # Show selected alert
    click.echo("\n" + "=" * 80)
    click.echo("Selected Alert:")
    click.echo(f"  Name: {labels.get('alertname')}")
    click.echo(f"  Namespace: {labels.get('namespace')}")
    click.echo(f"  Fingerprint: {alert.get('fingerprint')}")
    if "job_name" in labels:
        click.echo(f"  Job: {labels['job_name']}")
    click.echo("=" * 80 + "\n")

    # Get scenario details
    scenario_name = click.prompt("Scenario name", default=labels.get("alertname"))
    failure_type = click.prompt("Failure type", default="unknown")
    output_dir = click.prompt("Output directory", default=f"eval/fixtures/{scenario_name.lower().replace(' ', '_')}")

    if not click.confirm(f"\nCapture to {output_dir}?"):
        click.echo("Cancelled.")
        return 0

    # Run capture
    import json
    import os
    from pathlib import Path

    import yaml

    from eval.tools.capture import (
        capture_investigation,
        create_readme_template,
        create_scenario_template,
        generate_comparison_report,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    captured_by = os.getenv("USER", "unknown")

    try:
        if compare_modes:
            # Validate LLM configuration before proceeding
            llm_provider = os.getenv("LLM_PROVIDER", "vertexai").strip().lower()
            required_vars = []

            # Check if SDK is installed
            if llm_provider == "anthropic":
                try:
                    import langchain_anthropic  # noqa: F401
                except ImportError:
                    click.echo("\n❌ Error: langchain-anthropic SDK not installed", err=True)
                    click.echo(
                        "\nThe Anthropic SDK is an optional dependency and must be installed explicitly:", err=True
                    )
                    click.echo("  poetry install --extras anthropic", err=True)
                    click.echo("\nOr install all LLM providers:", err=True)
                    click.echo("  poetry install --extras all-providers", err=True)
                    return 1

                if not os.getenv("ANTHROPIC_API_KEY"):
                    required_vars.append("ANTHROPIC_API_KEY")

            elif llm_provider == "vertexai":
                try:
                    import langchain_google_vertexai  # noqa: F401
                except ImportError:
                    click.echo("\n❌ Error: langchain-google-vertexai SDK not installed", err=True)
                    click.echo(
                        "\nThe Vertex AI SDK is an optional dependency and must be installed explicitly:", err=True
                    )
                    click.echo("  poetry install --extras vertex", err=True)
                    click.echo("\nOr install all LLM providers:", err=True)
                    click.echo("  poetry install --extras all-providers", err=True)
                    return 1

                if not os.getenv("GOOGLE_CLOUD_PROJECT"):
                    required_vars.append("GOOGLE_CLOUD_PROJECT")
                if not os.getenv("GOOGLE_CLOUD_LOCATION"):
                    required_vars.append("GOOGLE_CLOUD_LOCATION")

            if required_vars:
                click.echo(f"\n❌ Error: LLM configuration incomplete for provider '{llm_provider}'", err=True)
                click.echo("\nMissing required environment variables:", err=True)
                for var in required_vars:
                    click.echo(f"  - {var}", err=True)
                click.echo("\nRecommended setup:", err=True)
                click.echo("  1. Create a .env.fixture file (DO NOT COMMIT):", err=True)
                click.echo("     echo '.env.fixture' >> .gitignore", err=True)
                if llm_provider == "anthropic":
                    click.echo("\n  2. Add your Anthropic configuration:", err=True)
                    click.echo("     cat > .env.fixture <<'EOF'", err=True)
                    click.echo("LLM_ENABLED=true", err=True)
                    click.echo("LLM_PROVIDER=anthropic", err=True)
                    click.echo("LLM_MODEL=claude-sonnet-4-20250514", err=True)
                    click.echo("LLM_TEMPERATURE=0.2", err=True)
                    click.echo("LLM_MAX_OUTPUT_TOKENS=4096", err=True)
                    click.echo("ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx", err=True)
                    click.echo("EOF", err=True)
                else:
                    click.echo("\n  2. Add your Vertex AI configuration:", err=True)
                    click.echo("     cat > .env.fixture <<'EOF'", err=True)
                    click.echo("LLM_ENABLED=true", err=True)
                    click.echo("LLM_PROVIDER=vertexai", err=True)
                    click.echo("LLM_MODEL=gemini-2.5-flash", err=True)
                    click.echo("GOOGLE_CLOUD_PROJECT=your-project-id", err=True)
                    click.echo("GOOGLE_CLOUD_LOCATION=us-central1", err=True)
                    click.echo("EOF", err=True)
                click.echo("\n  3. Load the configuration:", err=True)
                click.echo("     set -a && source .env.fixture && set +a", err=True)
                click.echo("\n  4. Re-run the capture script", err=True)
                return 1

            # Capture both modes for comparison
            click.echo("\n" + "=" * 80)
            click.echo("COMPARISON MODE: Capturing both LLM and No-LLM investigations")
            click.echo(f"LLM Provider: {llm_provider}")
            click.echo(f"LLM Model: {os.getenv('LLM_MODEL', 'default')}")
            click.echo("=" * 80)

            # Create subdirectories
            llm_path = output_path / "llm"
            no_llm_path = output_path / "no-llm"
            llm_path.mkdir(parents=True, exist_ok=True)
            no_llm_path.mkdir(parents=True, exist_ok=True)

            # Capture with LLM
            click.echo("\n[1/2] Running investigation WITH LLM enrichment...")
            os.environ["LLM_ENABLED"] = "true"
            investigation_llm = capture_investigation(alert, "1h")

            # Write LLM mode files
            (llm_path / "investigation.json").write_text(json.dumps(investigation_llm, indent=2))
            scenario_llm = create_scenario_template(
                investigation_llm, f"{scenario_name} (LLM)", failure_type, captured_by
            )
            scenario_llm["test_config"]["enable_llm"] = True
            (llm_path / "scenario.yaml").write_text(yaml.dump(scenario_llm, default_flow_style=False, sort_keys=False))
            (llm_path / "README.md").write_text(create_readme_template(f"{scenario_name} (LLM)", failure_type))

            click.echo("      ✓ LLM mode captured")

            # Capture without LLM
            click.echo("\n[2/2] Running investigation WITHOUT LLM (deterministic only)...")
            os.environ["LLM_ENABLED"] = "false"
            investigation_no_llm = capture_investigation(alert, "1h")

            # Write No-LLM mode files
            (no_llm_path / "investigation.json").write_text(json.dumps(investigation_no_llm, indent=2))
            scenario_no_llm = create_scenario_template(
                investigation_no_llm, f"{scenario_name} (No LLM)", failure_type, captured_by
            )
            scenario_no_llm["test_config"]["enable_llm"] = False
            scenario_no_llm["scoring"]["pass_threshold"] = max(
                50, scenario_no_llm["scoring"]["pass_threshold"] - 20
            )  # Lower threshold for deterministic
            (no_llm_path / "scenario.yaml").write_text(
                yaml.dump(scenario_no_llm, default_flow_style=False, sort_keys=False)
            )
            (no_llm_path / "README.md").write_text(create_readme_template(f"{scenario_name} (No LLM)", failure_type))

            click.echo("      ✓ No-LLM mode captured")

            # Generate comparison report
            click.echo("\n[3/3] Generating comparison report...")
            comparison_md = generate_comparison_report(investigation_llm, investigation_no_llm, scenario_name)
            (output_path / "COMPARISON.md").write_text(comparison_md)

            click.echo("\n" + "=" * 80)
            click.echo("✓ Comparison capture completed successfully!")
            click.echo("=" * 80)
            click.echo("\nFiles created:")
            click.echo(f"  - {llm_path}/investigation.json")
            click.echo(f"  - {llm_path}/scenario.yaml")
            click.echo(f"  - {llm_path}/README.md")
            click.echo(f"  - {no_llm_path}/investigation.json")
            click.echo(f"  - {no_llm_path}/scenario.yaml")
            click.echo(f"  - {no_llm_path}/README.md")
            click.echo(f"  - {output_path}/COMPARISON.md")

            click.echo("\nNext steps:")
            click.echo(f"  1. Review {output_path}/COMPARISON.md to see the delta")
            click.echo("  2. Edit scenario.yaml files to adjust expected outcomes")
            click.echo(f"  3. Run: pytest eval/runner.py -k {output_path.name} -v")

        else:
            # Single mode capture (original behavior)
            click.echo("\nRunning investigation (this may take 30-60 seconds)...")
            investigation_data = capture_investigation(alert, "1h")

            # Write files
            (output_path / "investigation.json").write_text(json.dumps(investigation_data, indent=2))

            scenario = create_scenario_template(investigation_data, scenario_name, failure_type, captured_by)
            (output_path / "scenario.yaml").write_text(yaml.dump(scenario, default_flow_style=False, sort_keys=False))

            (output_path / "README.md").write_text(create_readme_template(scenario_name, failure_type))

            click.echo("\n✓ Fixture captured successfully!")
            click.echo("\nFiles created:")
            click.echo(f"  - {output_path}/investigation.json")
            click.echo(f"  - {output_path}/scenario.yaml")
            click.echo(f"  - {output_path}/README.md")
            click.echo("\nNext steps:")
            click.echo(f"  1. Edit {output_path}/scenario.yaml to fill in expected outcomes")
            click.echo(f"  2. Edit {output_path}/README.md to document the scenario")
            click.echo(f"  3. Run: pytest eval/runner.py::test_{output_path.name} -v")

    except Exception as e:
        click.echo(f"\nError during capture: {e}", err=True)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    main()
