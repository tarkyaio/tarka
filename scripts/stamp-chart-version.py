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
    original = chart_path.read_text(encoding="utf-8")
    text = original
    text = re.sub(r"^version:.*", f"version: {version}", text, flags=re.MULTILINE)
    text = re.sub(r"^appVersion:.*", f'appVersion: "{version}"', text, flags=re.MULTILINE)

    # ArtifactHub annotations
    for pattern, replacement in IMAGE_PATTERNS:
        text = re.sub(pattern, replacement.format(version=version), text)

    if text == original:
        print(f"{chart_path} already at version {version}")
        return

    chart_path.write_text(text, encoding="utf-8")
    print(f"Stamped {chart_path} with version {version}")


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <version> [chart-yaml-path]", file=sys.stderr)
        sys.exit(1)

    version = sys.argv[1]
    if not VERSION_RE.match(version):
        print(
            f"Error: invalid version '{version}' (expected semver like 1.2.3)",
            file=sys.stderr,
        )
        sys.exit(1)

    chart_path = Path(sys.argv[2]) if len(sys.argv) > 2 else CHART_YAML_DEFAULT

    try:
        stamp(version, chart_path)
    except FileNotFoundError:
        print(f"Error: {chart_path} not found", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
