"""Image pull diagnostics helpers (deterministic, on-call UX).

We parse image references and error messages to produce stable, actionable hints.
Optionally, we can verify ECR tag/digest existence when AWS credentials are available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ImageRef:
    raw: str
    registry_host: Optional[str]
    repository: Optional[str]
    tag: Optional[str]
    digest: Optional[str]
    is_ecr: bool
    ecr_region: Optional[str]
    ecr_registry_id: Optional[str]


_ECR_HOST_RE = re.compile(r"^(?P<acct>\d+)\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com$")


def parse_image_ref(image: str) -> ImageRef:
    raw = (image or "").strip()
    host = None
    rest = raw

    # Split host/path: if the first component looks like a registry host, treat it as host.
    if "/" in raw:
        first, remainder = raw.split("/", 1)
        if "." in first or ":" in first or first == "localhost":
            host = first
            rest = remainder

    digest = None
    tag = None
    repo = rest
    if "@" in rest:
        repo, digest = rest.split("@", 1)
    else:
        # Tag is after the last ":" only (avoid host:port confusion since host is separated above)
        if ":" in rest:
            repo, tag = rest.rsplit(":", 1)

    is_ecr = False
    region = None
    registry_id = None
    if host:
        m = _ECR_HOST_RE.match(host)
        if m:
            is_ecr = True
            region = m.group("region")
            registry_id = m.group("acct")

    return ImageRef(
        raw=raw,
        registry_host=host,
        repository=repo or None,
        tag=tag or None,
        digest=digest or None,
        is_ecr=is_ecr,
        ecr_region=region,
        ecr_registry_id=registry_id,
    )


def extract_image_from_message(msg: str) -> Optional[str]:
    """
    Extract an image reference from common kubelet/containerd event messages.
    """
    s = msg or ""
    m = re.search(r'image\s+"([^"]+)"', s)
    if m:
        return m.group(1).strip() or None
    # Fallback patterns: occasionally no quotes
    m = re.search(r"pull(?:ing)? image\s+([^\s]+)", s, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip() or None
    return None


def classify_pull_error(msg: str) -> Tuple[str, str]:
    """
    Map an error message into a stable bucket for deterministic next steps.
    Returns: (bucket, evidence_snippet)
    """
    s = (msg or "").strip()
    sl = s.lower()

    # Not found / tag missing / repo missing
    if "notfound" in sl or "404" in sl or "manifest unknown" in sl:
        return "not_found", s[:220]

    # Auth / permissions
    if any(
        x in sl for x in ("unauthorized", "authentication required", "denied", "forbidden", "no basic auth credentials")
    ):
        return "auth", s[:220]

    # TLS/certs
    if any(x in sl for x in ("x509", "certificate", "tls handshake", "unknown authority")):
        return "tls", s[:220]

    # Network reachability / DNS
    if any(
        x in sl
        for x in ("i/o timeout", "context deadline", "no such host", "dial tcp", "connection refused", "timed out")
    ):
        return "network", s[:220]

    return "unknown", s[:220]


def ecr_describe_image(
    *,
    region: str,
    repository: str,
    tag: Optional[str],
    digest: Optional[str],
    registry_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Optional: verify ECR tag/digest existence.
    Returns a small dict: {status, detail}
    """
    try:
        import boto3  # type: ignore
        from botocore.exceptions import ClientError, NoCredentialsError  # type: ignore
    except Exception as e:
        return {"status": "skipped_no_boto", "detail": f"no_boto3:{type(e).__name__}"}

    if not region or not repository:
        return {"status": "skipped_invalid_input", "detail": "missing_region_or_repository"}
    if not (tag or digest):
        return {"status": "skipped_no_tag_or_digest", "detail": "no_tag_or_digest"}

    try:
        client = boto3.client("ecr", region_name=region)
        image_ids = []
        if digest:
            image_ids.append({"imageDigest": digest})
        else:
            image_ids.append({"imageTag": tag})
        # NOTE: `maxResults` is not valid when `imageIds` is provided.
        kwargs: Dict[str, Any] = {"repositoryName": repository, "imageIds": image_ids}
        if registry_id:
            kwargs["registryId"] = registry_id
        client.describe_images(**kwargs)
        return {"status": "exists", "detail": "found"}
    except NoCredentialsError:
        return {"status": "skipped_no_creds", "detail": "no_aws_credentials"}
    except Exception as e:
        # Best-effort error classification for common ECR cases.
        if isinstance(e, ClientError):
            code = (e.response or {}).get("Error", {}).get("Code")
            if code in ("ImageNotFoundException",):
                return {"status": "missing", "detail": "ImageNotFoundException"}
            if code in ("RepositoryNotFoundException",):
                return {"status": "missing", "detail": "RepositoryNotFoundException"}
            if code in ("AccessDeniedException", "UnrecognizedClientException"):
                return {"status": "error", "detail": f"ClientError:{code}"}
            return {"status": "error", "detail": f"ClientError:{code or 'unknown'}"}
        return {"status": "error", "detail": f"{type(e).__name__}"}
