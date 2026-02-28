"""Unit tests for Job failure diagnostic module."""

from datetime import datetime, timezone

from agent.core.models import Evidence, Investigation, K8sEvidence, LogsEvidence, TargetRef, TimeWindow
from agent.diagnostics.job_diagnostics import JobFailureDiagnosticModule
from agent.diagnostics.log_pattern_matcher import LogPattern, LogPatternMatcher


def create_test_investigation(**kwargs) -> Investigation:
    """Helper to create Investigation with required fields."""
    now = datetime.now(timezone.utc)
    defaults = {
        "alert": {"labels": {"alertname": "TestAlert"}},
        "time_window": TimeWindow(
            window="1h",
            start_time=now,
            end_time=now,
        ),
        "target": TargetRef(namespace="test-ns", pod="test-pod"),
        "evidence": Evidence(),
    }
    defaults.update(kwargs)
    return Investigation(**defaults)


class TestLogPatternMatcher:
    """Test generic log pattern matching framework."""

    def test_s3_access_denied_pattern_matches(self):
        """Test that S3 403 errors are detected."""
        pattern = LogPattern(
            pattern_id="s3_test",
            title="S3 test",
            patterns=[r"403.*(?:s3|bucket)", r"Forbidden.*HeadBucket"],
            confidence=90,
            why_template="S3 error",
            next_tests=[],
            context_extractors={"bucket": r"bucket[:\s]+([a-z0-9.-]+)"},
        )

        matcher = LogPatternMatcher([pattern])

        parsed_errors = [
            {
                "message": "botocore.exceptions.ClientError: An error occurred (403) when calling the HeadBucket operation: Forbidden"
            }
        ]

        matches = matcher.find_matches(parsed_errors)

        assert len(matches) == 1
        matched_pattern, context = matches[0]
        assert matched_pattern.pattern_id == "s3_test"

    def test_context_extraction(self):
        """Test extraction of bucket names from log errors."""
        pattern = LogPattern(
            pattern_id="s3_test",
            title="S3 test",
            patterns=[r"Failed to get bucket region"],
            confidence=90,
            why_template="S3 error",
            next_tests=[],
            context_extractors={
                "bucket": r"for ([a-z0-9.-]+):",  # Match "for my-bucket:"
                "operation": r"(HeadBucket|GetObject)",
            },
        )

        matcher = LogPatternMatcher([pattern])

        parsed_errors = [
            {
                "message": "Failed to get bucket region for my-bucket: An error occurred (403) when calling the HeadBucket operation"
            }
        ]

        matches = matcher.find_matches(parsed_errors)

        assert len(matches) == 1
        _, context = matches[0]
        assert context["bucket"] == "my-bucket"
        assert context["operation"] == "HeadBucket"

    def test_multiple_patterns(self):
        """Test matching against multiple patterns."""
        pattern1 = LogPattern(
            pattern_id="s3_403",
            title="S3 access denied",
            patterns=[r"403.*s3"],
            confidence=90,
            why_template="",
            next_tests=[],
            context_extractors={},
        )

        pattern2 = LogPattern(
            pattern_id="s3_404",
            title="S3 not found",
            patterns=[r"404.*bucket"],
            confidence=95,
            why_template="",
            next_tests=[],
            context_extractors={},
        )

        matcher = LogPatternMatcher([pattern1, pattern2])

        # Should match both patterns
        parsed_errors = [
            {"message": "Error 403 when accessing s3"},
            {"message": "Error 404: bucket not found"},
        ]

        matches = matcher.find_matches(parsed_errors)

        assert len(matches) == 2
        pattern_ids = [m[0].pattern_id for m in matches]
        assert "s3_403" in pattern_ids
        assert "s3_404" in pattern_ids

    def test_no_match(self):
        """Test that non-matching errors return no matches."""
        pattern = LogPattern(
            pattern_id="s3_test",
            title="S3 test",
            patterns=[r"s3.*403"],
            confidence=90,
            why_template="",
            next_tests=[],
            context_extractors={},
        )

        matcher = LogPatternMatcher([pattern])

        parsed_errors = [{"message": "Something completely different"}]

        matches = matcher.find_matches(parsed_errors)

        assert len(matches) == 0


class TestJobFailureDiagnosticModule:
    """Test Job failure diagnostic module."""

    def test_applies_to_job_workload(self):
        """Test that module applies to Job workload_kind."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(
            target=TargetRef(
                namespace="test-ns",
                pod="test-pod",
                workload_kind="Job",
                workload_name="test-job",
            )
        )

        assert module.applies(investigation)

    def test_applies_to_job_failed_family(self):
        """Test that module applies to job_failed family."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(meta={"family": "job_failed"})

        assert module.applies(investigation)

    def test_does_not_apply_to_other_families(self):
        """Test that module does not apply to non-Job families."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(
            target=TargetRef(
                namespace="test-ns",
                pod="test-pod",
                workload_kind="Deployment",
                workload_name="test-deploy",
            )
        )

        assert not module.applies(investigation)

    def test_diagnose_with_s3_errors(self):
        """Test hypothesis generation from S3 access denied errors."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(
            target=TargetRef(
                namespace="test-ns",
                pod="test-pod",
                workload_kind="Job",
                workload_name="test-job",
            ),
            evidence=Evidence(
                logs=LogsEvidence(
                    parsed_errors=[
                        {
                            "message": "Failed to get bucket region for my-bucket: An error occurred (403) when calling the HeadBucket operation: Forbidden",
                            "severity": "ERROR",
                        }
                    ]
                ),
                k8s=K8sEvidence(pod_info={"service_account": "test-sa"}),
            ),
        )

        hypotheses = module.diagnose(investigation)

        # Should generate at least one S3-related hypothesis
        assert len(hypotheses) > 0

        # Check that hypothesis mentions S3/access denied
        s3_hypothesis = next((h for h in hypotheses if "s3" in h.hypothesis_id.lower()), None)
        assert s3_hypothesis is not None
        assert s3_hypothesis.confidence_0_100 >= 80

        # Check that next_tests include AWS CLI commands
        next_tests_str = " ".join(s3_hypothesis.next_tests)
        assert "aws" in next_tests_str.lower()

    def test_diagnose_with_no_logs(self):
        """Test that module returns empty list when no logs available."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(
            target=TargetRef(
                namespace="test-ns",
                pod="test-pod",
                workload_kind="Job",
                workload_name="test-job",
            ),
            evidence=Evidence(logs=LogsEvidence(parsed_errors=[])),  # Empty parsed_errors
        )

        hypotheses = module.diagnose(investigation)

        assert len(hypotheses) == 0

    def test_diagnose_with_no_matching_patterns(self):
        """Test that module returns empty list when no patterns match."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(
            target=TargetRef(
                namespace="test-ns",
                pod="test-pod",
                workload_kind="Job",
                workload_name="test-job",
            ),
            evidence=Evidence(
                logs=LogsEvidence(
                    parsed_errors=[
                        {
                            "message": "Some generic error that doesn't match any pattern",
                            "severity": "ERROR",
                        }
                    ]
                )
            ),
        )

        hypotheses = module.diagnose(investigation)

        # Should return empty list when no patterns match
        assert len(hypotheses) == 0

    def test_context_building(self):
        """Test that context is built correctly from investigation."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(
            target=TargetRef(namespace="my-ns", pod="my-pod"),
            evidence=Evidence(
                logs=LogsEvidence(
                    parsed_errors=[
                        {
                            "message": "Failed to get bucket region for test-bucket: 403 Forbidden",
                            "severity": "ERROR",
                        }
                    ]
                ),
                k8s=K8sEvidence(pod_info={"service_account": "my-sa"}),
            ),
        )

        hypotheses = module.diagnose(investigation)

        if hypotheses:
            # Check that context fields are populated in next_tests
            hypothesis = hypotheses[0]
            next_tests_str = " ".join(hypothesis.next_tests)

            # Should include namespace and service account
            assert "my-ns" in next_tests_str or "namespace" in next_tests_str.lower()
            assert "my-sa" in next_tests_str or "sa" in next_tests_str.lower()

    def test_multiple_error_patterns(self):
        """Test that multiple matching patterns generate multiple hypotheses."""
        module = JobFailureDiagnosticModule()

        investigation = create_test_investigation(
            target=TargetRef(
                namespace="test-ns",
                pod="test-pod",
                workload_kind="Job",
                workload_name="test-job",
            ),
            evidence=Evidence(
                logs=LogsEvidence(
                    parsed_errors=[
                        {
                            "message": "Failed to get bucket region for my-bucket: 403 Forbidden",
                            "severity": "ERROR",
                        },
                        {
                            "message": "Unable to locate credentials",
                            "severity": "ERROR",
                        },
                    ]
                )
            ),
        )

        hypotheses = module.diagnose(investigation)

        # Should generate multiple hypotheses for different patterns
        # (S3 access denied + credentials error)
        assert len(hypotheses) >= 2


class TestS3Patterns:
    """Test S3-specific patterns."""

    def test_s3_access_denied_pattern(self):
        """Test S3 403 pattern matches various error formats."""
        from agent.diagnostics.patterns.s3_patterns import S3_ACCESS_DENIED

        test_cases = [
            "403 error when accessing s3 bucket",
            "Access Denied when calling HeadBucket",
            "botocore.exceptions.ClientError: 403 HeadBucket",
            "Failed to get bucket region: 403",
        ]

        for log_text in test_cases:
            assert S3_ACCESS_DENIED.matches(log_text), f"Pattern should match: {log_text}"

    def test_s3_bucket_not_found_pattern(self):
        """Test S3 404 pattern matches."""
        from agent.diagnostics.patterns.s3_patterns import S3_BUCKET_NOT_FOUND

        test_cases = [
            "404 NoSuchBucket error",
            "The specified bucket does not exist",
            "botocore.exceptions.ClientError: NoSuchBucket",
        ]

        for log_text in test_cases:
            assert S3_BUCKET_NOT_FOUND.matches(log_text), f"Pattern should match: {log_text}"

    def test_s3_credentials_error_pattern(self):
        """Test AWS credentials error pattern matches."""
        from agent.diagnostics.patterns.s3_patterns import S3_CREDENTIALS_ERROR

        test_cases = [
            "Unable to locate credentials",
            "No credentials found",
            "botocore.exceptions.NoCredentialsError",
            "Unable to locate AWS credentials",
        ]

        for log_text in test_cases:
            assert S3_CREDENTIALS_ERROR.matches(log_text), f"Pattern should match: {log_text}"

    def test_bucket_name_extraction(self):
        """Test bucket name extraction from various formats."""
        from agent.diagnostics.patterns.s3_patterns import S3_ACCESS_DENIED

        test_cases = [
            ("bucket: my-bucket-name", "my-bucket-name"),
            ("bucket my-bucket.with.dots", "my-bucket.with.dots"),
            ("for bucket test-bucket-123: error", "test-bucket-123"),
            (
                "ERROR:root:Failed to get bucket region for example-bucket.example.com: "
                "An error occurred (403) when calling the HeadBucket operation: Forbidden",
                "example-bucket.example.com",
            ),
        ]

        for log_text, expected_bucket in test_cases:
            context = S3_ACCESS_DENIED.extract_context(log_text)
            assert context.get("bucket") == expected_bucket, f"Should extract bucket name from: {log_text}"
