import agent.api.webhook as ws


def test_run_view_fields_strict_from_analysis_json() -> None:
    aj = {
        "analysis": {
            "verdict": {
                "severity": "warning",
                "classification": "actionable",
                "primary_driver": "oom_killed",
                "one_liner": "x",
            },
            "scores": {"impact_score": 90, "confidence_score": 75, "noise_score": 10},
            "features": {"family": "oom_killed"},
        },
        "target": {"team": "apps"},
    }
    out = ws._run_view_fields_from_analysis_json(aj)
    assert out["severity"] == "warning"
    assert out["classification"] == "actionable"
    assert out["primary_driver"] == "oom_killed"
    assert out["one_liner"] == "x"
    assert out["impact_score"] == 90
    assert out["confidence_score"] == 75
    assert out["noise_score"] == 10
    assert out["team"] == "apps"
    assert out["family"] == "oom_killed"


def test_run_view_fields_missing_are_null() -> None:
    out = ws._run_view_fields_from_analysis_json({})
    assert out["severity"] is None
    assert out["classification"] is None
    assert out["primary_driver"] is None
    assert out["one_liner"] is None
    assert out["impact_score"] is None
    assert out["confidence_score"] is None
    assert out["noise_score"] is None
    assert out["team"] is None
    assert out["family"] is None


def test_coalesce_severity_is_strict() -> None:
    # Back-compat shim now reads only analysis_json; raw value is ignored.
    assert ws._coalesce_severity("critical", {"analysis": {"verdict": {}}}) is None
