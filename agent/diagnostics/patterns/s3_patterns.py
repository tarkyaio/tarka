"""S3-specific error patterns (part of extensible pattern library).

These patterns detect common S3 failure modes from parsed log errors.
"""

from agent.diagnostics.log_pattern_matcher import LogPattern

# S3 Access Denied (403)
S3_ACCESS_DENIED = LogPattern(
    pattern_id="s3_access_denied",
    title="S3 access denied (IAM/bucket policy)",
    patterns=[
        r"(?:403|Forbidden).*(?:s3|bucket)",
        r"Access Denied.*(?:HeadBucket|GetObject|PutObject|ListBucket)",
        r"botocore\.exceptions\.ClientError.*403.*(?:HeadBucket|GetObject)",
        r"Failed to get bucket region.*403",
    ],
    confidence=90,
    why_template="Job pod cannot access S3 bucket '{bucket}' (403 Forbidden from {operation} operation)",
    remediation_steps=[
        "Step 1: Get the IAM role ARN from service account",
        "kubectl get sa {sa} -n {namespace} -o jsonpath='{{.metadata.annotations.eks\\.amazonaws\\.com/role-arn}}'",
        "",
        "Step 2: Create S3 policy document (save as s3-policy.json):",
        "```json",
        "{{",
        '  "Version": "2012-10-17",',
        '  "Statement": [{{',
        '    "Effect": "Allow",',
        '    "Action": ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],',
        '    "Resource": ["arn:aws:s3:::{bucket}", "arn:aws:s3:::{bucket}/*"]',
        "  }}]",
        "}}",
        "```",
        "",
        "Step 3: Attach the policy to the IAM role",
        "aws iam put-role-policy --role-name <ROLE_NAME> --policy-name S3Access --policy-document file://s3-policy.json",
        "",
        "Alternative: If the role doesn't exist, annotate the service account with a new role",
        "kubectl annotate sa {sa} -n {namespace} eks.amazonaws.com/role-arn=arn:aws:iam::<ACCOUNT>:role/<ROLE_NAME> --overwrite",
    ],
    next_tests=[
        "Verify bucket exists and check current permissions:",
        "aws s3api head-bucket --bucket {bucket}",
        "",
        "Check current IAM role annotation:",
        "kubectl get sa {sa} -n {namespace} -o yaml | grep eks.amazonaws.com/role-arn",
        "",
        "Test if role has required S3 permissions:",
        "aws iam simulate-principal-policy --policy-source-arn <ROLE_ARN> --action-names s3:GetObject s3:ListBucket --resource-arns arn:aws:s3:::{bucket}/*",
    ],
    context_extractors={
        "bucket": r"(?:for\s+(?:bucket\s+)?|bucket[:\s]\s*(?!region\b))([a-z0-9][a-z0-9.-]*[a-z0-9])",  # Match "bucket: X", "bucket X", "for X", "for bucket X" (not "bucket region")
        "operation": r"(HeadBucket|GetObject|PutObject|ListBucket|GetBucketRegion)",
    },
)

# S3 Bucket Not Found (404)
S3_BUCKET_NOT_FOUND = LogPattern(
    pattern_id="s3_bucket_not_found",
    title="S3 bucket does not exist",
    patterns=[
        r"(?:404|NoSuchBucket).*(?:s3|bucket)",
        r"The specified bucket does not exist",
        r"botocore\.exceptions\.ClientError.*NoSuchBucket",
    ],
    confidence=95,
    why_template="S3 bucket '{bucket}' does not exist or is in a different region",
    next_tests=[
        "Check if bucket exists:",
        "aws s3api head-bucket --bucket {bucket}",
        "",
        "List all accessible buckets:",
        "aws s3 ls | grep {bucket}",
        "",
        "Verify bucket name in application config:",
        "kubectl get configmap -n {namespace} -o yaml | grep -i {bucket}",
    ],
    context_extractors={"bucket": r"(?:for\s+(?:bucket\s+)?|bucket[:\s]\s*(?!region\b))([a-z0-9][a-z0-9.-]*[a-z0-9])"},
)

# S3 Credentials Not Found
S3_CREDENTIALS_ERROR = LogPattern(
    pattern_id="s3_credentials_error",
    title="AWS credentials not configured",
    patterns=[
        r"Unable to locate credentials",
        r"No credentials found",
        r"botocore\.exceptions\.NoCredentialsError",
        r"Unable to locate AWS credentials",
    ],
    confidence=85,
    why_template="Job pod has no AWS credentials configured (IRSA not set up)",
    next_tests=[
        "Check service account for IRSA annotation:",
        "kubectl get sa {sa} -n {namespace} -o yaml",
        "",
        "Verify service account token is mounted in pod:",
        "kubectl describe pod {pod} -n {namespace} | grep -A5 'AWS_WEB_IDENTITY_TOKEN_FILE'",
        "",
        "Check OIDC provider configuration for EKS cluster:",
        "aws eks describe-cluster --name {cluster_name} --query 'cluster.identity.oidc.issuer'",
    ],
    context_extractors={},
)

# S3 Region Mismatch
S3_REGION_MISMATCH = LogPattern(
    pattern_id="s3_region_mismatch",
    title="S3 bucket region mismatch",
    patterns=[
        r"bucket.*is in.*(?:region|Region)",
        r"PermanentRedirect.*bucket",
        r"The bucket you are attempting to access must be addressed using the specified endpoint",
    ],
    confidence=85,
    why_template="S3 bucket '{bucket}' is in a different region than the client is configured for",
    next_tests=[
        "Get bucket region:",
        "aws s3api get-bucket-location --bucket {bucket}",
        "",
        "Check AWS_DEFAULT_REGION environment variable in pod:",
        "kubectl exec {pod} -n {namespace} -- env | grep AWS_DEFAULT_REGION",
        "",
        "Fix: Add AWS_DEFAULT_REGION or AWS_REGION environment variable to pod spec",
    ],
    context_extractors={"bucket": r"(?:for\s+(?:bucket\s+)?|bucket[:\s]\s*(?!region\b))([a-z0-9][a-z0-9.-]*[a-z0-9])"},
)


# Export all S3 patterns
S3_PATTERNS = [
    S3_ACCESS_DENIED,
    S3_BUCKET_NOT_FOUND,
    S3_CREDENTIALS_ERROR,
    S3_REGION_MISMATCH,
]
