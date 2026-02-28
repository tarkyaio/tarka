#!/usr/bin/env python3
"""View investigation report from a fixture (same report as Agent UI).

Usage:
    poetry run python -m eval.tools.view_report --fixture eval/fixtures/kubejobfailed
    poetry run python -m eval.tools.view_report --fixture eval/fixtures/kubejobfailed --output /tmp/report.md
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

from agent.report import render_report
from eval.tools.replay import run_investigation_from_fixture


def main():
    parser = argparse.ArgumentParser(description="View investigation report from fixture")
    parser.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="Path to fixture directory (e.g., eval/fixtures/kubejobfailed)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output file path (default: temp file)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't automatically open the report",
    )
    parser.add_argument(
        "--enable-llm",
        action="store_true",
        help="Enable LLM enrichment",
    )

    args = parser.parse_args()

    # Run investigation from fixture
    print(f"Loading fixture from: {args.fixture}")
    investigation = run_investigation_from_fixture(args.fixture, enable_llm=args.enable_llm)

    # Render report
    print("Rendering investigation report...")
    report_md = render_report(investigation)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        # Create temp file
        fd, temp_path = tempfile.mkstemp(suffix=".md", prefix="investigation_report_")
        output_path = Path(temp_path)

    # Write report
    output_path.write_text(report_md)
    print(f"\n✓ Report written to: {output_path}")
    print(f"  Lines: {len(report_md.splitlines())}")
    print(f"  Size: {len(report_md)} bytes")

    # Show summary
    print("\nInvestigation Summary:")
    print(f"  Alert: {investigation.alert.labels.get('alertname', 'unknown')}")
    print(f"  Target: {investigation.target.workload_name or investigation.target.pod or 'unknown'}")
    print(f"  Hypotheses: {len(investigation.analysis.hypotheses)}")
    for i, hyp in enumerate(investigation.analysis.hypotheses[:3], 1):
        print(f"    {i}. [{hyp.confidence_0_100}%] {hyp.title}")

    # Open in viewer
    if not args.no_open:
        print("\nOpening report...")
        try:
            # Try to open with default markdown viewer or text editor
            subprocess.run(["open", str(output_path)], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"Could not auto-open. View manually: cat {output_path}")
    else:
        print(f"\nView report: cat {output_path}")


if __name__ == "__main__":
    main()
