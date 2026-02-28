"""
Evaluation runner (pytest-based).

Usage:
    pytest eval/runner.py -v
    pytest eval/runner.py::test_job_failure_imagepullbackoff -v
    pytest eval/runner.py --html=eval_report.html
    pytest eval/runner.py -k "image" -v
"""

from pathlib import Path

import pytest
import yaml

from eval.scoring.scorer import score_rca_quality
from eval.tools.replay import run_investigation_from_fixture

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def discover_scenarios():
    """
    Discover all scenario fixtures.

    Walks through fixtures/ directory and finds all subdirectories
    containing scenario.yaml and investigation.json files.

    Supports both flat and nested structures:
    - Flat: fixtures/my-scenario/{scenario.yaml, investigation.json}
    - Nested: fixtures/my-scenario/llm/{scenario.yaml, investigation.json}
              fixtures/my-scenario/no-llm/{scenario.yaml, investigation.json}

    Returns:
        List of (scenario_name, fixture_dir) tuples for parametrization
    """
    scenarios = []
    if not FIXTURES_DIR.exists():
        return scenarios

    for scenario_dir in FIXTURES_DIR.iterdir():
        if not scenario_dir.is_dir():
            continue

        scenario_file = scenario_dir / "scenario.yaml"
        investigation_file = scenario_dir / "investigation.json"

        # Check for flat structure (legacy)
        if scenario_file.exists() and investigation_file.exists():
            scenarios.append((scenario_dir.name, scenario_dir))
        else:
            # Check for nested structure (llm/no-llm subdirectories)
            for mode_dir in scenario_dir.iterdir():
                if not mode_dir.is_dir():
                    continue

                nested_scenario_file = mode_dir / "scenario.yaml"
                nested_investigation_file = mode_dir / "investigation.json"

                if nested_scenario_file.exists() and nested_investigation_file.exists():
                    # Use format: "parent-dir/mode" for test name
                    test_name = f"{scenario_dir.name}/{mode_dir.name}"
                    scenarios.append((test_name, mode_dir))

    return scenarios


@pytest.mark.eval
@pytest.mark.parametrize("scenario_name,fixture_dir", discover_scenarios())
def test_scenario(scenario_name: str, fixture_dir: Path):
    """
    Test a single scenario.

    Loads the fixture, runs investigation analysis (replay mode),
    scores RCA quality, and asserts against the pass threshold.

    Args:
        scenario_name: Name of the scenario (for test identification)
        fixture_dir: Path to fixture directory
    """
    # Load scenario config
    with open(fixture_dir / "scenario.yaml") as f:
        scenario = yaml.safe_load(f)

    # Run investigation from fixture (no live cluster calls)
    test_config = scenario.get("test_config", {})
    enable_llm = test_config.get("enable_llm", False)

    investigation = run_investigation_from_fixture(fixture_dir, enable_llm=enable_llm)

    # Score RCA quality
    score_result = score_rca_quality(
        investigation=investigation,
        expected_outcomes=scenario["expected_outcomes"],
        scoring_weights=scenario["scoring"],
    )

    # Print detailed results
    print(f"\n{'='*70}")
    print(f"Scenario: {scenario['name']}")
    print(f"{'='*70}")
    print(f"\nTotal Score: {score_result['total_score']:.1f}/100")
    print(f"Pass Threshold: {scenario['scoring']['pass_threshold']}")

    passed_count = sum(1 for b in score_result["breakdown"].values() if b["passed"])
    total_count = len(score_result["breakdown"])
    print(f"Components Passed: {passed_count}/{total_count}")

    print(f"\n{'Component':<25} {'Score':<12} {'Weight':<10} {'Status':<10}")
    print(f"{'-'*70}")

    for component, details in score_result["breakdown"].items():
        status = "✓ PASS" if details["passed"] else "✗ FAIL"
        weight_pct = details["weight"] * 100
        print(
            f"{component:<25} "
            f"{details['score']:.1f}/{details['max_score']:<5.1f}    "
            f"{weight_pct:>4.0f}%      "
            f"{status}"
        )

    print(f"{'='*70}\n")

    # Additional diagnostic info if test fails
    if score_result["total_score"] < scenario["scoring"]["pass_threshold"]:
        print("\nDiagnostic Information:")
        print("-" * 70)

        if investigation.analysis.decision:
            print("\nBase Decision (Label):")
            print(f"  {investigation.analysis.decision.label}")

        if investigation.analysis.hypotheses:
            print("\nTop Hypotheses:")
            for i, hyp in enumerate(investigation.analysis.hypotheses[:3], 1):
                print(f"  {i}. [{hyp.confidence_0_100}%] {hyp.title}")

        if investigation.analysis.rca:
            print("\nRCA Root Cause:")
            print(f"  {investigation.analysis.rca.root_cause or 'Not provided'}")

        print()

    # Assert against threshold
    assert score_result["total_score"] >= scenario["scoring"]["pass_threshold"], (
        f"RCA quality score {score_result['total_score']:.1f} is below "
        f"pass threshold {scenario['scoring']['pass_threshold']}"
    )


def test_no_scenarios_warning():
    """
    Warning test that fails if no scenarios are found.

    This ensures developers don't accidentally delete fixtures or
    misconfigure the fixtures directory.
    """
    scenarios = discover_scenarios()
    if not scenarios:
        pytest.skip("No scenario fixtures found in eval/fixtures/")
