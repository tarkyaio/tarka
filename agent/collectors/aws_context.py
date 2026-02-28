"""
AWS evidence collector with metadata extraction from investigations.

Extracts AWS resource IDs from alert labels, K8s pod specs, and annotations,
then collects AWS resource health information (EC2, EBS, ELB, RDS, ECR, networking).
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.core.models import Investigation
from agent.providers.aws_provider import get_aws_provider


def extract_aws_metadata_from_investigation(investigation: Investigation) -> Dict[str, Any]:
    """
    Extract AWS resource IDs from investigation context.

    Sources (in order of precedence):
    1. Alert labels (instance_id, volume_id, aws_region, load_balancer, etc.)
    2. K8s node names (EC2 instance IDs if starts with i-)
    3. Pod annotations (EBS CSI volume IDs)
    4. PVC metadata (EBS volume IDs)
    5. Container images (ECR repositories)
    6. Node labels (security group IDs, VPC IDs)

    Returns dict with discovered resource IDs keyed by type:
        {
            "region": "us-east-1",
            "ec2_instances": ["i-abc123"],
            "ebs_volumes": ["vol-xyz789"],
            "elb_names": ["my-classic-lb"],
            "elbv2_target_groups": ["arn:aws:..."],
            "rds_instances": ["my-db"],
            "ecr_repositories": [{"repo": "my-app", "tag": "v1.2.3"}],
            "security_groups": ["sg-abc123"],
            "nat_gateways": ["nat-xyz789"],
            "vpc_endpoints": ["vpce-abc123"],
        }
    """
    metadata: Dict[str, Any] = {
        "region": os.getenv("AWS_REGION", "us-east-1"),  # Default region
        "ec2_instances": [],
        "ebs_volumes": [],
        "elb_names": [],
        "elbv2_target_groups": [],
        "rds_instances": [],
        "ecr_repositories": [],
        "security_groups": [],
        "nat_gateways": [],
        "vpc_endpoints": [],
    }

    # 1. Extract from alert labels
    alert_labels = investigation.alert.labels or {}

    # Region
    if alert_labels.get("aws_region"):
        metadata["region"] = str(alert_labels["aws_region"])
    elif alert_labels.get("region"):
        metadata["region"] = str(alert_labels["region"])

    # EC2 instances
    if alert_labels.get("instance_id"):
        instance_id = str(alert_labels["instance_id"])
        if instance_id.startswith("i-"):
            metadata["ec2_instances"].append(instance_id)

    if alert_labels.get("instance"):
        instance = str(alert_labels["instance"])
        if instance.startswith("i-"):
            metadata["ec2_instances"].append(instance)

    # EBS volumes
    if alert_labels.get("volume_id"):
        volume_id = str(alert_labels["volume_id"])
        if volume_id.startswith("vol-"):
            metadata["ebs_volumes"].append(volume_id)

    # ELB
    if alert_labels.get("load_balancer"):
        metadata["elb_names"].append(str(alert_labels["load_balancer"]))

    if alert_labels.get("load_balancer_name"):
        metadata["elb_names"].append(str(alert_labels["load_balancer_name"]))

    # ELBv2 target groups
    if alert_labels.get("target_group"):
        tg = str(alert_labels["target_group"])
        if tg.startswith("arn:aws:elasticloadbalancing:"):
            metadata["elbv2_target_groups"].append(tg)

    if alert_labels.get("target_group_arn"):
        metadata["elbv2_target_groups"].append(str(alert_labels["target_group_arn"]))

    # RDS
    if alert_labels.get("db_instance_id"):
        metadata["rds_instances"].append(str(alert_labels["db_instance_id"]))

    if alert_labels.get("dbinstance_identifier"):
        metadata["rds_instances"].append(str(alert_labels["dbinstance_identifier"]))

    # Security groups
    if alert_labels.get("security_group_id"):
        sg_id = str(alert_labels["security_group_id"])
        if sg_id.startswith("sg-"):
            metadata["security_groups"].append(sg_id)

    # NAT gateways
    if alert_labels.get("nat_gateway_id"):
        nat_id = str(alert_labels["nat_gateway_id"])
        if nat_id.startswith("nat-"):
            metadata["nat_gateways"].append(nat_id)

    # VPC endpoints
    if alert_labels.get("vpc_endpoint_id"):
        vpce_id = str(alert_labels["vpc_endpoint_id"])
        if vpce_id.startswith("vpce-"):
            metadata["vpc_endpoints"].append(vpce_id)

    # 2. Extract from K8s context
    k8s_evidence = investigation.evidence.k8s
    pod_info = k8s_evidence.pod_info or {}

    # Node name as EC2 instance ID
    node_name = pod_info.get("node_name")
    if node_name:
        # EKS node names are often: ip-10-12-34-56.ec2.internal or i-abc123def456
        if node_name.startswith("i-"):
            metadata["ec2_instances"].append(node_name)
        # Try to extract instance ID from labels (EKS pattern)
        owner_chain = k8s_evidence.owner_chain or {}
        workload = owner_chain.get("workload") or {}
        workload_labels = workload.get("labels") or {}
        if workload_labels.get("eks.amazonaws.com/nodegroup"):
            # Node is from EKS - we might be able to look it up
            pass  # Future: Query K8s node object for instance ID annotation

    # 3. Extract from pod annotations (EBS CSI)
    # EBS CSI driver adds annotations like: volume.kubernetes.io/storage-provisioner: ebs.csi.aws.com
    # Volume IDs are in PV annotations, not pod annotations - would need PVC lookup

    # 4. Extract from container images (ECR)
    containers = pod_info.get("containers") or []
    for container in containers:
        image = container.get("image", "")
        # ECR image format: <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>
        ecr_match = re.match(r"(\d+)\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com/([^:]+):(.+)", image)
        if ecr_match:
            account, region, repo, tag = ecr_match.groups()
            metadata["ecr_repositories"].append({"repository": repo, "tag": tag, "region": region})
            # Update region if ECR region is different
            if region and not alert_labels.get("aws_region"):
                metadata["region"] = region

    # 5. Deduplicate lists
    metadata["ec2_instances"] = list(set(metadata["ec2_instances"]))
    metadata["ebs_volumes"] = list(set(metadata["ebs_volumes"]))
    metadata["elb_names"] = list(set(metadata["elb_names"]))
    metadata["elbv2_target_groups"] = list(set(metadata["elbv2_target_groups"]))
    metadata["rds_instances"] = list(set(metadata["rds_instances"]))
    metadata["security_groups"] = list(set(metadata["security_groups"]))
    metadata["nat_gateways"] = list(set(metadata["nat_gateways"]))
    metadata["vpc_endpoints"] = list(set(metadata["vpc_endpoints"]))

    return metadata


def collect_aws_evidence(investigation: Investigation) -> Dict[str, Any]:
    """
    Collect AWS evidence for an investigation.

    Best-effort collection - never raises exceptions.
    Returns dict with collected evidence and any errors encountered.

    Returns:
        {
            "ec2_instances": {instance_id: {status_data}, ...},
            "ebs_volumes": {volume_id: {health_data}, ...},
            "elb_health": {lb_name: {health_data}, ...},
            "rds_instances": {db_id: {status_data}, ...},
            "ecr_images": {repo:tag: {scan_data}, ...},
            "networking": {resource_id: {status_data}, ...},
            "metadata": metadata,
            "errors": [error_strings],
        }
    """
    errors: List[str] = []

    # Extract AWS metadata
    try:
        metadata = extract_aws_metadata_from_investigation(investigation)
    except Exception as e:
        return {"errors": [f"metadata_extraction_failed:{type(e).__name__}"]}

    region = metadata.get("region", "us-east-1")
    aws = get_aws_provider()

    # Collect EC2 instance status
    ec2_instances: Dict[str, Any] = {}
    for instance_id in metadata.get("ec2_instances", []):
        try:
            result = aws.get_ec2_instance_status(instance_id, region)
            ec2_instances[instance_id] = result
        except Exception as e:
            errors.append(f"ec2:{instance_id}:{type(e).__name__}")

    # Collect EBS volume health
    ebs_volumes: Dict[str, Any] = {}
    for volume_id in metadata.get("ebs_volumes", []):
        try:
            result = aws.get_ebs_volume_health(volume_id, region)
            ebs_volumes[volume_id] = result
        except Exception as e:
            errors.append(f"ebs:{volume_id}:{type(e).__name__}")

    # Collect ELB health (Classic)
    elb_health: Dict[str, Any] = {}
    for lb_name in metadata.get("elb_names", []):
        try:
            result = aws.get_elb_target_health(lb_name, region)
            elb_health[lb_name] = result
        except Exception as e:
            errors.append(f"elb:{lb_name}:{type(e).__name__}")

    # Collect ELBv2 health (ALB/NLB)
    for tg_arn in metadata.get("elbv2_target_groups", []):
        try:
            result = aws.get_elbv2_target_health(tg_arn, region)
            elb_health[tg_arn] = result
        except Exception as e:
            errors.append(f"elbv2:{tg_arn}:{type(e).__name__}")

    # Collect RDS instance status
    rds_instances: Dict[str, Any] = {}
    for db_id in metadata.get("rds_instances", []):
        try:
            result = aws.get_rds_instance_status(db_id, region)
            rds_instances[db_id] = result
        except Exception as e:
            errors.append(f"rds:{db_id}:{type(e).__name__}")

    # Collect ECR image scan findings
    ecr_images: Dict[str, Any] = {}
    for ecr_ref in metadata.get("ecr_repositories", []):
        if isinstance(ecr_ref, dict):
            repo = ecr_ref.get("repository")
            tag = ecr_ref.get("tag")
            ecr_region = ecr_ref.get("region", region)
            if repo and tag:
                try:
                    result = aws.get_ecr_image_scan_findings(repo, tag, ecr_region)
                    ecr_images[f"{repo}:{tag}"] = result
                except Exception as e:
                    errors.append(f"ecr:{repo}:{tag}:{type(e).__name__}")

    # Collect networking status
    networking: Dict[str, Any] = {}

    for sg_id in metadata.get("security_groups", []):
        try:
            result = aws.get_security_group_rules(sg_id, region)
            networking[sg_id] = result
        except Exception as e:
            errors.append(f"sg:{sg_id}:{type(e).__name__}")

    for nat_id in metadata.get("nat_gateways", []):
        try:
            result = aws.get_nat_gateway_status(nat_id, region)
            networking[nat_id] = result
        except Exception as e:
            errors.append(f"nat:{nat_id}:{type(e).__name__}")

    for vpce_id in metadata.get("vpc_endpoints", []):
        try:
            result = aws.get_vpc_endpoint_status(vpce_id, region)
            networking[vpce_id] = result
        except Exception as e:
            errors.append(f"vpce:{vpce_id}:{type(e).__name__}")

    return {
        "ec2_instances": ec2_instances,
        "ebs_volumes": ebs_volumes,
        "elb_health": elb_health,
        "rds_instances": rds_instances,
        "ecr_images": ecr_images,
        "networking": networking,
        "metadata": metadata,
        "errors": errors,
    }


def collect_cloudtrail_events(
    investigation: Investigation, expanded_start: datetime, max_results: int = 50
) -> Optional[Dict[str, Any]]:
    """
    Collect CloudTrail events for investigation.

    Phase 1: Extract metadata (region, resource IDs)
    Phase 2: Query CloudTrail with LookupEvents
    Phase 3: Group events by category

    Args:
        investigation: Investigation object with alert and evidence context
        expanded_start: Query start time (with lookback before alert)
        max_results: Max events to return (default 50)

    Returns:
        {
            "events": [...],  # Raw events (chronological)
            "grouped": {...},  # Events grouped by category
            "metadata": {...}  # Query metadata
        }
    """
    # Phase 1: Extract region and resource IDs from investigation
    region = _extract_region(investigation)
    resource_ids = _extract_resource_ids(investigation)

    # Phase 2: Query CloudTrail
    aws = get_aws_provider()

    # Parse end time (alert ends_at or now)
    end_time_str = investigation.alert.ends_at
    if end_time_str:
        # Parse ISO timestamp
        end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
    else:
        end_time = datetime.utcnow()

    start_query = time.time()
    events = aws.lookup_cloudtrail_events(
        region=region,
        start_time=expanded_start,
        end_time=end_time,
        resource_ids=resource_ids,
        max_results=max_results,
    )
    query_duration = time.time() - start_query

    # Check for errors
    if isinstance(events, list) and len(events) == 1 and isinstance(events[0], dict) and events[0].get("error"):
        return None

    # Phase 3: Group by category
    grouped = _group_cloudtrail_events(events)

    return {
        "events": events,
        "grouped": grouped,
        "metadata": {
            "time_window": f"{expanded_start.isoformat()} to {end_time.isoformat()}",
            "event_count": len(events),
            "query_duration_ms": int(query_duration * 1000),
            "region": region,
        },
    }


def _extract_region(investigation: Investigation) -> str:
    """Extract AWS region from investigation metadata."""
    # 1. Try alert labels
    alert_labels = investigation.alert.labels or {}
    if alert_labels.get("region"):
        return str(alert_labels["region"])
    if alert_labels.get("aws_region"):
        return str(alert_labels["aws_region"])

    # 2. Try AWS evidence metadata
    if investigation.evidence.aws and investigation.evidence.aws.metadata:
        metadata = investigation.evidence.aws.metadata
        if metadata.get("region"):
            return str(metadata["region"])

    # 3. Try EC2 instances
    if investigation.evidence.aws and investigation.evidence.aws.ec2_instances:
        for instance_id, data in investigation.evidence.aws.ec2_instances.items():
            if isinstance(data, dict) and data.get("region"):
                return str(data["region"])

    # 4. Default to environment variable or us-east-1
    return os.getenv("AWS_REGION", "us-east-1")


def _extract_resource_ids(investigation: Investigation) -> Optional[List[str]]:
    """Extract AWS resource IDs from investigation evidence."""
    resource_ids = []

    if investigation.evidence.aws:
        # EC2 instances
        if investigation.evidence.aws.ec2_instances:
            resource_ids.extend(investigation.evidence.aws.ec2_instances.keys())

        # EBS volumes
        if investigation.evidence.aws.ebs_volumes:
            resource_ids.extend(investigation.evidence.aws.ebs_volumes.keys())

        # RDS instances
        if investigation.evidence.aws.rds_instances:
            resource_ids.extend(investigation.evidence.aws.rds_instances.keys())

    return resource_ids if resource_ids else None


def _group_cloudtrail_events(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group CloudTrail events by category."""
    grouped = {
        "security_group": [],
        "auto_scaling": [],
        "ec2_lifecycle": [],
        "iam_policy": [],
        "storage": [],
        "database": [],
        "networking": [],
        "load_balancer": [],
    }

    for event in events:
        event_name = event.get("EventName", "")

        if event_name in ["AuthorizeSecurityGroupIngress", "RevokeSecurityGroupIngress", "ModifySecurityGroupRules"]:
            grouped["security_group"].append(event)
        elif event_name in ["UpdateAutoScalingGroup", "SetDesiredCapacity", "TerminateInstanceInAutoScalingGroup"]:
            grouped["auto_scaling"].append(event)
        elif event_name in ["RunInstances", "TerminateInstances", "StopInstances", "StartInstances", "RebootInstances"]:
            grouped["ec2_lifecycle"].append(event)
        elif event_name in ["PutUserPolicy", "AttachUserPolicy", "PutRolePolicy", "AttachRolePolicy"]:
            grouped["iam_policy"].append(event)
        elif event_name in ["CreateVolume", "AttachVolume", "DetachVolume", "DeleteVolume", "ModifyVolume"]:
            grouped["storage"].append(event)
        elif event_name in ["CreateDBInstance", "ModifyDBInstance", "RebootDBInstance", "DeleteDBInstance"]:
            grouped["database"].append(event)
        elif event_name in ["CreateNetworkInterface", "DeleteNetworkInterface", "ModifyNetworkInterfaceAttribute"]:
            grouped["networking"].append(event)
        elif event_name in ["RegisterTargets", "DeregisterTargets", "ModifyLoadBalancerAttributes"]:
            grouped["load_balancer"].append(event)

    # Remove empty categories
    return {k: v for k, v in grouped.items() if v}
