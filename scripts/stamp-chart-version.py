#!/usr/bin/env python3
"""Stamp the Helm chart version, appVersion, and container image tags.

Usage:
    python3 scripts/stamp-chart-version.py <version> [chart-yaml-path]

Example:
    python3 scripts/stamp-chart-version.py 0.3.0
    python3 scripts/stamp-chart-version.py 0.3.0 deploy/chart/Chart.yaml
"""

import re
import sys
from pathlib import Path

CHART_YAML_DEFAULT = Path("deploy/chart/Chart.yaml")

IMAGE_PATTERNS = [
    # Order matters: tarka-ui first (most specific), then -all-providers, then bare tag
    (r"(ghcr\.io/tarkyaio/tarka-ui:)[^\s]+", r"\g<1>{version}"),
    (
        r"(ghcr\.io/tarkyaio/tarka:)[^\s]+-all-providers",
        r"\g<1>{version}-all-providers",
    ),
    (r"(ghcr\.io/tarkyaio/tarka:)\d+\.\d+\.\d+(?!-)", r"\g<1>{version}"),
]


def stamp(version: str, chart_path: Path) -> None:
    text = chart_path.read_text()

    # Chart version and appVersion
    text = re.sub(r"^version:.*", f"version: {version}", text, flags=re.MULTILINE)
    text = re.sub(r"^appVersion:.*", f'appVersion: "{version}"', text, flags=re.MULTILINE)

    # Container image tags in ArtifactHub annotations
    for pattern, replacement in IMAGE_PATTERNS:
        text = re.sub(pattern, replacement.format(version=version), text)

    chart_path.write_text(text)
    print(f"Stamped {chart_path} with version {version}")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <version> [chart-yaml-path]", file=sys.stderr)
        sys.exit(1)

    version = sys.argv[1]
    chart_path = Path(sys.argv[2]) if len(sys.argv) > 2 else CHART_YAML_DEFAULT

    if not chart_path.exists():
        print(f"Error: {chart_path} not found", file=sys.stderr)
        sys.exit(1)

    stamp(version, chart_path)


if __name__ == "__main__":
    main()
