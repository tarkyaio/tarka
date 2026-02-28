"""IAM role and IRSA validation utilities (optional, for AWS users).

These utilities validate IAM roles and IRSA (IAM Roles for Service Accounts) setup
when logs indicate IAM/authentication failures. Only used when AWS_EVIDENCE_ENABLED=true.
"""

import logging
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def get_iam_role_info(role_name: str) -> Dict[str, Any]:
    """
    Get IAM role info including trust policy and actual policy documents.

    This helps diagnose IAM permission issues by showing:
    - Whether role exists
    - Trust policy (who can assume the role)
    - Attached managed policies with full policy documents
    - Inline policies with full policy documents

    Args:
        role_name: IAM role name (not ARN)

    Returns:
        {
            "role_name": str,
            "role_arn": Optional[str],
            "trust_policy": Optional[dict] (AssumeRolePolicyDocument),
            "attached_policies": List[dict] with {
                "arn": str,
                "name": str,
                "document": dict (actual policy JSON)
            },
            "inline_policies": List[dict] with {
                "name": str,
                "document": dict (actual policy JSON)
            },
            "error": Optional[str],
            "error_code": Optional[str]
        }
    """
    try:
        iam = boto3.client("iam")

        # Get role details
        role_response = iam.get_role(RoleName=role_name)
        role = role_response.get("Role", {})

        role_arn = role.get("Arn")
        trust_policy = role.get("AssumeRolePolicyDocument")

        # Get attached managed policies with full documents
        attached_policies = []
        try:
            policies_response = iam.list_attached_role_policies(RoleName=role_name)
            for policy_summary in policies_response.get("AttachedPolicies", []):
                policy_arn = policy_summary.get("PolicyArn")
                policy_name = policy_summary.get("PolicyName")

                # Fetch the actual policy document
                try:
                    policy_response = iam.get_policy(PolicyArn=policy_arn)
                    policy_metadata = policy_response.get("Policy", {})
                    default_version_id = policy_metadata.get("DefaultVersionId")

                    if default_version_id:
                        version_response = iam.get_policy_version(PolicyArn=policy_arn, VersionId=default_version_id)
                        policy_document = version_response.get("PolicyVersion", {}).get("Document")

                        attached_policies.append(
                            {
                                "arn": policy_arn,
                                "name": policy_name,
                                "document": policy_document,
                            }
                        )
                    else:
                        # No default version, include without document
                        attached_policies.append(
                            {
                                "arn": policy_arn,
                                "name": policy_name,
                                "document": None,
                            }
                        )
                except Exception as e:
                    logger.warning(f"Failed to get policy document for {policy_arn}: {e}")
                    # Include policy without document
                    attached_policies.append(
                        {
                            "arn": policy_arn,
                            "name": policy_name,
                            "document": None,
                        }
                    )
        except Exception as e:
            logger.warning(f"Failed to list attached policies for role {role_name}: {e}")

        # Get inline policies with full documents
        inline_policies = []
        try:
            inline_response = iam.list_role_policies(RoleName=role_name)
            for policy_name in inline_response.get("PolicyNames", []):
                # Fetch the actual inline policy document
                try:
                    policy_response = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
                    policy_document = policy_response.get("PolicyDocument")

                    inline_policies.append(
                        {
                            "name": policy_name,
                            "document": policy_document,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to get inline policy document for {policy_name}: {e}")
                    # Include policy without document
                    inline_policies.append(
                        {
                            "name": policy_name,
                            "document": None,
                        }
                    )
        except Exception as e:
            logger.warning(f"Failed to list inline policies for role {role_name}: {e}")

        return {
            "role_name": role_name,
            "role_arn": role_arn,
            "trust_policy": trust_policy,
            "attached_policies": attached_policies,
            "inline_policies": inline_policies,
            "error": None,
            "error_code": None,
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.warning(f"Failed to get IAM role info for {role_name}: {error_code} - {error_message}")

        return {
            "role_name": role_name,
            "role_arn": None,
            "trust_policy": None,
            "attached_policies": [],
            "inline_policies": [],
            "error": error_message,
            "error_code": error_code,
        }

    except Exception as e:
        logger.error(f"Unexpected error getting IAM role info for {role_name}: {e}")
        return {
            "role_name": role_name,
            "role_arn": None,
            "trust_policy": None,
            "attached_policies": [],
            "inline_policies": [],
            "error": str(e),
            "error_code": "Unknown",
        }


def simulate_s3_principal_policy(
    role_arn: str,
    bucket_name: str,
    actions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Simulate IAM policy to check if role has S3 permissions.

    Uses iam:SimulatePrincipalPolicy API to test permissions without actually
    executing S3 operations.

    Args:
        role_arn: IAM role ARN
        bucket_name: S3 bucket name
        actions: List of S3 actions to test (default: common read/write actions)

    Returns:
        {
            "results": List[dict] (evaluation results per action),
            "allowed_actions": List[str] (actions that are allowed),
            "denied_actions": List[str] (actions that are denied),
            "error": Optional[str],
            "error_code": Optional[str]
        }
    """
    if actions is None:
        # Test common S3 actions
        actions = [
            "s3:GetObject",
            "s3:PutObject",
            "s3:ListBucket",
            "s3:HeadBucket",
            "s3:GetBucketLocation",
        ]

    try:
        iam = boto3.client("iam")

        # Simulate policy for bucket and objects
        resource_arns = [
            f"arn:aws:s3:::{bucket_name}",  # Bucket-level actions (ListBucket, HeadBucket)
            f"arn:aws:s3:::{bucket_name}/*",  # Object-level actions (GetObject, PutObject)
        ]

        response = iam.simulate_principal_policy(
            PolicySourceArn=role_arn,
            ActionNames=actions,
            ResourceArns=resource_arns,
        )

        results = response.get("EvaluationResults", [])

        allowed_actions = []
        denied_actions = []

        for result in results:
            action = result.get("EvalActionName")
            decision = result.get("EvalDecision")

            if decision == "allowed":
                allowed_actions.append(action)
            else:
                denied_actions.append(action)

        return {
            "results": results,
            "allowed_actions": allowed_actions,
            "denied_actions": denied_actions,
            "error": None,
            "error_code": None,
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.warning(f"Failed to simulate IAM policy for {role_arn}: {error_code} - {error_message}")

        return {
            "results": [],
            "allowed_actions": [],
            "denied_actions": [],
            "error": error_message,
            "error_code": error_code,
        }

    except Exception as e:
        logger.error(f"Unexpected error simulating IAM policy for {role_arn}: {e}")
        return {
            "results": [],
            "allowed_actions": [],
            "denied_actions": [],
            "error": str(e),
            "error_code": "Unknown",
        }


def check_irsa_trust_policy(trust_policy: dict, cluster_oidc_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Check if IAM role trust policy allows IRSA (EKS service account assumption).

    For IRSA to work, the trust policy must:
    1. Allow "sts:AssumeRoleWithWebIdentity" action
    2. Have a principal with the cluster's OIDC provider URL
    3. Have proper conditions for the service account

    Args:
        trust_policy: AssumeRolePolicyDocument from get_iam_role_info()
        cluster_oidc_url: Optional EKS cluster OIDC URL to validate against

    Returns:
        {
            "is_irsa_compatible": bool,
            "has_oidc_provider": bool,
            "oidc_providers": List[str] (OIDC provider URLs found),
            "has_assume_web_identity": bool,
            "issues": List[str] (list of issues found),
        }
    """
    issues = []
    oidc_providers = []
    has_assume_web_identity = False

    if not trust_policy or not isinstance(trust_policy, dict):
        return {
            "is_irsa_compatible": False,
            "has_oidc_provider": False,
            "oidc_providers": [],
            "has_assume_web_identity": False,
            "issues": ["Trust policy is missing or invalid"],
        }

    # Check statements
    statements = trust_policy.get("Statement", [])
    if not isinstance(statements, list):
        statements = [statements]

    for statement in statements:
        effect = statement.get("Effect")
        if effect != "Allow":
            continue

        # Check actions
        actions = statement.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]

        if "sts:AssumeRoleWithWebIdentity" in actions:
            has_assume_web_identity = True

        # Check principals
        principal = statement.get("Principal", {})
        if isinstance(principal, dict):
            federated = principal.get("Federated", [])
            if isinstance(federated, str):
                federated = [federated]

            for fed in federated:
                if "oidc.eks" in fed or "oidc-provider" in fed:
                    oidc_providers.append(fed)

    # Determine IRSA compatibility
    has_oidc_provider = len(oidc_providers) > 0

    if not has_assume_web_identity:
        issues.append("Trust policy does not allow 'sts:AssumeRoleWithWebIdentity' action")

    if not has_oidc_provider:
        issues.append("Trust policy does not reference an EKS OIDC provider")

    if cluster_oidc_url and has_oidc_provider:
        # Check if any OIDC provider matches the cluster
        if not any(cluster_oidc_url in provider for provider in oidc_providers):
            issues.append(f"Trust policy OIDC providers don't match cluster OIDC: {cluster_oidc_url}")

    is_irsa_compatible = has_assume_web_identity and has_oidc_provider

    return {
        "is_irsa_compatible": is_irsa_compatible,
        "has_oidc_provider": has_oidc_provider,
        "oidc_providers": oidc_providers,
        "has_assume_web_identity": has_assume_web_identity,
        "issues": issues,
    }


def extract_role_name_from_arn(role_arn: str) -> str:
    """
    Extract role name from IAM role ARN.

    Example:
        arn:aws:iam::123456789012:role/my-role -> my-role
        arn:aws:iam::123456789012:role/path/to/my-role -> my-role

    Args:
        role_arn: IAM role ARN

    Returns:
        Role name (last component of ARN path)
    """
    if not role_arn or ":" not in role_arn:
        return role_arn

    # Split by "/" and take the last component
    parts = role_arn.split("/")
    return parts[-1] if parts else role_arn
