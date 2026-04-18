#!/usr/bin/env python3
"""Stamp the Helm chart version, appVersion, container image tags, and the
artifacthub.io/changes annotation from CHANGELOG.md.

Usage:
    python3 scripts/stamp-chart-version.py <version> [chart-yaml-path] [changelog-path]

Example:
    python3 scripts/stamp-chart-version.py 0.4.1
    python3 scripts/stamp-chart-version.py 0.4.1 deploy/chart/Chart.yaml CHANGELOG.md
"""

import re
import sys
from pathlib import Path

CHART_YAML_DEFAULT = Path("deploy/chart/Chart.yaml")
CHANGELOG_DEFAULT = Path("CHANGELOG.md")

IMAGE_PATTERNS = [
    # Order matters: most specific suffixes first, bare tag last.
    (r"(ghcr\.io/tarkyaio/tarka-ui:)[^\s]+", r"\g<1>{version}"),
    (r"(ghcr\.io/tarkyaio/tarka:)[^\s]+-full", r"\g<1>{version}-full"),
    (r"(ghcr\.io/tarkyaio/tarka:)[^\s]+-llm", r"\g<1>{version}-llm"),
    (r"(ghcr\.io/tarkyaio/tarka:)\d+\.\d+\.\d+(?!-)", r"\g<1>{version}"),
]

# Matches the artifacthub.io/changes YAML literal block in Chart.yaml,
# capturing the header line and the 4-space-indented body.
CHANGES_BLOCK_RE = re.compile(
    r"(^  artifacthub\.io/changes: \|\n)((?:^    .*\n)+)",
    re.MULTILINE,
)

# Keep-a-Changelog headings mapped to ArtifactHub change kinds.
# Unknown headings (e.g. "Dependencies", "Documentation") fall back to "changed".
SECTION_TO_KIND = {
    "added": "added",
    "changed": "changed",
    "deprecated": "deprecated",
    "removed": "removed",
    "fixed": "fixed",
    "security": "security",
}


def extract_changelog_entries(version: str, changelog_path: Path) -> list[tuple[str, str]]:
    text = changelog_path.read_text(encoding="utf-8")
    section_re = re.compile(
        rf"^## \[{re.escape(version)}\].*?(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = section_re.search(text)
    if not match:
        raise SystemExit(
            f"Error: no CHANGELOG entry for version {version} in {changelog_path}"
        )

    entries: list[tuple[str, str]] = []
    current_kind: str | None = None
    for line in match.group(0).splitlines():
        subheading = re.match(r"^### (\w+)", line)
        if subheading:
            current_kind = SECTION_TO_KIND.get(subheading.group(1).lower(), "changed")
            continue
        if current_kind is None:
            continue
        bullet = re.match(r"^- (.+)", line)
        if bullet:
            entries.append((current_kind, bullet.group(1).strip()))

    if not entries:
        raise SystemExit(
            f"Error: CHANGELOG section for {version} has no bullet entries"
        )
    return entries


def render_changes(entries: list[tuple[str, str]]) -> str:
    lines: list[str] = []
    for kind, description in entries:
        escaped = description.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f"    - kind: {kind}")
        lines.append(f'      description: "{escaped}"')
    return "\n".join(lines) + "\n"


def stamp(version: str, chart_path: Path, changelog_path: Path) -> None:
    original = chart_path.read_text(encoding="utf-8")
    text = original
    text = re.sub(r"^version:.*", f"version: {version}", text, flags=re.MULTILINE)
    text = re.sub(r"^appVersion:.*", f'appVersion: "{version}"', text, flags=re.MULTILINE)

    for pattern, replacement in IMAGE_PATTERNS:
        text = re.sub(pattern, replacement.format(version=version), text)

    entries = extract_changelog_entries(version, changelog_path)
    rendered = render_changes(entries)
    new_text, substitutions = CHANGES_BLOCK_RE.subn(
        lambda m: m.group(1) + rendered, text, count=1
    )
    if substitutions == 0:
        raise SystemExit(
            f"Error: could not find artifacthub.io/changes block in {chart_path}"
        )
    text = new_text

    if text == original:
        print(f"{chart_path} already at version {version}")
        return

    chart_path.write_text(text, encoding="utf-8")
    print(f"Stamped {chart_path} with version {version}")


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


def main() -> None:
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <version> [chart-yaml-path] [changelog-path]",
            file=sys.stderr,
        )
        sys.exit(1)

    version = sys.argv[1]
    if not VERSION_RE.match(version):
        print(
            f"Error: invalid version '{version}' (expected semver like 1.2.3)",
            file=sys.stderr,
        )
        sys.exit(1)

    chart_path = Path(sys.argv[2]) if len(sys.argv) > 2 else CHART_YAML_DEFAULT
    changelog_path = Path(sys.argv[3]) if len(sys.argv) > 3 else CHANGELOG_DEFAULT

    try:
        stamp(version, chart_path, changelog_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
