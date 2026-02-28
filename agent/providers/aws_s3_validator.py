"""S3 bucket validation utilities (optional, for AWS users).

These utilities validate S3 resources when logs indicate S3-related failures.
Only used when AWS_EVIDENCE_ENABLED=true.
"""

import logging
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


def check_s3_bucket_exists(bucket_name: str, region: Optional[str] = None) -> Dict[str, Any]:
    """
    Check if S3 bucket exists and is accessible.

    This validates:
    - Bucket existence (404 = doesn't exist)
    - Bucket accessibility (403 = access denied)
    - Bucket region (for region mismatch detection)

    Args:
        bucket_name: S3 bucket name
        region: Optional AWS region (if None, uses default boto3 config)

    Returns:
        {
            "exists": bool or None (True=exists, False=doesn't exist, None=unknown),
            "region": Optional[str] (bucket region if accessible),
            "error": Optional[str] (error message if any),
            "error_code": Optional[str] ("403", "404", etc.)
        }
    """
    try:
        s3 = boto3.client("s3", region_name=region)
        response = s3.head_bucket(Bucket=bucket_name)

        # Extract region from response headers
        bucket_region = response.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("x-amz-bucket-region")

        return {
            "exists": True,
            "region": bucket_region,
            "error": None,
            "error_code": None,
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        # 404 = bucket doesn't exist
        # 403 = bucket exists but access denied
        exists = None if error_code == "403" else False if error_code == "404" else None

        logger.warning(f"S3 bucket check failed for {bucket_name}: {error_code} - {error_message}")

        return {
            "exists": exists,
            "region": None,
            "error": error_message,
            "error_code": error_code,
        }

    except BotoCoreError as e:
        logger.error(f"Boto3 error checking S3 bucket {bucket_name}: {e}")
        return {
            "exists": None,
            "region": None,
            "error": str(e),
            "error_code": "BotoError",
        }

    except Exception as e:
        logger.error(f"Unexpected error checking S3 bucket {bucket_name}: {e}")
        return {
            "exists": None,
            "region": None,
            "error": str(e),
            "error_code": "Unknown",
        }


def get_s3_bucket_policy(bucket_name: str, region: Optional[str] = None) -> Dict[str, Any]:
    """
    Get S3 bucket policy (if accessible).

    This can help diagnose access issues by showing whether bucket policy
    restricts access from the IAM role.

    Args:
        bucket_name: S3 bucket name
        region: Optional AWS region

    Returns:
        {
            "policy": Optional[str] (JSON policy document),
            "error": Optional[str],
            "error_code": Optional[str]
        }
    """
    try:
        s3 = boto3.client("s3", region_name=region)
        response = s3.get_bucket_policy(Bucket=bucket_name)

        return {
            "policy": response.get("Policy"),
            "error": None,
            "error_code": None,
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        # NoSuchBucketPolicy means no policy is attached (not an error)
        if error_code == "NoSuchBucketPolicy":
            return {
                "policy": None,
                "error": None,
                "error_code": None,
            }

        logger.warning(f"Failed to get bucket policy for {bucket_name}: {error_code} - {error_message}")

        return {
            "policy": None,
            "error": error_message,
            "error_code": error_code,
        }

    except Exception as e:
        logger.error(f"Unexpected error getting bucket policy for {bucket_name}: {e}")
        return {
            "policy": None,
            "error": str(e),
            "error_code": "Unknown",
        }


def get_s3_bucket_location(bucket_name: str) -> Dict[str, Any]:
    """
    Get S3 bucket region.

    Useful for diagnosing region mismatch issues.

    Args:
        bucket_name: S3 bucket name

    Returns:
        {
            "region": Optional[str] (e.g., "us-east-1", "us-west-2"),
            "error": Optional[str],
            "error_code": Optional[str]
        }
    """
    try:
        # Use default region for location query
        s3 = boto3.client("s3")
        response = s3.get_bucket_location(Bucket=bucket_name)

        # Note: US East 1 returns None for LocationConstraint
        location = response.get("LocationConstraint") or "us-east-1"

        return {
            "region": location,
            "error": None,
            "error_code": None,
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_message = e.response.get("Error", {}).get("Message", str(e))

        logger.warning(f"Failed to get bucket location for {bucket_name}: {error_code} - {error_message}")

        return {
            "region": None,
            "error": error_message,
            "error_code": error_code,
        }

    except Exception as e:
        logger.error(f"Unexpected error getting bucket location for {bucket_name}: {e}")
        return {
            "region": None,
            "error": str(e),
            "error_code": "Unknown",
        }
