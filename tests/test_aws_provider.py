"""
Unit tests for AWS provider with mocked boto3 client.
"""

from __future__ import annotations

import pytest

from agent.providers.aws_provider import (
    get_ebs_volume_health,
    get_ec2_instance_status,
    get_elb_target_health,
    get_elbv2_target_health,
    get_nat_gateway_status,
    get_rds_instance_status,
    get_security_group_rules,
    get_vpc_endpoint_status,
)


class _MockBoto3Client:
    """Mock boto3 client for testing."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def describe_instance_status(self, **kwargs):
        self.calls.append(("describe_instance_status", kwargs))
        return self.responses.get("describe_instance_status", {})

    def describe_volume_status(self, **kwargs):
        self.calls.append(("describe_volume_status", kwargs))
        return self.responses.get("describe_volume_status", {})

    def describe_volumes(self, **kwargs):
        self.calls.append(("describe_volumes", kwargs))
        return self.responses.get("describe_volumes", {})

    def describe_instance_health(self, **kwargs):
        self.calls.append(("describe_instance_health", kwargs))
        return self.responses.get("describe_instance_health", {})

    def describe_target_health(self, **kwargs):
        self.calls.append(("describe_target_health", kwargs))
        return self.responses.get("describe_target_health", {})

    def describe_db_instances(self, **kwargs):
        self.calls.append(("describe_db_instances", kwargs))
        return self.responses.get("describe_db_instances", {})

    def describe_security_groups(self, **kwargs):
        self.calls.append(("describe_security_groups", kwargs))
        return self.responses.get("describe_security_groups", {})

    def describe_nat_gateways(self, **kwargs):
        self.calls.append(("describe_nat_gateways", kwargs))
        return self.responses.get("describe_nat_gateways", {})

    def describe_vpc_endpoints(self, **kwargs):
        self.calls.append(("describe_vpc_endpoints", kwargs))
        return self.responses.get("describe_vpc_endpoints", {})


@pytest.fixture
def mock_boto3(monkeypatch):
    """Mock boto3 client factory."""
    clients = {}

    def _fake_boto3_client(service, region_name):
        key = f"{service}:{region_name}"
        if key not in clients:
            clients[key] = _MockBoto3Client({})
        return clients[key]

    class _FakeBoto3:
        client = staticmethod(_fake_boto3_client)

    import sys

    sys.modules["boto3"] = _FakeBoto3()  # type: ignore
    monkeypatch.setattr("agent.providers.aws_provider._boto3_clients", {})  # Clear cache

    return clients


def test_ec2_instance_status_success(mock_boto3, monkeypatch):
    """Test EC2 instance status retrieval."""
    client = _MockBoto3Client(
        {
            "describe_instance_status": {
                "InstanceStatuses": [
                    {
                        "InstanceId": "i-abc123",
                        "AvailabilityZone": "us-east-1a",
                        "InstanceState": {"Name": "running"},
                        "SystemStatus": {"Status": "ok"},
                        "InstanceStatus": {"Status": "ok"},
                        "Events": [],
                    }
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_ec2_instance_status("i-abc123", "us-east-1")

    assert result["instance_id"] == "i-abc123"
    assert result["instance_state"] == "running"
    assert result["system_status"] == "ok"
    assert result["instance_status"] == "ok"


def test_ec2_instance_not_found(mock_boto3, monkeypatch):
    """Test EC2 instance not found."""
    client = _MockBoto3Client({"describe_instance_status": {"InstanceStatuses": []}})

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_ec2_instance_status("i-notfound", "us-east-1")

    assert "error" in result
    assert "instance_not_found" in result["error"]


def test_ebs_volume_health_success(mock_boto3, monkeypatch):
    """Test EBS volume health retrieval."""
    client = _MockBoto3Client(
        {
            "describe_volume_status": {
                "VolumeStatuses": [
                    {
                        "VolumeId": "vol-xyz789",
                        "AvailabilityZone": "us-east-1a",
                        "VolumeStatus": {"Status": "ok"},
                        "Actions": [],
                        "Events": [],
                    }
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_ebs_volume_health("vol-xyz789", "us-east-1")

    assert result["volume_id"] == "vol-xyz789"
    assert result["volume_status"] == "ok"
    assert result["actions"] == []


def test_ebs_volume_fallback_to_describe_volumes(mock_boto3, monkeypatch):
    """Test EBS volume falls back to describe_volumes when no status."""
    client = _MockBoto3Client(
        {
            "describe_volume_status": {"VolumeStatuses": []},
            "describe_volumes": {
                "Volumes": [
                    {
                        "VolumeId": "vol-xyz789",
                        "State": "in-use",
                        "VolumeType": "gp3",
                        "Size": 100,
                        "Iops": 3000,
                        "AvailabilityZone": "us-east-1a",
                        "Attachments": [],
                    }
                ]
            },
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_ebs_volume_health("vol-xyz789", "us-east-1")

    assert result["volume_id"] == "vol-xyz789"
    assert result["state"] == "in-use"
    assert result["volume_type"] == "gp3"


def test_elb_target_health_success(mock_boto3, monkeypatch):
    """Test Classic ELB target health."""
    client = _MockBoto3Client(
        {
            "describe_instance_health": {
                "InstanceStates": [
                    {"InstanceId": "i-abc123", "State": "InService"},
                    {"InstanceId": "i-def456", "State": "OutOfService"},
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_elb_target_health("my-lb", "us-east-1")

    assert result["load_balancer_name"] == "my-lb"
    assert len(result["instance_states"]) == 2


def test_elbv2_target_health_success(mock_boto3, monkeypatch):
    """Test ALB/NLB target health."""
    client = _MockBoto3Client(
        {
            "describe_target_health": {
                "TargetHealthDescriptions": [
                    {
                        "Target": {"Id": "i-abc123", "Port": 8080},
                        "HealthCheckPort": "8080",
                        "TargetHealth": {"State": "healthy"},
                    }
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_elbv2_target_health("arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/my-tg/abc", "us-east-1")

    assert "target_group_arn" in result
    assert len(result["target_health_descriptions"]) == 1


def test_rds_instance_status_success(mock_boto3, monkeypatch):
    """Test RDS instance status."""
    client = _MockBoto3Client(
        {
            "describe_db_instances": {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "my-db",
                        "DBInstanceStatus": "available",
                        "Engine": "postgres",
                        "EngineVersion": "14.5",
                        "AvailabilityZone": "us-east-1a",
                        "MultiAZ": True,
                        "StorageEncrypted": True,
                        "PendingModifiedValues": {},
                    }
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_rds_instance_status("my-db", "us-east-1")

    assert result["db_instance_id"] == "my-db"
    assert result["db_instance_status"] == "available"
    assert result["engine"] == "postgres"


def test_security_group_rules_success(mock_boto3, monkeypatch):
    """Test security group rules retrieval."""
    client = _MockBoto3Client(
        {
            "describe_security_groups": {
                "SecurityGroups": [
                    {
                        "GroupId": "sg-abc123",
                        "GroupName": "my-sg",
                        "Description": "Test SG",
                        "VpcId": "vpc-xyz789",
                        "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443}],
                        "IpPermissionsEgress": [{"IpProtocol": "-1"}],
                    }
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_security_group_rules("sg-abc123", "us-east-1")

    assert result["security_group_id"] == "sg-abc123"
    assert len(result["ingress_rules"]) == 1
    assert len(result["egress_rules"]) == 1


def test_nat_gateway_status_success(mock_boto3, monkeypatch):
    """Test NAT gateway status."""
    client = _MockBoto3Client(
        {
            "describe_nat_gateways": {
                "NatGateways": [
                    {
                        "NatGatewayId": "nat-abc123",
                        "State": "available",
                        "SubnetId": "subnet-xyz",
                        "VpcId": "vpc-123",
                        "NatGatewayAddresses": [{"PublicIp": "1.2.3.4"}],
                    }
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_nat_gateway_status("nat-abc123", "us-east-1")

    assert result["nat_gateway_id"] == "nat-abc123"
    assert result["state"] == "available"


def test_vpc_endpoint_status_success(mock_boto3, monkeypatch):
    """Test VPC endpoint status."""
    client = _MockBoto3Client(
        {
            "describe_vpc_endpoints": {
                "VpcEndpoints": [
                    {
                        "VpcEndpointId": "vpce-abc123",
                        "VpcEndpointType": "Interface",
                        "VpcId": "vpc-xyz",
                        "ServiceName": "com.amazonaws.us-east-1.s3",
                        "State": "available",
                        "SubnetIds": ["subnet-1"],
                        "DnsEntries": [{"DnsName": "vpce-abc123-xyz.s3.us-east-1.vpce.amazonaws.com"}],
                    }
                ]
            }
        }
    )

    def _fake_get_client(service, region):
        return client

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_vpc_endpoint_status("vpce-abc123", "us-east-1")

    assert result["vpc_endpoint_id"] == "vpce-abc123"
    assert result["state"] == "available"


def test_missing_parameters_return_error():
    """Test that missing parameters return error dict."""
    result = get_ec2_instance_status("", "us-east-1")
    assert "error" in result

    result = get_ebs_volume_health("vol-123", "")
    assert "error" in result


def test_aws_api_error_returns_error_dict(monkeypatch):
    """Test that AWS API errors return error dict instead of raising."""

    def _fake_get_client(service, region):
        raise Exception("AWS API unavailable")

    monkeypatch.setattr("agent.providers.aws_provider._get_boto3_client", _fake_get_client)

    result = get_ec2_instance_status("i-abc123", "us-east-1")

    assert "error" in result
    assert "aws_error" in result["error"]
