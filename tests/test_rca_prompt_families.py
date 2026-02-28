"""Tests for family-aware RCA prompt generation."""

from agent.graphs.rca import _build_planner_prompt, _get_family_specific_guidance


def test_get_family_specific_guidance_job_failed():
    """job_failed family should have AWS/IAM-specific guidance."""
    guidance = _get_family_specific_guidance("job_failed")

    # Should include Job/AWS-specific examples
    assert "S3 errors" in guidance
    assert "IAM errors" in guidance
    assert "ECR errors" in guidance
    assert "DB errors" in guidance

    # Should include permission boundary interpretation
    assert "Interpreting Permission Boundaries" in guidance
    assert "job's IAM role" in guidance
    assert "cross-account" in guidance
    assert "bucket policy" in guidance


def test_get_family_specific_guidance_cpu_throttling():
    """cpu_throttling family should have CPU-specific guidance."""
    guidance = _get_family_specific_guidance("cpu_throttling")

    # Should include CPU-specific guidance
    assert "CPU limits" in guidance or "cpu limits" in guidance.lower()
    assert "throttling" in guidance.lower()

    # Should NOT have AWS/IAM guidance
    assert "IAM" not in guidance
    assert "S3 errors" not in guidance
    assert "cross-account" not in guidance


def test_get_family_specific_guidance_oom_killed():
    """oom_killed family should have memory-specific guidance."""
    guidance = _get_family_specific_guidance("oom_killed")

    # Should include memory-specific guidance
    assert "memory" in guidance.lower()
    assert "OOM" in guidance or "oom" in guidance.lower()

    # Should NOT have AWS/IAM guidance
    assert "IAM" not in guidance
    assert "S3" not in guidance


def test_get_family_specific_guidance_http_5xx():
    """http_5xx family should have service-specific guidance."""
    guidance = _get_family_specific_guidance("http_5xx")

    # Should include HTTP/service-specific guidance
    assert "5xx" in guidance.lower() or "upstream" in guidance.lower()

    # Should NOT have AWS/IAM guidance
    assert "IAM" not in guidance
    assert "S3" not in guidance


def test_get_family_specific_guidance_pod_not_healthy():
    """pod_not_healthy family should have pod health-specific guidance."""
    guidance = _get_family_specific_guidance("pod_not_healthy")

    # Should include pod health guidance
    assert "readiness" in guidance.lower() or "liveness" in guidance.lower()
    assert "pod" in guidance.lower()

    # Should NOT have AWS/IAM guidance
    assert "IAM" not in guidance
    assert "S3" not in guidance


def test_get_family_specific_guidance_crashloop():
    """crashloop family should have crash-specific guidance."""
    guidance = _get_family_specific_guidance("crashloop")

    # Should include crash-specific guidance
    assert "crash" in guidance.lower()
    assert "exit code" in guidance.lower()

    # Should NOT have AWS/IAM guidance
    assert "IAM" not in guidance
    assert "S3" not in guidance


def test_get_family_specific_guidance_memory_pressure():
    """memory_pressure family should have memory pressure-specific guidance."""
    guidance = _get_family_specific_guidance("memory_pressure")

    # Should include memory pressure guidance
    assert "memory" in guidance.lower()
    assert "pressure" in guidance.lower()

    # Should NOT have AWS/IAM guidance
    assert "IAM" not in guidance
    assert "S3" not in guidance


def test_get_family_specific_guidance_generic():
    """Generic/unknown families should have generic guidance."""
    guidance = _get_family_specific_guidance("unknown_family")

    # Should include generic guidance
    assert "adapt to your specific alert" in guidance.lower()

    # Should NOT have AWS/IAM guidance
    assert "IAM" not in guidance
    assert "S3" not in guidance


def test_planner_prompt_uses_job_failed_family():
    """Planner prompt should include job_failed-specific guidance when family=job_failed."""
    analysis_json = {
        "target": {"target_type": "pod"},
        "analysis": {
            "verdict": {"family": "job_failed"},
            "hypotheses": [],
        },
    }
    prompt = _build_planner_prompt(analysis_json=analysis_json, tool_events=[], allowed_tools=["promql.instant"])

    # Should include Job/AWS guidance
    assert "S3 errors" in prompt
    assert "IAM errors" in prompt
    assert "Interpreting Permission Boundaries" in prompt


def test_planner_prompt_uses_cpu_throttling_family():
    """Planner prompt should include cpu_throttling-specific guidance when family=cpu_throttling."""
    analysis_json = {
        "target": {"target_type": "pod"},
        "analysis": {
            "verdict": {"family": "cpu_throttling"},
            "hypotheses": [],
        },
    }
    prompt = _build_planner_prompt(analysis_json=analysis_json, tool_events=[], allowed_tools=["promql.instant"])

    # Should include CPU guidance
    assert "CPU limits" in prompt or "cpu limits" in prompt.lower()
    assert "throttling" in prompt.lower()

    # Should NOT include AWS/IAM guidance
    assert "S3 errors" not in prompt
    assert "IAM errors" not in prompt
    assert "Interpreting Permission Boundaries" not in prompt


def test_planner_prompt_uses_oom_killed_family():
    """Planner prompt should include oom_killed-specific guidance when family=oom_killed."""
    analysis_json = {
        "target": {"target_type": "pod"},
        "analysis": {
            "verdict": {"family": "oom_killed"},
            "hypotheses": [],
        },
    }
    prompt = _build_planner_prompt(analysis_json=analysis_json, tool_events=[], allowed_tools=["promql.instant"])

    # Should include memory guidance
    assert "memory" in prompt.lower()

    # Should NOT include AWS/IAM guidance
    assert "S3 errors" not in prompt
    assert "IAM errors" not in prompt


def test_planner_prompt_uses_generic_family():
    """Planner prompt should include generic guidance when family is unknown."""
    analysis_json = {
        "target": {"target_type": "pod"},
        "analysis": {
            "verdict": {"family": "unknown_family"},
            "hypotheses": [],
        },
    }
    prompt = _build_planner_prompt(analysis_json=analysis_json, tool_events=[], allowed_tools=["promql.instant"])

    # Should include generic guidance
    assert "adapt to your specific alert" in prompt.lower()

    # Should NOT include AWS/IAM guidance
    assert "S3 errors" not in prompt
    assert "IAM errors" not in prompt


def test_planner_prompt_handles_missing_family():
    """Planner prompt should use generic guidance when family is missing."""
    analysis_json = {
        "target": {"target_type": "pod"},
        "analysis": {
            "verdict": {},  # No family field
            "hypotheses": [],
        },
    }
    prompt = _build_planner_prompt(analysis_json=analysis_json, tool_events=[], allowed_tools=["promql.instant"])

    # Should include generic guidance (fallback)
    assert "adapt to your specific alert" in prompt.lower()

    # Should NOT include AWS/IAM guidance
    assert "S3 errors" not in prompt
    assert "IAM errors" not in prompt


def test_planner_prompt_includes_base_sections():
    """All prompts should include base sections regardless of family."""
    analysis_json = {
        "target": {"target_type": "pod"},
        "analysis": {
            "verdict": {"family": "cpu_throttling"},
            "hypotheses": [],
        },
    }
    prompt = _build_planner_prompt(analysis_json=analysis_json, tool_events=[], allowed_tools=["promql.instant"])

    # Should include base sections
    assert "You are Tarka, an on-call incident investigation agent" in prompt
    assert "Goal:" in prompt
    assert "Tool Usage" in prompt
    assert "Hypothesis Verification (CRITICAL):" in prompt
    assert "Hard constraints" in prompt
    assert "Output JSON schema" in prompt
