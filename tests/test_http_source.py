"""Unit tests for the generic HTTP source: config, signature, and normalizer."""

from __future__ import annotations

import hashlib
import hmac
import textwrap

import pytest

from agent.sources.config import FieldMap, SourceConfig, load_sources_config
from agent.sources.normalizer import normalize_http_event
from agent.sources.signature import verify_signature

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> SourceConfig:
    defaults = dict(
        id="test",
        name="Test Source",
        field_map=FieldMap(
            fingerprint="{{ payload.id }}",
            alertname="{{ payload.title }}",
            status="{{ 'resolved' if payload.status == 'resolved' else 'firing' }}",
        ),
    )
    defaults.update(overrides)
    return SourceConfig(**defaults)


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_sources_config(tmp_path):
    cfg_yaml = textwrap.dedent("""
        sources:
          - id: zendesk
            name: Zendesk
            field_map:
              fingerprint: "{{ payload.ticket.id }}"
              alertname: "{{ payload.ticket.subject }}"
              status: "firing"
    """)
    f = tmp_path / "http_sources.yaml"
    f.write_text(cfg_yaml)

    cfg = load_sources_config(str(f))
    assert len(cfg.sources) == 1
    assert cfg.sources[0].id == "zendesk"
    assert cfg.sources[0].field_map.fingerprint == "{{ payload.ticket.id }}"


def test_load_sources_config_empty(tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("{}")
    cfg = load_sources_config(str(f))
    assert cfg.sources == []


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_verify_signature_valid():
    body = b'{"hello": "world"}'
    secret = "supersecret"
    sig = _sign(body, secret)
    assert verify_signature(body, secret, sig) is True


def test_verify_signature_with_prefix():
    body = b'{"hello": "world"}'
    secret = "supersecret"
    sig = "sha256=" + _sign(body, secret)
    assert verify_signature(body, secret, sig, prefix="sha256=") is True


def test_verify_signature_invalid():
    body = b'{"hello": "world"}'
    assert verify_signature(body, "secret", "badsig") is False


def test_verify_signature_empty_header():
    body = b'{"hello": "world"}'
    assert verify_signature(body, "secret", "") is False


def test_verify_signature_wrong_secret():
    body = b'{"hello": "world"}'
    sig = _sign(body, "correct-secret")
    assert verify_signature(body, "wrong-secret", sig) is False


# ---------------------------------------------------------------------------
# Normalizer — happy path
# ---------------------------------------------------------------------------

ZENDESK_PAYLOAD = {
    "ticket": {
        "id": "ZD-1001",
        "subject": "DB slow",
        "status": "open",
        "priority": "high",
        "created_at": "2024-01-15T10:00:00Z",
        "url": "https://example.zendesk.com/tickets/1001",
        "description": "Queries taking >10s",
        "tags": ["team-platform", "db"],
    }
}

ZENDESK_CONFIG = SourceConfig(
    id="zendesk",
    name="Zendesk",
    field_map=FieldMap(
        fingerprint="{{ payload.ticket.id }}",
        alertname="{{ payload.ticket.subject }}",
        severity="{{ 'critical' if payload.ticket.priority == 'urgent' else 'warning' if payload.ticket.priority == 'high' else 'info' }}",
        summary="{{ payload.ticket.subject }} [{{ payload.ticket.id }}]",
        description="{{ payload.ticket.description | truncate(500) }}",
        source_url="{{ payload.ticket.url }}",
        starts_at="{{ payload.ticket.created_at }}",
        status="{{ 'resolved' if payload.ticket.status in ['solved', 'closed'] else 'firing' }}",
    ),
    extra_labels={
        "source": "zendesk",
        "team": "{{ payload.ticket.tags | select('startswith', 'team-') | first | default('unknown') }}",
    },
)


def test_normalize_zendesk_firing():
    result = normalize_http_event(ZENDESK_PAYLOAD, ZENDESK_CONFIG)

    assert result["fingerprint"] == "ZD-1001"
    assert result["labels"]["alertname"] == "DB slow"
    assert result["labels"]["severity"] == "warning"
    assert result["labels"]["source_id"] == "zendesk"
    assert result["labels"]["source"] == "zendesk"
    assert result["labels"]["team"] == "team-platform"
    assert result["annotations"]["summary"] == "DB slow [ZD-1001]"
    assert result["annotations"]["description"] == "Queries taking >10s"
    assert result["annotations"]["source_url"] == "https://example.zendesk.com/tickets/1001"
    assert result["starts_at"] == "2024-01-15T10:00:00Z"
    assert result["status"]["state"] == "firing"


def test_normalize_zendesk_resolved():
    payload = {**ZENDESK_PAYLOAD, "ticket": {**ZENDESK_PAYLOAD["ticket"], "status": "solved"}}
    result = normalize_http_event(payload, ZENDESK_CONFIG)
    assert result["status"]["state"] == "resolved"


def test_normalize_severity_critical():
    payload = {**ZENDESK_PAYLOAD, "ticket": {**ZENDESK_PAYLOAD["ticket"], "priority": "urgent"}}
    result = normalize_http_event(payload, ZENDESK_CONFIG)
    assert result["labels"]["severity"] == "critical"


def test_normalize_severity_info():
    payload = {**ZENDESK_PAYLOAD, "ticket": {**ZENDESK_PAYLOAD["ticket"], "priority": "low"}}
    result = normalize_http_event(payload, ZENDESK_CONFIG)
    assert result["labels"]["severity"] == "info"


# ---------------------------------------------------------------------------
# Normalizer — minimal config
# ---------------------------------------------------------------------------


def test_normalize_minimal():
    config = _make_config()
    payload = {"id": "evt-1", "title": "High error rate", "status": "firing"}
    result = normalize_http_event(payload, config)

    assert result["fingerprint"] == "evt-1"
    assert result["labels"]["alertname"] == "High error rate"
    assert result["status"]["state"] == "firing"
    assert result["labels"]["source_id"] == "test"
    # Optional fields absent
    assert result["annotations"] == {}
    assert result["starts_at"] is None
    assert result["ends_at"] is None


def test_normalize_unknown_status():
    config = _make_config(
        field_map=FieldMap(
            fingerprint="{{ payload.id }}",
            alertname="{{ payload.title }}",
            status="{{ payload.state }}",
        )
    )
    payload = {"id": "x", "title": "Something", "state": "pending"}
    result = normalize_http_event(payload, config)
    assert result["status"]["state"] == "unknown"


# ---------------------------------------------------------------------------
# Normalizer — error cases
# ---------------------------------------------------------------------------


def test_normalize_missing_fingerprint_raises():
    config = _make_config(
        field_map=FieldMap(
            fingerprint="{{ payload.missing_field }}",
            alertname="{{ payload.title }}",
            status="firing",
        )
    )
    with pytest.raises(ValueError, match="fingerprint"):
        normalize_http_event({"title": "hello"}, config)


def test_normalize_missing_alertname_raises():
    config = _make_config(
        field_map=FieldMap(
            fingerprint="{{ payload.id }}",
            alertname="{{ payload.missing_field }}",
            status="firing",
        )
    )
    with pytest.raises(ValueError, match="alertname"):
        normalize_http_event({"id": "x"}, config)


def test_normalize_missing_status_raises():
    config = _make_config(
        field_map=FieldMap(
            fingerprint="{{ payload.id }}",
            alertname="{{ payload.title }}",
            status="{{ payload.missing_field }}",
        )
    )
    with pytest.raises(ValueError, match="status"):
        normalize_http_event({"id": "x", "title": "t"}, config)


# ---------------------------------------------------------------------------
# Normalizer — extra_labels templating
# ---------------------------------------------------------------------------


def test_normalize_extra_labels_static():
    config = _make_config(extra_labels={"env": "production", "team": "platform"})
    result = normalize_http_event({"id": "x", "title": "t", "status": "firing"}, config)
    assert result["labels"]["env"] == "production"
    assert result["labels"]["team"] == "platform"


def test_normalize_extra_labels_templated():
    config = _make_config(extra_labels={"priority": "{{ payload.p }}"})
    result = normalize_http_event({"id": "x", "title": "t", "status": "firing", "p": "high"}, config)
    assert result["labels"]["priority"] == "high"


def test_normalize_extra_labels_undefined_skipped():
    config = _make_config(extra_labels={"x": "{{ payload.does_not_exist }}"})
    result = normalize_http_event({"id": "x", "title": "t", "status": "firing"}, config)
    # Empty-string extra labels are dropped
    assert "x" not in result["labels"]
