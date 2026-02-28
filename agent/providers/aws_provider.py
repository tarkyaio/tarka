"""AWS API client for fetching EC2, EBS, ELB, RDS, ECR, and networking information (read-only)."""

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

_boto3_clients: Dict[str, Any] = {}
_client_lock = threading.Lock()


@runtime_checkable
class AwsProvider(Protocol):
    """Protocol for AWS resource health checks (read-only operations)."""

    # EC2 & EBS
    def get_ec2_instance_status(self, instance_id: str, region: str) -> Dict[str, Any]: ...

    def get_ebs_volume_health(self, volume_id: str, region: str) -> Dict[str, Any]: ...

    # ELB & ALB
    def get_elb_target_health(self, load_balancer_name: str, region: str) -> Dict[str, Any]: ...

    def get_elbv2_target_health(self, target_group_arn: str, region: str) -> Dict[str, Any]: ...

    # RDS
    def get_rds_instance_status(self, db_instance_id: str, region: str) -> Dict[str, Any]: ...

    # ECR (for image pull issues)
    def get_ecr_image_scan_findings(self, repository: str, image_tag: str, region: str) -> Dict[str, Any]: ...

    def get_ecr_repository_policy(self, repository: str, region: str) -> Dict[str, Any]: ...

    # Networking
    def get_security_group_rules(self, security_group_id: str, region: str) -> Dict[str, Any]: ...

    def get_nat_gateway_status(self, nat_gateway_id: str, region: str) -> Dict[str, Any]: ...

    def get_vpc_endpoint_status(self, vpc_endpoint_id: str, region: str) -> Dict[str, Any]: ...

    # S3
    def get_s3_bucket_location(self, bucket: str) -> Dict[str, Any]: ...

    # IAM
    def get_iam_role_permissions(self, role_name: str) -> Dict[str, Any]: ...

    # CloudTrail
    def lookup_cloudtrail_events(
        self,
        region: str,
        start_time: datetime,
        end_time: datetime,
        resource_ids: Optional[List[str]] = None,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Query CloudTrail events using LookupEvents API.

        Args:
            region: AWS region
            start_time: Query start (UTC)
            end_time: Query end (UTC)
            resource_ids: Optional list of resource IDs to filter by
            max_results: Max events to return (default 50)

        Returns:
            List of CloudTrail event dicts or error dict {"error": "..."}
        """
        ...


class DefaultAwsProvider:
    """Default AWS provider implementation using boto3."""

    def get_ec2_instance_status(self, instance_id: str, region: str) -> Dict[str, Any]:
        return get_ec2_instance_status(instance_id, region)

    def get_ebs_volume_health(self, volume_id: str, region: str) -> Dict[str, Any]:
        return get_ebs_volume_health(volume_id, region)

    def get_elb_target_health(self, load_balancer_name: str, region: str) -> Dict[str, Any]:
        return get_elb_target_health(load_balancer_name, region)

    def get_elbv2_target_health(self, target_group_arn: str, region: str) -> Dict[str, Any]:
        return get_elbv2_target_health(target_group_arn, region)

    def get_rds_instance_status(self, db_instance_id: str, region: str) -> Dict[str, Any]:
        return get_rds_instance_status(db_instance_id, region)

    def get_ecr_image_scan_findings(self, repository: str, image_tag: str, region: str) -> Dict[str, Any]:
        return get_ecr_image_scan_findings(repository, image_tag, region)

    def get_ecr_repository_policy(self, repository: str, region: str) -> Dict[str, Any]:
        return get_ecr_repository_policy(repository, region)

    def get_security_group_rules(self, security_group_id: str, region: str) -> Dict[str, Any]:
        return get_security_group_rules(security_group_id, region)

    def get_nat_gateway_status(self, nat_gateway_id: str, region: str) -> Dict[str, Any]:
        return get_nat_gateway_status(nat_gateway_id, region)

    def get_vpc_endpoint_status(self, vpc_endpoint_id: str, region: str) -> Dict[str, Any]:
        return get_vpc_endpoint_status(vpc_endpoint_id, region)

    def get_s3_bucket_location(self, bucket: str) -> Dict[str, Any]:
        return get_s3_bucket_location(bucket)

    def get_iam_role_permissions(self, role_name: str) -> Dict[str, Any]:
        return get_iam_role_permissions(role_name)

    def lookup_cloudtrail_events(
        self,
        region: str,
        start_time: datetime,
        end_time: datetime,
        resource_ids: Optional[List[str]] = None,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        return lookup_cloudtrail_events(region, start_time, end_time, resource_ids, max_results)


def get_aws_provider() -> AwsProvider:
    """Factory function for AWS provider (allows future swapping)."""
    return DefaultAwsProvider()


def _get_boto3_client(service: str, region: str) -> Any:
    """
    Get cached boto3 client for a service in a specific region.

    Thread-safe caching to avoid repeated client initialization.
    Uses IAM role authentication (IRSA/Workload Identity).
    """
    cache_key = f"{service}:{region}"

    if cache_key in _boto3_clients:
        return _boto3_clients[cache_key]

    with _client_lock:
        if cache_key in _boto3_clients:
            return _boto3_clients[cache_key]

        try:
            import boto3
        except ImportError:
            raise Exception("boto3 not installed. Install with: pip install boto3")

        # Use default credential chain (IAM role, env vars, etc.)
        client = boto3.client(service, region_name=region)
        _boto3_clients[cache_key] = client
        return client


# ============================================================================
# EC2 & EBS
# ============================================================================


def get_ec2_instance_status(instance_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch EC2 instance status (read-only).

    Returns system status checks, instance status checks, scheduled events, state.
    Never raises - returns dict with "error" key on failure.
    """
    if not instance_id or not region:
        return {"error": "instance_id and region required"}

    try:
        ec2 = _get_boto3_client("ec2", region)
        response = ec2.describe_instance_status(InstanceIds=[instance_id], IncludeAllInstances=True)

        if not response.get("InstanceStatuses"):
            return {"error": "instance_not_found", "instance_id": instance_id}

        status = response["InstanceStatuses"][0]

        return {
            "instance_id": status.get("InstanceId"),
            "availability_zone": status.get("AvailabilityZone"),
            "instance_state": status.get("InstanceState", {}).get("Name"),
            "system_status": status.get("SystemStatus", {}).get("Status"),
            "instance_status": status.get("InstanceStatus", {}).get("Status"),
            "system_status_details": status.get("SystemStatus", {}).get("Details", []),
            "instance_status_details": status.get("InstanceStatus", {}).get("Details", []),
            "events": status.get("Events", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


def get_ebs_volume_health(volume_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch EBS volume status and health (read-only).

    Returns volume state, attachment state, IOPS, throughput, and any actions needed.
    Never raises - returns dict with "error" key on failure.
    """
    if not volume_id or not region:
        return {"error": "volume_id and region required"}

    try:
        ec2 = _get_boto3_client("ec2", region)
        response = ec2.describe_volume_status(VolumeIds=[volume_id])

        if not response.get("VolumeStatuses"):
            # Volume might exist but have no status data - try describe_volumes
            try:
                vol_response = ec2.describe_volumes(VolumeIds=[volume_id])
                if vol_response.get("Volumes"):
                    vol = vol_response["Volumes"][0]
                    return {
                        "volume_id": vol.get("VolumeId"),
                        "state": vol.get("State"),
                        "volume_type": vol.get("VolumeType"),
                        "size": vol.get("Size"),
                        "iops": vol.get("Iops"),
                        "throughput": vol.get("Throughput"),
                        "attachments": vol.get("Attachments", []),
                        "availability_zone": vol.get("AvailabilityZone"),
                    }
            except Exception:
                pass
            return {"error": "volume_not_found", "volume_id": volume_id}

        status = response["VolumeStatuses"][0]

        return {
            "volume_id": status.get("VolumeId"),
            "availability_zone": status.get("AvailabilityZone"),
            "volume_status": status.get("VolumeStatus", {}).get("Status"),
            "volume_status_details": status.get("VolumeStatus", {}).get("Details", []),
            "actions": status.get("Actions", []),
            "events": status.get("Events", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


# ============================================================================
# ELB & ALB
# ============================================================================


def get_elb_target_health(load_balancer_name: str, region: str) -> Dict[str, Any]:
    """
    Fetch Classic Load Balancer target health (read-only).

    Returns registered instances and their health status.
    Never raises - returns dict with "error" key on failure.
    """
    if not load_balancer_name or not region:
        return {"error": "load_balancer_name and region required"}

    try:
        elb = _get_boto3_client("elb", region)
        response = elb.describe_instance_health(LoadBalancerName=load_balancer_name)

        return {
            "load_balancer_name": load_balancer_name,
            "instance_states": response.get("InstanceStates", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


def get_elbv2_target_health(target_group_arn: str, region: str) -> Dict[str, Any]:
    """
    Fetch Application/Network Load Balancer target health (read-only).

    Returns target health descriptions for all registered targets.
    Never raises - returns dict with "error" key on failure.
    """
    if not target_group_arn or not region:
        return {"error": "target_group_arn and region required"}

    try:
        elbv2 = _get_boto3_client("elbv2", region)
        response = elbv2.describe_target_health(TargetGroupArn=target_group_arn)

        return {
            "target_group_arn": target_group_arn,
            "target_health_descriptions": response.get("TargetHealthDescriptions", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


# ============================================================================
# RDS
# ============================================================================


def get_rds_instance_status(db_instance_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch RDS instance status (read-only).

    Returns instance state, availability, pending maintenance, recent events.
    Never raises - returns dict with "error" key on failure.
    """
    if not db_instance_id or not region:
        return {"error": "db_instance_id and region required"}

    try:
        rds = _get_boto3_client("rds", region)
        response = rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)

        if not response.get("DBInstances"):
            return {"error": "db_instance_not_found", "db_instance_id": db_instance_id}

        db = response["DBInstances"][0]

        return {
            "db_instance_id": db.get("DBInstanceIdentifier"),
            "db_instance_status": db.get("DBInstanceStatus"),
            "engine": db.get("Engine"),
            "engine_version": db.get("EngineVersion"),
            "availability_zone": db.get("AvailabilityZone"),
            "multi_az": db.get("MultiAZ"),
            "storage_encrypted": db.get("StorageEncrypted"),
            "pending_modified_values": db.get("PendingModifiedValues", {}),
            "status_infos": db.get("StatusInfos", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


# ============================================================================
# ECR
# ============================================================================


def get_ecr_image_scan_findings(repository: str, image_tag: str, region: str) -> Dict[str, Any]:
    """
    Fetch ECR image scan findings (read-only).

    Returns vulnerability scan results for a specific image.
    Never raises - returns dict with "error" key on failure.
    """
    if not repository or not image_tag or not region:
        return {"error": "repository, image_tag, and region required"}

    try:
        ecr = _get_boto3_client("ecr", region)
        response = ecr.describe_image_scan_findings(repositoryName=repository, imageId={"imageTag": image_tag})

        return {
            "repository": repository,
            "image_tag": image_tag,
            "scan_status": response.get("imageScanStatus", {}).get("status"),
            "findings_summary": response.get("imageScanFindings", {}).get("findingSeverityCounts", {}),
            "scan_findings": response.get("imageScanFindings", {}).get("findings", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


def get_ecr_repository_policy(repository: str, region: str) -> Dict[str, Any]:
    """
    Fetch ECR repository policy (read-only).

    Returns repository policy document (useful for diagnosing image pull permission issues).
    Never raises - returns dict with "error" key on failure.
    """
    if not repository or not region:
        return {"error": "repository and region required"}

    try:
        ecr = _get_boto3_client("ecr", region)
        response = ecr.get_repository_policy(repositoryName=repository)

        return {
            "repository": repository,
            "registry_id": response.get("registryId"),
            "policy_text": response.get("policyText"),
        }
    except Exception as e:
        # RepositoryPolicyNotFoundException is expected if no policy exists
        if "RepositoryPolicyNotFoundException" in str(type(e).__name__):
            return {"repository": repository, "policy_text": None}
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


# ============================================================================
# Networking
# ============================================================================


def get_security_group_rules(security_group_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch security group rules (read-only).

    Returns ingress and egress rules for a security group.
    Never raises - returns dict with "error" key on failure.
    """
    if not security_group_id or not region:
        return {"error": "security_group_id and region required"}

    try:
        ec2 = _get_boto3_client("ec2", region)
        response = ec2.describe_security_groups(GroupIds=[security_group_id])

        if not response.get("SecurityGroups"):
            return {"error": "security_group_not_found", "security_group_id": security_group_id}

        sg = response["SecurityGroups"][0]

        return {
            "security_group_id": sg.get("GroupId"),
            "group_name": sg.get("GroupName"),
            "description": sg.get("Description"),
            "vpc_id": sg.get("VpcId"),
            "ingress_rules": sg.get("IpPermissions", []),
            "egress_rules": sg.get("IpPermissionsEgress", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


def get_nat_gateway_status(nat_gateway_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch NAT gateway status (read-only).

    Returns NAT gateway state and connectivity information.
    Never raises - returns dict with "error" key on failure.
    """
    if not nat_gateway_id or not region:
        return {"error": "nat_gateway_id and region required"}

    try:
        ec2 = _get_boto3_client("ec2", region)
        response = ec2.describe_nat_gateways(NatGatewayIds=[nat_gateway_id])

        if not response.get("NatGateways"):
            return {"error": "nat_gateway_not_found", "nat_gateway_id": nat_gateway_id}

        ng = response["NatGateways"][0]

        return {
            "nat_gateway_id": ng.get("NatGatewayId"),
            "state": ng.get("State"),
            "subnet_id": ng.get("SubnetId"),
            "vpc_id": ng.get("VpcId"),
            "nat_gateway_addresses": ng.get("NatGatewayAddresses", []),
            "failure_code": ng.get("FailureCode"),
            "failure_message": ng.get("FailureMessage"),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


def get_vpc_endpoint_status(vpc_endpoint_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch VPC endpoint status (read-only).

    Returns VPC endpoint state and service information.
    Never raises - returns dict with "error" key on failure.
    """
    if not vpc_endpoint_id or not region:
        return {"error": "vpc_endpoint_id and region required"}

    try:
        ec2 = _get_boto3_client("ec2", region)
        response = ec2.describe_vpc_endpoints(VpcEndpointIds=[vpc_endpoint_id])

        if not response.get("VpcEndpoints"):
            return {"error": "vpc_endpoint_not_found", "vpc_endpoint_id": vpc_endpoint_id}

        ep = response["VpcEndpoints"][0]

        return {
            "vpc_endpoint_id": ep.get("VpcEndpointId"),
            "vpc_endpoint_type": ep.get("VpcEndpointType"),
            "vpc_id": ep.get("VpcId"),
            "service_name": ep.get("ServiceName"),
            "state": ep.get("State"),
            "subnet_ids": ep.get("SubnetIds", []),
            "dns_entries": ep.get("DnsEntries", []),
        }
    except Exception as e:
        return {"error": f"aws_error:{type(e).__name__}", "message": str(e)}


# ============================================================================
# S3
# ============================================================================


def get_s3_bucket_location(bucket: str) -> Dict[str, Any]:
    """
    Get S3 bucket region/location for diagnostic purposes.

    Agent must have s3:GetBucketLocation permission on all buckets.
    Never raises - returns dict with "error" key on failure.

    Returns:
        {
            "bucket": str,
            "location": str (region, e.g., "us-west-2", "us-east-1"),
            "exists": bool,
            "accessible": bool,
            "error": str|None
        }
    """
    if not bucket:
        return {"error": "bucket_name_required"}

    try:
        # GetBucketLocation doesn't require region - S3 is global
        s3 = _get_boto3_client("s3", "us-east-1")
        response = s3.get_bucket_location(Bucket=bucket)

        # LocationConstraint is None for us-east-1 (legacy behavior)
        location = response.get("LocationConstraint") or "us-east-1"

        return {
            "bucket": bucket,
            "location": location,
            "exists": True,
            "accessible": True,
            "error": None,
        }
    except Exception as e:
        error_name = type(e).__name__

        # Check for specific error codes
        if hasattr(e, "response") and "Error" in e.response:
            error_code = e.response["Error"].get("Code", "")

            if error_code == "NoSuchBucket":
                return {
                    "bucket": bucket,
                    "exists": False,
                    "accessible": False,
                    "location": None,
                    "error": "bucket_not_found",
                }
            elif error_code == "AccessDenied" or error_code == "403":
                # Agent itself lacks permission (shouldn't happen if IAM configured correctly)
                return {
                    "bucket": bucket,
                    "exists": "unknown",
                    "accessible": False,
                    "location": None,
                    "error": "agent_lacks_permission",
                }

        # Generic error
        return {
            "bucket": bucket,
            "exists": "unknown",
            "accessible": False,
            "location": None,
            "error": f"aws_error:{error_name}",
        }


# ============================================================================
# IAM
# ============================================================================


def get_iam_role_permissions(role_name: str) -> Dict[str, Any]:
    """
    Get IAM role permissions for diagnosis (generic, reusable for any AWS service).

    Agent must have iam:GetRole, iam:ListAttachedRolePolicies, iam:GetRolePolicy,
    iam:ListRolePolicies, iam:GetPolicy, iam:GetPolicyVersion permissions.
    Never raises - returns dict with "error" key on failure.

    Returns:
        {
            "role_name": str,
            "role_arn": str,
            "attached_policies": [{
                "policy_name": str,
                "policy_arn": str,
                "permissions_by_service": {
                    "s3": ["s3:GetObject", "s3:PutObject", ...],
                    "rds": ["rds:DescribeDBInstances", ...],
                    ...
                }
            }],
            "inline_policies": [{
                "policy_name": str,
                "permissions_by_service": {...}
            }],
            "error": str|None
        }
    """
    if not role_name:
        return {"error": "role_name_required"}

    try:
        # IAM is global, use us-east-1
        iam = _get_boto3_client("iam", "us-east-1")

        # Get role ARN
        role = iam.get_role(RoleName=role_name)
        role_arn = role["Role"]["Arn"]

        result = {
            "role_name": role_name,
            "role_arn": role_arn,
            "attached_policies": [],
            "inline_policies": [],
            "error": None,
        }

        # Check attached managed policies
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            policy_arn = policy["PolicyArn"]
            policy_name = policy["PolicyName"]

            # Get policy document
            try:
                policy_obj = iam.get_policy(PolicyArn=policy_arn)
                version_id = policy_obj["Policy"]["DefaultVersionId"]
                policy_version = iam.get_policy_version(PolicyArn=policy_arn, VersionId=version_id)
                policy_doc = policy_version["PolicyVersion"]["Document"]

                # Group permissions by service
                perms_by_service = _group_permissions_by_service(policy_doc)

                result["attached_policies"].append(
                    {
                        "policy_name": policy_name,
                        "policy_arn": policy_arn,
                        "permissions_by_service": perms_by_service,
                    }
                )
            except Exception:
                # Skip policies we can't read
                continue

        # Check inline policies
        inline = iam.list_role_policies(RoleName=role_name)
        for policy_name in inline.get("PolicyNames", []):
            try:
                policy_doc = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)["PolicyDocument"]
                perms_by_service = _group_permissions_by_service(policy_doc)

                result["inline_policies"].append(
                    {
                        "policy_name": policy_name,
                        "permissions_by_service": perms_by_service,
                    }
                )
            except Exception:
                # Skip policies we can't read
                continue

        return result

    except Exception as e:
        error_name = type(e).__name__

        # Check for specific error codes
        if hasattr(e, "response") and "Error" in e.response:
            error_code = e.response["Error"].get("Code", "")

            if error_code == "NoSuchEntity":
                return {"role_name": role_name, "error": "role_not_found"}
            elif error_code == "AccessDenied":
                return {"role_name": role_name, "error": "agent_lacks_iam_permission"}

        return {"role_name": role_name, "error": f"aws_error:{error_name}"}


def _group_permissions_by_service(policy_document: dict) -> dict:
    """
    Group IAM actions by AWS service prefix.

    Returns: {"s3": ["s3:GetObject", ...], "rds": [...], ...}
    """
    permissions: Dict[str, List[str]] = {}

    for statement in policy_document.get("Statement", []):
        if statement.get("Effect") != "Allow":
            continue

        actions = statement.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]

        for action in actions:
            if ":" in action:
                service = action.split(":")[0]
                if service not in permissions:
                    permissions[service] = []
                permissions[service].append(action)
            elif action == "*":
                # Wildcard - all services, all actions
                permissions["*"] = ["*"]

    # Dedupe each service's actions
    return {svc: list(set(actions)) for svc, actions in permissions.items()}


# ============================================================================
# CloudTrail
# ============================================================================


# Priority event names for infrastructure change detection
_PRIORITY_EVENT_NAMES = {
    # Security
    "AuthorizeSecurityGroupIngress",
    "RevokeSecurityGroupIngress",
    "ModifySecurityGroupRules",
    # Auto Scaling
    "UpdateAutoScalingGroup",
    "SetDesiredCapacity",
    "TerminateInstanceInAutoScalingGroup",
    # EC2 Lifecycle
    "RunInstances",
    "TerminateInstances",
    "StopInstances",
    "StartInstances",
    "RebootInstances",
    # IAM
    "PutUserPolicy",
    "AttachUserPolicy",
    "PutRolePolicy",
    "AttachRolePolicy",
    # Storage
    "CreateVolume",
    "AttachVolume",
    "DetachVolume",
    "DeleteVolume",
    "ModifyVolume",
    # Database
    "CreateDBInstance",
    "ModifyDBInstance",
    "RebootDBInstance",
    "DeleteDBInstance",
    # Networking
    "CreateNetworkInterface",
    "DeleteNetworkInterface",
    "ModifyNetworkInterfaceAttribute",
    # Load Balancer
    "RegisterTargets",
    "DeregisterTargets",
    "ModifyLoadBalancerAttributes",
}


def lookup_cloudtrail_events(
    region: str,
    start_time: datetime,
    end_time: datetime,
    resource_ids: Optional[List[str]] = None,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    """
    Query CloudTrail events using LookupEvents API.

    Filters to management events only (ReadWriteType="Write") and priority event names.
    Handles pagination with exponential backoff for CloudTrail rate limits.
    Never raises - returns list of events or [{"error": "..."}] on failure.

    Args:
        region: AWS region
        start_time: Query start (UTC, timezone-aware)
        end_time: Query end (UTC, timezone-aware)
        resource_ids: Optional list of resource IDs to filter by
        max_results: Max events to return (default 50)

    Returns:
        List of CloudTrail event dicts (chronological order)
    """
    if not region or not start_time or not end_time:
        return [{"error": "region, start_time, and end_time required"}]

    try:
        cloudtrail = _get_boto3_client("cloudtrail", region)

        # Build lookup attributes (resource filter)
        lookup_attributes = []
        if resource_ids:
            # CloudTrail LookupEvents only supports one attribute at a time
            # Use ResourceName attribute for first resource ID
            lookup_attributes.append({"AttributeKey": "ResourceName", "AttributeValue": resource_ids[0]})

        events = []
        next_token = None
        retries = 0
        max_retries = 3

        while len(events) < max_results:
            try:
                # Build request params
                params: Dict[str, Any] = {
                    "StartTime": start_time,
                    "EndTime": end_time,
                    "MaxResults": min(50, max_results - len(events)),  # API max is 50 per call
                }

                if lookup_attributes:
                    params["LookupAttributes"] = lookup_attributes

                if next_token:
                    params["NextToken"] = next_token

                response = cloudtrail.lookup_events(**params)

                # Filter to priority events and write operations
                for event in response.get("Events", []):
                    event_name = event.get("EventName", "")

                    # Filter to priority event names
                    if event_name not in _PRIORITY_EVENT_NAMES:
                        continue

                    # Extract event details
                    event_data = {
                        "EventName": event_name,
                        "EventTime": event.get("EventTime").isoformat() if event.get("EventTime") else None,
                        "Username": event.get("Username", "unknown"),
                        "EventId": event.get("EventId"),
                        "Resources": event.get("Resources", []),
                        "CloudTrailEvent": event.get("CloudTrailEvent"),  # JSON string of full event
                    }

                    events.append(event_data)

                # Check for pagination
                next_token = response.get("NextToken")
                if not next_token:
                    break

                # Rate limit: CloudTrail allows 2 req/sec, sleep to avoid throttling
                time.sleep(0.5)

            except Exception as e:
                # Check for throttling errors
                error_name = type(e).__name__
                if "Throttling" in error_name or "TooManyRequests" in error_name:
                    retries += 1
                    if retries >= max_retries:
                        return [{"error": f"cloudtrail_throttled_after_{retries}_retries"}]

                    # Exponential backoff
                    backoff = 2**retries
                    time.sleep(backoff)
                    continue
                else:
                    # Non-throttling error
                    return [{"error": f"aws_error:{error_name}", "message": str(e)}]

        # Sort events chronologically (oldest first)
        events.sort(key=lambda e: e.get("EventTime") or "")

        return events[:max_results]

    except Exception as e:
        return [{"error": f"aws_error:{type(e).__name__}", "message": str(e)}]
