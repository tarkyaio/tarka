"""Tests for placeholder resolver utility."""

from datetime import datetime

from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, LogsEvidence, TimeWindow
from agent.utils.placeholder_resolver import PlaceholderResolver


def test_resolve_service_account_name():
    """Test that 'unknown' is replaced with actual service account name."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "pod": "test-pod"},
        evidence=Evidence(
            k8s=K8sEvidence(
                pod_info={
                    "service_account_name": "app-service-account",
                }
            ),
            logs=LogsEvidence(logs=[]),
        ),
    )

    # Store SA name as enrichment would
    investigation.evidence.k8s.service_account_name = "app-service-account"

    resolver = PlaceholderResolver(investigation)

    # Test replacing "unknown"
    command = "kubectl get sa unknown -n dds"
    result = resolver.resolve(command)
    assert "app-service-account" in result
    assert "unknown" not in result


def test_resolve_bucket_name_from_logs():
    """Test extracting bucket name from S3 error logs."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "pod": "test-pod"},
        evidence=Evidence(
            k8s=K8sEvidence(),
            logs=LogsEvidence(
                logs=[
                    {"content": "ERROR: Access denied to bucket: my-test-bucket"},
                    {"content": "Other log line"},
                ]
            ),
        ),
    )

    resolver = PlaceholderResolver(investigation)

    # Test extracting bucket name
    assert resolver.values.get("bucket_name") == "my-test-bucket"

    # Test replacing "ERROR"
    command = "aws s3api head-bucket --bucket ERROR"
    result = resolver.resolve(command)
    assert "my-test-bucket" in result
    assert "ERROR" not in result or "ERROR:" in result  # Allow "ERROR:" prefix


def test_resolve_partial_placeholders():
    """Test that some placeholders remain when values not available."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "pod": "test-pod"},
        evidence=Evidence(k8s=K8sEvidence(), logs=LogsEvidence(logs=[])),
    )

    resolver = PlaceholderResolver(investigation)

    # Test that <ROLE_NAME> remains as-is (self-explanatory from context)
    command = "aws iam put-role-policy --role-name <ROLE_NAME>"
    result = resolver.resolve(command)
    assert "<ROLE_NAME>" in result
    # No multi-line notes (breaks markdown formatting)
    assert "\n" not in result


def test_resolve_namespace():
    """Test namespace replacement."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "pod": "test-pod"},
        evidence=Evidence(k8s=K8sEvidence(), logs=LogsEvidence(logs=[])),
    )

    resolver = PlaceholderResolver(investigation)

    assert resolver.values.get("namespace") == "production"


def test_no_replacement_when_values_missing():
    """Test that commands remain unchanged when no values available."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": None, "pod": None},
        evidence=Evidence(k8s=K8sEvidence(), logs=LogsEvidence(logs=[])),
    )

    resolver = PlaceholderResolver(investigation)

    command = "kubectl get sa unknown -n default"
    result = resolver.resolve(command)
    # Should remain unchanged (unknown is a common word in this context)
    assert "unknown" in result


def test_resolve_bucket_name_from_real_log_format():
    """Test extracting bucket name from real Job failure log format."""
    investigation = Investigation(
        alert=AlertInstance(fingerprint="test", labels={}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=datetime.now(), end_time=datetime.now()),
        target={"namespace": "production", "pod": "test-pod"},
        evidence=Evidence(
            k8s=K8sEvidence(),
            logs=LogsEvidence(
                logs=[
                    {
                        "content": "ERROR:root:<Processing>: Failed to get bucket region for example-bucket.example.com: An error occurred (403) when calling the HeadBucket operation: Forbidden"
                    },
                ]
            ),
        ),
    )

    resolver = PlaceholderResolver(investigation)

    # Test extracting bucket name from real log format
    assert resolver.values.get("bucket_name") == "example-bucket.example.com"

    # Test replacing "ERROR"
    command = "aws s3api head-bucket --bucket ERROR"
    result = resolver.resolve(command)
    assert "example-bucket.example.com" in result
    # "ERROR" should be replaced, but "ERROR:" in the log is fine
    assert result.count("ERROR") == 0  # No standalone "ERROR" word
