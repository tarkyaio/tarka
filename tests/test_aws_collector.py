"""
Unit tests for AWS evidence collector and metadata extraction.
"""

from __future__ import annotations

import pytest

from agent.collectors.aws_context import collect_aws_evidence, extract_aws_metadata_from_investigation
from agent.core.models import AlertInstance, Evidence, Investigation, K8sEvidence, TargetRef, TimeWindow


def _make_investigation(**overrides) -> Investigation:
    """Helper to create test investigations."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    return Investigation(
        alert=AlertInstance(fingerprint="test", labels=overrides.get("labels", {}), annotations={}),
        time_window=TimeWindow(window="1h", start_time=now - timedelta(hours=1), end_time=now),
        target=TargetRef(namespace="default", **overrides.get("target", {})),
        evidence=Evidence(k8s=K8sEvidence(**overrides.get("k8s_evidence", {}))),
    )


def test_extract_ec2_instance_from_alert_labels():
    """Extract EC2 instance ID from alert labels."""
    investigation = _make_investigation(labels={"instance_id": "i-abc123def456", "aws_region": "us-west-2"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert metadata["region"] == "us-west-2"
    assert "i-abc123def456" in metadata["ec2_instances"]


def test_extract_ebs_volume_from_alert_labels():
    """Extract EBS volume ID from alert labels."""
    investigation = _make_investigation(labels={"volume_id": "vol-xyz789"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "vol-xyz789" in metadata["ebs_volumes"]


def test_extract_elb_from_alert_labels():
    """Extract ELB name from alert labels."""
    investigation = _make_investigation(labels={"load_balancer": "my-classic-lb"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "my-classic-lb" in metadata["elb_names"]


def test_extract_elbv2_target_group_from_alert_labels():
    """Extract ELBv2 target group ARN from alert labels."""
    investigation = _make_investigation(
        labels={"target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/my-tg/abc"}
    )

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/my-tg/abc" in metadata["elbv2_target_groups"]


def test_extract_rds_instance_from_alert_labels():
    """Extract RDS instance ID from alert labels."""
    investigation = _make_investigation(labels={"db_instance_id": "my-database"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "my-database" in metadata["rds_instances"]


def test_extract_security_group_from_alert_labels():
    """Extract security group ID from alert labels."""
    investigation = _make_investigation(labels={"security_group_id": "sg-abc123"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "sg-abc123" in metadata["security_groups"]


def test_extract_nat_gateway_from_alert_labels():
    """Extract NAT gateway ID from alert labels."""
    investigation = _make_investigation(labels={"nat_gateway_id": "nat-xyz789"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "nat-xyz789" in metadata["nat_gateways"]


def test_extract_vpc_endpoint_from_alert_labels():
    """Extract VPC endpoint ID from alert labels."""
    investigation = _make_investigation(labels={"vpc_endpoint_id": "vpce-abc123"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "vpce-abc123" in metadata["vpc_endpoints"]


def test_extract_ec2_instance_from_node_name():
    """Extract EC2 instance ID from K8s node name."""
    investigation = _make_investigation(
        k8s_evidence={
            "pod_info": {
                "node_name": "i-nodeid123",
            }
        }
    )

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert "i-nodeid123" in metadata["ec2_instances"]


def test_extract_ecr_repository_from_container_image():
    """Extract ECR repository and tag from container image."""
    investigation = _make_investigation(
        k8s_evidence={
            "pod_info": {
                "containers": [{"name": "app", "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-app:v1.2.3"}]
            }
        }
    )

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert len(metadata["ecr_repositories"]) == 1
    assert metadata["ecr_repositories"][0]["repository"] == "my-app"
    assert metadata["ecr_repositories"][0]["tag"] == "v1.2.3"
    assert metadata["ecr_repositories"][0]["region"] == "us-east-1"


def test_deduplicates_resource_ids():
    """Metadata extraction deduplicates resource IDs."""
    investigation = _make_investigation(
        labels={"instance_id": "i-abc123", "instance": "i-abc123"},  # Same instance ID twice
    )

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert metadata["ec2_instances"] == ["i-abc123"]  # Deduplicated


def test_default_region_from_env(monkeypatch):
    """Default region comes from AWS_REGION env var."""
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    investigation = _make_investigation(labels={})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert metadata["region"] == "eu-west-1"


def test_region_from_alert_overrides_env(monkeypatch):
    """Region from alert labels overrides env var."""
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    investigation = _make_investigation(labels={"aws_region": "us-east-1"})

    metadata = extract_aws_metadata_from_investigation(investigation)

    assert metadata["region"] == "us-east-1"


class _MockAwsProvider:
    """Mock AWS provider for testing."""

    def __init__(self):
        self.calls = []

    def get_ec2_instance_status(self, instance_id, region):
        self.calls.append(("ec2", instance_id, region))
        return {"instance_id": instance_id, "state": "running"}

    def get_ebs_volume_health(self, volume_id, region):
        self.calls.append(("ebs", volume_id, region))
        return {"volume_id": volume_id, "status": "ok"}

    def get_elb_target_health(self, lb_name, region):
        self.calls.append(("elb", lb_name, region))
        return {"load_balancer_name": lb_name, "instance_states": []}

    def get_elbv2_target_health(self, tg_arn, region):
        self.calls.append(("elbv2", tg_arn, region))
        return {"target_group_arn": tg_arn, "target_health_descriptions": []}

    def get_rds_instance_status(self, db_id, region):
        self.calls.append(("rds", db_id, region))
        return {"db_instance_id": db_id, "status": "available"}

    def get_ecr_image_scan_findings(self, repo, tag, region):
        self.calls.append(("ecr", repo, tag, region))
        return {"repository": repo, "image_tag": tag, "findings_summary": {}}

    def get_ecr_repository_policy(self, repo, region):
        self.calls.append(("ecr_policy", repo, region))
        return {"repository": repo, "policy_text": None}

    def get_security_group_rules(self, sg_id, region):
        self.calls.append(("sg", sg_id, region))
        return {"security_group_id": sg_id, "ingress_rules": []}

    def get_nat_gateway_status(self, nat_id, region):
        self.calls.append(("nat", nat_id, region))
        return {"nat_gateway_id": nat_id, "state": "available"}

    def get_vpc_endpoint_status(self, vpce_id, region):
        self.calls.append(("vpce", vpce_id, region))
        return {"vpc_endpoint_id": vpce_id, "state": "available"}


@pytest.fixture
def mock_aws_provider(monkeypatch):
    """Mock AWS provider."""
    provider = _MockAwsProvider()

    def _fake_get_aws_provider():
        return provider

    monkeypatch.setattr("agent.collectors.aws_context.get_aws_provider", _fake_get_aws_provider)

    return provider


def test_collect_aws_evidence_calls_provider(mock_aws_provider):
    """AWS evidence collection calls provider for each discovered resource."""
    investigation = _make_investigation(
        labels={"instance_id": "i-abc123", "volume_id": "vol-xyz789", "aws_region": "us-east-1"}
    )

    result = collect_aws_evidence(investigation)

    assert "i-abc123" in result["ec2_instances"]
    assert "vol-xyz789" in result["ebs_volumes"]
    assert result["metadata"]["region"] == "us-east-1"
    assert len(mock_aws_provider.calls) == 2  # EC2 + EBS


def test_collect_aws_evidence_continues_on_error(mock_aws_provider, monkeypatch):
    """AWS evidence collection continues even if some resources fail."""

    def _failing_ec2(instance_id, region):
        raise Exception("EC2 API error")

    monkeypatch.setattr(mock_aws_provider, "get_ec2_instance_status", _failing_ec2)

    investigation = _make_investigation(
        labels={"instance_id": "i-abc123", "volume_id": "vol-xyz789", "aws_region": "us-east-1"}
    )

    result = collect_aws_evidence(investigation)

    # EBS should still be collected despite EC2 failure
    assert "vol-xyz789" in result["ebs_volumes"]
    # Error should be recorded
    assert any("ec2:" in err for err in result["errors"])


def test_collect_aws_evidence_returns_empty_when_no_resources():
    """AWS evidence collection returns empty dicts when no resources found."""
    investigation = _make_investigation(labels={})

    result = collect_aws_evidence(investigation)

    assert result["ec2_instances"] == {}
    assert result["ebs_volumes"] == {}
    assert result["elb_health"] == {}
    assert result["rds_instances"] == {}
    assert result["errors"] == []


def test_collect_aws_evidence_handles_ecr_images(mock_aws_provider):
    """AWS evidence collection handles ECR image scan findings."""
    investigation = _make_investigation(
        k8s_evidence={
            "pod_info": {
                "containers": [{"name": "app", "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-app:v1.0.0"}]
            }
        }
    )

    result = collect_aws_evidence(investigation)

    assert "my-app:v1.0.0" in result["ecr_images"]
    assert any("ecr" in call[0] for call in mock_aws_provider.calls)
