"""RCA quality scoring engine."""

from typing import Any, Dict

from agent.core.models import Investigation
from eval.scoring.matchers import match_patterns


def score_rca_quality(
    investigation: Investigation, expected_outcomes: Dict[str, Any], scoring_weights: Dict[str, float]
) -> Dict[str, Any]:
    """
    Score RCA quality against expected outcomes.

    Args:
        investigation: Completed investigation with analysis
        expected_outcomes: Expected patterns and requirements from scenario.yaml
        scoring_weights: Weight for each scoring component

    Returns:
        Dictionary with:
            - total_score: float (0-100)
            - breakdown: dict with per-component scores
    """
    breakdown = {}

    # 1. Root cause identification (40% by default)
    root_cause_score = score_root_cause(investigation, expected_outcomes.get("root_cause", {}))
    breakdown["root_cause"] = {
        "score": root_cause_score,
        "max_score": 100,
        "weight": scoring_weights.get("root_cause_weight", 0.4),
        "passed": root_cause_score >= 70,
    }

    # 2. Fix accuracy (30% by default)
    fix_score = score_proposed_fix(investigation, expected_outcomes.get("proposed_fix", {}))
    breakdown["fix_accuracy"] = {
        "score": fix_score,
        "max_score": 100,
        "weight": scoring_weights.get("fix_accuracy_weight", 0.3),
        "passed": fix_score >= 60,
    }

    # 3. Hypothesis quality (20% by default)
    hypothesis_score = score_hypotheses(investigation, expected_outcomes.get("hypotheses", {}))
    breakdown["hypothesis_quality"] = {
        "score": hypothesis_score,
        "max_score": 100,
        "weight": scoring_weights.get("hypothesis_quality_weight", 0.2),
        "passed": hypothesis_score >= 50,
    }

    # 4. Next steps (10% by default)
    next_steps_score = score_next_steps(investigation, expected_outcomes.get("next_steps", {}))
    breakdown["next_steps"] = {
        "score": next_steps_score,
        "max_score": 100,
        "weight": scoring_weights.get("next_steps_weight", 0.1),
        "passed": next_steps_score >= 50,
    }

    # Weighted total
    total_score = sum(b["score"] * b["weight"] for b in breakdown.values())

    return {"total_score": total_score, "breakdown": breakdown}


def score_root_cause(investigation: Investigation, expected: Dict[str, Any]) -> float:
    """
    Score root cause identification.

    Checks multiple locations in order of priority:
    1. RCA root_cause field (100 points)
    2. Base decision why bullets (90 points)
    3. High-confidence hypotheses (80 points)
    """
    if not expected:
        return 100.0

    patterns = expected.get("patterns", [])
    match_type = expected.get("match_type", "substring")

    # Check RCA root_cause field
    if investigation.analysis.rca and investigation.analysis.rca.root_cause:
        if match_patterns(investigation.analysis.rca.root_cause, patterns, match_type):
            return 100.0

    # Check base decision why
    if investigation.analysis.decision:
        decision_text = "\n".join(investigation.analysis.decision.why)
        if match_patterns(decision_text, patterns, match_type):
            return 90.0

    # Check high-confidence hypotheses
    for hyp in investigation.analysis.hypotheses:
        if hyp.confidence_0_100 >= 80:
            hyp_text = f"{hyp.title}\n{'\n'.join(hyp.why)}"
            if match_patterns(hyp_text, patterns, match_type):
                return 80.0

    return 0.0


def score_proposed_fix(investigation: Investigation, expected: Dict[str, Any]) -> float:
    """
    Score proposed fix quality.

    Checks for presence of required fix elements (all_of)
    and optional fix elements (any_of).
    """
    if not expected:
        return 100.0

    # Collect fix-related content from multiple sources
    fixes = []
    if investigation.analysis.rca and investigation.analysis.rca.remediation:
        fixes.extend(investigation.analysis.rca.remediation)
    if investigation.analysis.decision:
        fixes.extend(investigation.analysis.decision.next)
    if investigation.analysis.verdict:
        fixes.extend(investigation.analysis.verdict.next_steps)

    fix_text = "\n".join(fixes)

    # Check all_of requirements (must have ALL)
    if "all_of" in expected:
        all_met = True
        for req in expected["all_of"]:
            if not match_patterns(fix_text, req["patterns"], req["match_type"]):
                all_met = False
                break
        if not all_met:
            return 30.0  # Partial credit for having some fix content

    # Check any_of requirements (must have AT LEAST ONE)
    if "any_of" in expected:
        any_met = False
        for req in expected["any_of"]:
            if match_patterns(fix_text, req["patterns"], req["match_type"]):
                any_met = True
                break
        if not any_met:
            return 50.0  # Partial credit

    return 100.0


def score_hypotheses(investigation: Investigation, expected: Dict[str, Any]) -> float:
    """
    Score hypothesis quality.

    Checks if hypotheses mention expected failure modes or patterns.
    """
    if not expected:
        return 100.0

    any_patterns = expected.get("any_of", [])
    if not any_patterns:
        return 100.0

    # Check if hypotheses mention expected patterns
    for hyp in investigation.analysis.hypotheses:
        hyp_text = f"{hyp.title}\n{'\n'.join(hyp.why)}"
        for pattern in any_patterns:
            if pattern.lower() in hyp_text.lower():
                return 100.0

    return 40.0  # Partial credit for having hypotheses


def score_next_steps(investigation: Investigation, expected: Dict[str, Any]) -> float:
    """
    Score next steps quality.

    Checks for actionable commands and expected command types.
    """
    if not expected:
        return 100.0

    # Collect next steps from both decision.next and verdict.next_steps
    next_steps = []
    if investigation.analysis.decision:
        next_steps.extend(investigation.analysis.decision.next)
    if investigation.analysis.verdict:
        next_steps.extend(investigation.analysis.verdict.next_steps)

    steps_text = "\n".join(next_steps)

    # Check for expected command types (kubectl, aws, etc.)
    command_types = expected.get("command_types", [])
    if command_types:
        found = sum(1 for cmd in command_types if cmd in steps_text)
        return (found / len(command_types)) * 100

    # Check for must-include patterns
    must_include = expected.get("must_include", [])
    if must_include:
        for pattern in must_include:
            if pattern.lower() not in steps_text.lower():
                return 50.0

    return 100.0
