"""
Unit tests for AWS chat tools with policy enforcement.
"""

from __future__ import annotations

import pytest

from agent.authz.policy import ChatPolicy
from agent.chat.tools import run_tool


class _MockAwsProvider:
    """Mock AWS provider for testing."""

    def get_ec2_instance_status(self, instance_id, region):
        return {
            "instance_id": instance_id,
            "instance_state": "running",
            "system_status": "ok",
            "instance_status": "ok",
        }

    def get_ebs_volume_health(self, volume_id, region):
        return {"volume_id": volume_id, "volume_status": "ok", "actions": []}

    def get_elb_target_health(self, lb_name, region):
        return {"load_balancer_name": lb_name, "instance_states": [{"InstanceId": "i-123", "State": "InService"}]}

    def get_elbv2_target_health(self, tg_arn, region):
        return {"target_group_arn": tg_arn, "target_health_descriptions": [{"TargetHealth": {"State": "healthy"}}]}

    def get_rds_instance_status(self, db_id, region):
        return {"db_instance_id": db_id, "db_instance_status": "available"}

    def get_ecr_image_scan_findings(self, repo, tag, region):
        return {"repository": repo, "image_tag": tag, "scan_status": "complete", "findings_summary": {}}

    def get_security_group_rules(self, sg_id, region):
        return {"security_group_id": sg_id, "ingress_rules": [], "egress_rules": []}

    def get_nat_gateway_status(self, nat_id, region):
        return {"nat_gateway_id": nat_id, "state": "available"}

    def get_vpc_endpoint_status(self, vpce_id, region):
        return {"vpc_endpoint_id": vpce_id, "state": "available"}

    def lookup_cloudtrail_events(self, region, start_time, end_time, resource_ids, max_results):
        return [
            {
                "EventName": "RunInstances",
                "EventTime": "2024-01-01T12:00:00Z",
                "Username": "admin",
                "EventId": "evt-123",
                "Resources": [{"ResourceType": "AWS::EC2::Instance", "ResourceName": "i-abc123"}],
            }
        ]

    def get_s3_bucket_location(self, bucket):
        # Mock responses for different test scenarios
        if bucket == "test-bucket-useast1":
            return {"bucket": bucket, "location": "us-east-1", "exists": True, "accessible": True, "error": None}
        elif bucket == "test-bucket-uswest2":
            return {"bucket": bucket, "location": "us-west-2", "exists": True, "accessible": True, "error": None}
        elif bucket == "nonexistent-bucket":
            return {
                "bucket": bucket,
                "exists": False,
                "accessible": False,
                "location": None,
                "error": "bucket_not_found",
            }
        elif bucket == "forbidden-bucket":
            return {
                "bucket": bucket,
                "exists": "unknown",
                "accessible": False,
                "location": None,
                "error": "agent_lacks_permission",
            }
        else:
            return {"bucket": bucket, "location": "us-east-1", "exists": True, "accessible": True, "error": None}

    def get_iam_role_permissions(self, role_name):
        # Mock responses for different test scenarios
        if role_name == "test-role-with-s3":
            return {
                "role_name": role_name,
                "role_arn": f"arn:aws:iam::123456789012:role/{role_name}",
                "attached_policies": [
                    {
                        "policy_name": "S3FullAccess",
                        "policy_arn": "arn:aws:iam::aws:policy/AmazonS3FullAccess",
                        "permissions_by_service": {
                            "s3": ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
                        },
                    }
                ],
                "inline_policies": [],
                "error": None,
            }
        elif role_name == "test-role-no-s3":
            return {
                "role_name": role_name,
                "role_arn": f"arn:aws:iam::123456789012:role/{role_name}",
                "attached_policies": [
                    {
                        "policy_name": "EC2ReadOnly",
                        "policy_arn": "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess",
                        "permissions_by_service": {"ec2": ["ec2:Describe*"]},
                    }
                ],
                "inline_policies": [],
                "error": None,
            }
        elif role_name == "nonexistent-role":
            return {"role_name": role_name, "error": "role_not_found"}
        else:
            return {
                "role_name": role_name,
                "role_arn": f"arn:aws:iam::123456789012:role/{role_name}",
                "attached_policies": [],
                "inline_policies": [],
                "error": None,
            }


@pytest.fixture
def mock_aws_provider(monkeypatch):
    """Mock AWS provider."""
    provider = _MockAwsProvider()

    def _fake_get_aws_provider():
        return provider

    monkeypatch.setattr("agent.providers.aws_provider.get_aws_provider", _fake_get_aws_provider)
    return provider


def test_aws_ec2_status_requires_policy(mock_aws_provider):
    """EC2 status tool requires allow_aws_read policy."""
    policy = ChatPolicy(allow_aws_read=False)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ec2_status",
        args={"instance_id": "i-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert not result.ok
    assert result.error == "tool_not_allowed"


def test_aws_ec2_status_explicit_params(mock_aws_provider):
    """EC2 status with explicit parameters."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ec2_status",
        args={"instance_id": "i-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["instance_id"] == "i-abc123"
    assert result.result["instance_state"] == "running"


def test_aws_ec2_status_auto_discovery(mock_aws_provider):
    """EC2 status auto-discovers from investigation metadata."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {"ec2_instances": ["i-auto123"], "region": "us-west-2"}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ec2_status",
        args={},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["instance_id"] == "i-auto123"


def test_aws_ec2_status_region_allowlist(mock_aws_provider):
    """EC2 status respects region allowlist."""
    policy = ChatPolicy(allow_aws_read=True, aws_region_allowlist={"us-east-1", "us-west-2"})
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    # Allowed region
    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ec2_status",
        args={"instance_id": "i-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )
    assert result.ok

    # Blocked region
    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ec2_status",
        args={"instance_id": "i-abc123", "region": "eu-west-1"},
        analysis_json=analysis,
    )
    assert not result.ok
    assert "region_not_allowed" in result.error


def test_aws_ebs_health_explicit_params(mock_aws_provider):
    """EBS health with explicit parameters."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ebs_health",
        args={"volume_id": "vol-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["volume_id"] == "vol-abc123"
    assert result.result["volume_status"] == "ok"


def test_aws_ebs_health_auto_discovery(mock_aws_provider):
    """EBS health auto-discovers from metadata."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {"ebs_volumes": ["vol-auto123"], "region": "us-east-1"}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ebs_health",
        args={},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["volume_id"] == "vol-auto123"


def test_aws_elb_health_classic_lb(mock_aws_provider):
    """ELB health for Classic Load Balancer."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.elb_health",
        args={"load_balancer": "my-lb", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["load_balancer_name"] == "my-lb"


def test_aws_elb_health_alb(mock_aws_provider):
    """ELB health for Application Load Balancer."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.elb_health",
        args={
            "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/my-tg/abc",
            "region": "us-east-1",
        },
        analysis_json=analysis,
    )

    assert result.ok
    assert "target_group_arn" in result.result


def test_aws_rds_status_explicit_params(mock_aws_provider):
    """RDS status with explicit parameters."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.rds_status",
        args={"db_instance_id": "my-db", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["db_instance_id"] == "my-db"
    assert result.result["db_instance_status"] == "available"


def test_aws_ecr_image_explicit_params(mock_aws_provider):
    """ECR image scan with explicit parameters."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ecr_image",
        args={"repository": "my-app", "image_tag": "v1.0.0", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["repository"] == "my-app"
    assert result.result["image_tag"] == "v1.0.0"


def test_aws_ecr_image_auto_discovery(mock_aws_provider):
    """ECR image auto-discovers from metadata."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {
        "evidence": {
            "aws": {
                "metadata": {
                    "ecr_repositories": [{"repository": "auto-app", "tag": "v2.0.0", "region": "us-west-2"}],
                }
            }
        }
    }

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ecr_image",
        args={},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["repository"] == "auto-app"
    assert result.result["image_tag"] == "v2.0.0"


def test_aws_security_group_explicit_params(mock_aws_provider):
    """Security group with explicit parameters."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.security_group",
        args={"security_group_id": "sg-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["security_group_id"] == "sg-abc123"


def test_aws_nat_gateway_explicit_params(mock_aws_provider):
    """NAT gateway with explicit parameters."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.nat_gateway",
        args={"nat_gateway_id": "nat-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["nat_gateway_id"] == "nat-abc123"
    assert result.result["state"] == "available"


def test_aws_vpc_endpoint_explicit_params(mock_aws_provider):
    """VPC endpoint with explicit parameters."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.vpc_endpoint",
        args={"vpc_endpoint_id": "vpce-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["vpc_endpoint_id"] == "vpce-abc123"
    assert result.result["state"] == "available"


def test_aws_tools_require_resource_id():
    """AWS tools return error if required resource ID is missing."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    # EC2 without instance_id
    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ec2_status",
        args={"region": "us-east-1"},
        analysis_json=analysis,
    )
    assert not result.ok
    assert "required" in result.error

    # EBS without volume_id
    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ebs_health",
        args={"region": "us-east-1"},
        analysis_json=analysis,
    )
    assert not result.ok
    assert "required" in result.error


def test_aws_tools_handle_provider_errors(mock_aws_provider, monkeypatch):
    """AWS tools handle provider errors gracefully."""

    def _failing_provider():
        raise Exception("AWS API unavailable")

    monkeypatch.setattr("agent.providers.aws_provider.get_aws_provider", _failing_provider)

    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.ec2_status",
        args={"instance_id": "i-abc123", "region": "us-east-1"},
        analysis_json=analysis,
    )

    assert not result.ok
    assert "aws_error" in result.error


def test_aws_cloudtrail_events_with_empty_args(mock_aws_provider, monkeypatch):
    """CloudTrail events with empty args uses default time window and region."""

    policy = ChatPolicy(allow_aws_read=True)
    analysis = {
        "alert": {"starts_at": "2024-01-01T10:00:00Z", "ends_at": "2024-01-01T11:00:00Z"},
        "evidence": {"aws": {"metadata": {"region": "us-east-1", "ec2_instances": ["i-abc123"]}}},
    }

    # Mock _group_cloudtrail_events in the correct module
    monkeypatch.setattr("agent.collectors.aws_context._group_cloudtrail_events", lambda events: {})

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.cloudtrail_events",
        args={},  # Empty args - should use defaults
        analysis_json=analysis,
    )

    assert result.ok
    assert "events" in result.result
    assert "metadata" in result.result
    assert result.result["metadata"]["region"] == "us-east-1"
    assert len(result.result["events"]) == 1


def test_aws_cloudtrail_events_requires_policy(mock_aws_provider):
    """CloudTrail events requires allow_aws_read policy."""
    policy = ChatPolicy(allow_aws_read=False)
    analysis = {"evidence": {"aws": {"metadata": {}}}}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.cloudtrail_events",
        args={},
        analysis_json=analysis,
    )

    assert not result.ok
    assert result.error == "tool_not_allowed"


def test_aws_cloudtrail_events_respects_region_allowlist(mock_aws_provider, monkeypatch):
    """CloudTrail events respects region allowlist."""
    # Mock _group_cloudtrail_events in the correct module
    monkeypatch.setattr("agent.collectors.aws_context._group_cloudtrail_events", lambda events: {})

    policy = ChatPolicy(allow_aws_read=True, aws_region_allowlist={"us-east-1"})
    analysis = {
        "alert": {"starts_at": "2024-01-01T10:00:00Z"},
        "evidence": {"aws": {"metadata": {"region": "eu-west-1"}}},
    }

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.cloudtrail_events",
        args={},
        analysis_json=analysis,
    )

    assert not result.ok
    assert "region_not_allowed" in result.error


def test_aws_s3_bucket_location_requires_policy(mock_aws_provider):
    """S3 bucket location tool requires allow_aws_read policy."""
    policy = ChatPolicy(allow_aws_read=False)
    analysis = {}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.s3_bucket_location",
        args={"bucket": "test-bucket"},
        analysis_json=analysis,
    )

    assert not result.ok
    assert result.error == "tool_not_allowed"


def test_aws_s3_bucket_location_explicit_bucket(mock_aws_provider):
    """S3 bucket location with explicit bucket name."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.s3_bucket_location",
        args={"bucket": "test-bucket-uswest2"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["bucket"] == "test-bucket-uswest2"
    assert result.result["location"] == "us-west-2"
    assert result.result["exists"] is True


def test_aws_s3_bucket_location_auto_extract_from_logs(mock_aws_provider):
    """S3 bucket location auto-extracts bucket name from parsed_errors."""
    policy = ChatPolicy(allow_aws_read=True)
    # Use error message format that matches the actual S3 error pattern
    # "Failed to get bucket region for example-bucket.example.com:"
    analysis = {
        "evidence": {
            "logs": {
                "parsed_errors": [
                    {"message": "Failed to get bucket region for test-bucket-useast1:"},
                    {"message": "Another error"},
                ]
            }
        }
    }

    result = run_tool(policy=policy, action_policy=None, tool="aws.s3_bucket_location", args={}, analysis_json=analysis)

    assert result.ok
    assert result.result["bucket"] == "test-bucket-useast1"
    assert result.result["location"] == "us-east-1"


def test_aws_s3_bucket_location_nonexistent_bucket(mock_aws_provider):
    """S3 bucket location handles nonexistent bucket."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.s3_bucket_location",
        args={"bucket": "nonexistent-bucket"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["exists"] is False
    assert result.result["error"] == "bucket_not_found"


def test_aws_s3_bucket_location_missing_bucket_name(mock_aws_provider):
    """S3 bucket location requires bucket name."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"logs": {"parsed_errors": []}}}

    result = run_tool(policy=policy, action_policy=None, tool="aws.s3_bucket_location", args={}, analysis_json=analysis)

    assert not result.ok
    assert result.error == "bucket_name_required"


def test_aws_iam_role_permissions_requires_policy(mock_aws_provider):
    """IAM role permissions tool requires allow_aws_read policy."""
    policy = ChatPolicy(allow_aws_read=False)
    analysis = {}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.iam_role_permissions",
        args={"role_name": "test-role"},
        analysis_json=analysis,
    )

    assert not result.ok
    assert result.error == "tool_not_allowed"


def test_aws_iam_role_permissions_explicit_role(mock_aws_provider):
    """IAM role permissions with explicit role name."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.iam_role_permissions",
        args={"role_name": "test-role-with-s3"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["role_name"] == "test-role-with-s3"
    assert "attached_policies" in result.result
    assert len(result.result["attached_policies"]) > 0
    assert "s3" in result.result["attached_policies"][0]["permissions_by_service"]


def test_aws_iam_role_permissions_no_s3_permissions(mock_aws_provider):
    """IAM role permissions shows role without S3 permissions."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.iam_role_permissions",
        args={"role_name": "test-role-no-s3"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["role_name"] == "test-role-no-s3"
    assert "attached_policies" in result.result
    # Check that no S3 permissions are present
    for policy_doc in result.result["attached_policies"]:
        assert "s3" not in policy_doc["permissions_by_service"]


def test_aws_iam_role_permissions_auto_extract_from_service_account(mock_aws_provider):
    """IAM role permissions auto-extracts role from service account annotations."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {
        "evidence": {
            "k8s": {
                "pod_info": {
                    "service_account": "my-sa",
                    "annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/test-role-with-s3"},
                }
            }
        }
    }

    result = run_tool(
        policy=policy, action_policy=None, tool="aws.iam_role_permissions", args={}, analysis_json=analysis
    )

    assert result.ok
    assert result.result["role_name"] == "test-role-with-s3"


def test_aws_iam_role_permissions_nonexistent_role(mock_aws_provider):
    """IAM role permissions handles nonexistent role."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {}

    result = run_tool(
        policy=policy,
        action_policy=None,
        tool="aws.iam_role_permissions",
        args={"role_name": "nonexistent-role"},
        analysis_json=analysis,
    )

    assert result.ok
    assert result.result["error"] == "role_not_found"


def test_aws_iam_role_permissions_missing_role_name(mock_aws_provider):
    """IAM role permissions requires role name."""
    policy = ChatPolicy(allow_aws_read=True)
    analysis = {"evidence": {"k8s": {"pod_info": {}}}}

    result = run_tool(
        policy=policy, action_policy=None, tool="aws.iam_role_permissions", args={}, analysis_json=analysis
    )

    assert not result.ok
    assert result.error == "role_name_required"
