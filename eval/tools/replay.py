"""
Replay mechanism: load Investigation from fixture and run analysis.

Instead of mocking individual providers, we directly populate the Investigation
evidence from captured fixtures. This is simpler and more deterministic.
"""

import json
from pathlib import Path

from agent.core.models import Investigation
from agent.diagnostics.engine import run_diagnostics
from agent.pipeline.enrich import build_family_enrichment
from agent.pipeline.features import compute_features
from agent.pipeline.scoring import score_investigation
from agent.pipeline.verdict import build_base_decision


def load_investigation_from_fixture(fixture_dir: Path) -> Investigation:
    """
    Load Investigation from fixture (Pydantic deserialization).

    Args:
        fixture_dir: Path to fixture directory containing investigation.json

    Returns:
        Investigation object with all evidence pre-populated
    """
    investigation_path = fixture_dir / "investigation.json"
    if not investigation_path.exists():
        raise FileNotFoundError(f"Investigation fixture not found: {investigation_path}")

    with open(investigation_path) as f:
        investigation_data = json.load(f)

    investigation = Investigation.model_validate(investigation_data)

    # Mark as replay mode to prevent any live provider calls
    investigation.meta["replay_mode"] = True
    investigation.meta["fixture_source"] = str(fixture_dir)

    return investigation


def run_investigation_from_fixture(fixture_dir: Path, enable_llm: bool = False) -> Investigation:
    """
    Run investigation analysis from fixture.

    Skips evidence collection (already captured), runs:
    - Diagnostic modules (with do_collect=False to skip evidence gathering)
    - Feature computation
    - Scoring + verdict
    - Base decision building
    - Family enrichment
    - LLM RCA (if enabled)

    Args:
        fixture_dir: Path to fixture directory
        enable_llm: Whether to enable LLM enrichment

    Returns:
        Investigation with completed analysis
    """
    investigation = load_investigation_from_fixture(fixture_dir)

    # Compute features from evidence
    features = compute_features(investigation)
    investigation.analysis.features = features

    # Build base triage decision
    investigation.analysis.decision = build_base_decision(investigation)

    # Build family enrichment
    investigation.analysis.enrichment = build_family_enrichment(investigation)

    # Run diagnostics (skip evidence collection phase)
    run_diagnostics(investigation, do_collect=False)

    # Score investigation
    scores, verdict = score_investigation(investigation, features)
    investigation.analysis.scores = scores
    investigation.analysis.verdict = verdict

    # Optional LLM enrichment
    if enable_llm:
        try:
            from agent.llm.enrich_investigation import maybe_enrich_investigation

            maybe_enrich_investigation(investigation, enabled=True)
        except Exception as e:
            # LLM enrichment is optional, don't fail if it errors
            investigation.meta["llm_error"] = str(e)

    return investigation
