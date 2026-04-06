from __future__ import annotations

from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field


class FieldMap(BaseModel):
    """
    Jinja2 template strings that map an incoming payload to Tarka's internal alert schema.

    The template context exposes a single variable: ``payload`` — the parsed JSON body
    of the incoming HTTP request.  All standard Jinja2 filters are available.

    Required fields (fingerprint, alertname, status) must render to a non-empty string;
    a missing or empty value causes the event to be rejected with HTTP 400.

    ``status`` must render to exactly ``firing`` or ``resolved`` (case-insensitive).
    Any other value is treated as ``unknown``.
    """

    fingerprint: str = Field(..., description="Jinja2 template — stable dedup key (required)")
    alertname: str = Field(..., description="Jinja2 template — alert title / name (required)")
    status: str = Field(..., description="Jinja2 template — must render to firing|resolved (required)")

    severity: Optional[str] = Field(None, description="Jinja2 template — maps to labels.severity")
    summary: Optional[str] = Field(None, description="Jinja2 template — maps to annotations.summary")
    description: Optional[str] = Field(None, description="Jinja2 template — maps to annotations.description")
    source_url: Optional[str] = Field(None, description="Jinja2 template — maps to annotations.source_url")
    starts_at: Optional[str] = Field(None, description="Jinja2 template — RFC3339 timestamp")
    ends_at: Optional[str] = Field(None, description="Jinja2 template — RFC3339 timestamp (resolved events)")


class SourceConfig(BaseModel):
    """Configuration for a single HTTP source."""

    id: str = Field(..., description="URL-safe identifier — used in POST /sources/{id}/ingest")
    name: str = Field(..., description="Human-readable display name")

    # Optional HMAC-SHA256 signature verification
    secret: Optional[str] = Field(None, description="Shared secret for HMAC-SHA256 verification")
    signature_header: Optional[str] = Field(None, description="HTTP header that carries the signature")
    signature_prefix: str = Field("", description="Optional prefix to strip before comparing (e.g. 'sha256=')")

    field_map: FieldMap
    extra_labels: Dict[str, str] = Field(
        default_factory=dict,
        description="Static or Jinja2-templated labels added to every event from this source",
    )


class HttpSourcesConfig(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)


def load_sources_config(path: str) -> HttpSourcesConfig:
    """Load and validate an http_sources.yaml file."""
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    return HttpSourcesConfig.model_validate(raw or {})
