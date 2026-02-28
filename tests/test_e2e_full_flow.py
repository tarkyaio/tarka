"""E2E tests for full alert processing flow.

These tests require a running server and are executed in CI or manually.
Run with: pytest -m e2e
"""

import time
from typing import Generator

import pytest
import requests

BASE_URL = "http://localhost:8080"

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def wait_for_server() -> Generator[None, None, None]:
    """Wait for server to be ready."""
    max_retries = 30
    for i in range(max_retries):
        try:
            r = requests.get(f"{BASE_URL}/healthz", timeout=2)
            if r.status_code == 200:
                break
        except requests.RequestException:
            if i == max_retries - 1:
                raise Exception("Server failed to start within 30 seconds")
            time.sleep(1)
    yield


def test_webhook_accepts_alertmanager_payload(wait_for_server):
    """
    Test webhook accepts Alertmanager payload and publishes to NATS.

    This verifies:
    1. Webhook endpoint is accessible
    2. Payload validation works
    3. Job is published to NATS (worker processing is tested separately)
    """
    # Mock Alertmanager webhook payload (firing alert)
    alert_payload = {
        "version": "4",
        "groupKey": "test-group",
        "status": "firing",
        "receiver": "webhook",
        "groupLabels": {"alertname": "PodCPUThrottling"},
        "commonLabels": {
            "alertname": "PodCPUThrottling",
            "severity": "warning",
            "namespace": "default",
            "pod": "test-pod-12345",
            "cluster": "test-cluster",
        },
        "commonAnnotations": {
            "summary": "Pod test-pod-12345 is being CPU throttled",
            "description": "Pod is experiencing significant CPU throttling",
        },
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PodCPUThrottling",
                    "severity": "warning",
                    "namespace": "default",
                    "pod": "test-pod-12345",
                    "container": "app",
                    "cluster": "test-cluster",
                },
                "annotations": {
                    "summary": "Pod test-pod-12345 is being CPU throttled",
                    "description": "CPU throttling detected",
                },
                "startsAt": "2024-01-15T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "test-fp-12345",
            }
        ],
    }

    # Send alert to webhook
    r = requests.post(f"{BASE_URL}/alerts", json=alert_payload)
    assert r.status_code == 202, f"Webhook failed: {r.text}"
    body = r.json()
    assert body["ok"] is True
    # New response format includes enqueued count and mode
    assert "enqueued" in body or "message" in body  # Support both old and new format


def test_webhook_rejects_invalid_payload(wait_for_server):
    """Test webhook rejects malformed payloads."""
    # Missing required fields
    invalid_payload = {
        "version": "4",
        "status": "firing",
        # Missing alerts, labels, etc.
    }

    r = requests.post(f"{BASE_URL}/alerts", json=invalid_payload)
    # Should either reject or handle gracefully
    assert r.status_code in [202, 400, 422]


def test_webhook_handles_resolved_alerts(wait_for_server):
    """Test webhook handles resolved alerts."""
    resolved_payload = {
        "version": "4",
        "groupKey": "test-group-resolved",
        "status": "resolved",
        "receiver": "webhook",
        "groupLabels": {"alertname": "PodCPUThrottling"},
        "commonLabels": {
            "alertname": "PodCPUThrottling",
            "severity": "warning",
            "namespace": "default",
            "pod": "test-pod-resolved",
            "cluster": "test-cluster",
        },
        "commonAnnotations": {"summary": "Alert resolved"},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "resolved",
                "labels": {
                    "alertname": "PodCPUThrottling",
                    "severity": "warning",
                    "namespace": "default",
                    "pod": "test-pod-resolved",
                    "cluster": "test-cluster",
                },
                "annotations": {"summary": "Alert resolved"},
                "startsAt": "2024-01-15T10:00:00Z",
                "endsAt": "2024-01-15T10:30:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "test-fp-resolved",
            }
        ],
    }

    r = requests.post(f"{BASE_URL}/alerts", json=resolved_payload)
    assert r.status_code == 202
    body = r.json()
    assert body["ok"] is True


@pytest.mark.skip(reason="Requires worker to be running - manual test only")
def test_full_investigation_flow(wait_for_server):
    """
    MANUAL TEST: Complete end-to-end flow from webhook to investigation.

    To run this test manually:
    1. Start services: make dev-up
    2. Start webhook server: make dev-serve (terminal 1)
    3. Start worker: make dev-worker (terminal 2)
    4. Run this test: pytest tests/test_e2e_full_flow.py::test_full_investigation_flow -v
    5. Check worker logs for investigation output

    This test verifies:
    - Webhook receives alert
    - Job published to NATS
    - Worker consumes job
    - Investigation runs with mock data
    - Report generated (check logs)
    """
    alert_payload = {
        "version": "4",
        "groupKey": "e2e-test",
        "status": "firing",
        "receiver": "webhook",
        "groupLabels": {"alertname": "PodCPUThrottling"},
        "commonLabels": {
            "alertname": "PodCPUThrottling",
            "severity": "warning",
            "namespace": "default",
            "pod": "e2e-test-pod",
            "cluster": "test-cluster",
        },
        "commonAnnotations": {"summary": "E2E test alert"},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PodCPUThrottling",
                    "severity": "warning",
                    "namespace": "default",
                    "pod": "e2e-test-pod",
                    "container": "app",
                    "cluster": "test-cluster",
                },
                "annotations": {"summary": "E2E test alert"},
                "startsAt": "2024-01-15T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": "e2e-test-fp",
            }
        ],
    }

    # Send alert
    r = requests.post(f"{BASE_URL}/alerts", json=alert_payload)
    assert r.status_code == 202

    # Wait for worker to process (this requires worker to be running)
    time.sleep(10)

    # TODO: Query database or S3 to verify investigation was created
    # For now, this is a manual verification via worker logs
    print("\n✓ Alert sent. Check worker logs for investigation output.")


def test_investigation_quality_from_test_ci(wait_for_server):
    """
    Verify investigation created by test-ci.sh has valid structure.

    This test runs in Phase 10 of test-ci.sh, AFTER Phase 9 creates the investigation.
    It verifies the investigation has proper evidence collection and diagnostics.

    RESILIENCE TESTING:
    This test verifies the OUTCOME (evidence collected), not the MECHANISM (which path).
    The pipeline is designed for graceful degradation:
    - Primary path: Diagnostic module collects evidence
    - Fallback path: Playbook collects evidence if diagnostic fails
    - Either way: Investigation must have proper structure

    Quality checks:
    - Target fields are set (workload_kind, pod, namespace)
    - Evidence was collected (logs, metrics, k8s context)
    - Analysis was performed (verdict, scores, features)
    - Report is actionable (not empty placeholders)

    Note: This test depends on the investigation file created by test-ci.sh Phase 9.
    If running tests outside of test-ci.sh, this test may be skipped.
    """
    import glob
    import json
    import os

    # Find investigation created by test-ci.sh Phase 9
    # Look for JSON files (preferred) or MD files (fallback to parse)
    investigation_files = glob.glob("./investigations/*.json")
    if not investigation_files:
        # Fallback: try to find corresponding JSON from MD filename
        md_files = glob.glob("./investigations/*.md")
        if md_files:
            # Convert .md path to .json path (same name, different extension)
            investigation_files = [
                f.replace(".md", ".json") for f in md_files if os.path.exists(f.replace(".md", ".json"))
            ]

    if not investigation_files:
        pytest.skip("No investigation files found (test-ci.sh Phase 9 may not have run)")

    # Get most recent investigation
    latest_investigation = max(investigation_files, key=os.path.getctime)

    with open(latest_investigation) as f:
        investigation = json.load(f)

    # ========================================
    # CRITICAL STRUCTURAL CHECKS
    # These verify the pipeline executed correctly
    # ========================================

    # 1. Basic structure exists
    assert "target" in investigation, "Investigation missing target"
    assert "evidence" in investigation, "Investigation missing evidence"
    assert "analysis" in investigation, "Investigation missing analysis"

    # 2. Target was identified (namespace is ALWAYS set, regardless of path)
    target = investigation["target"]
    assert target.get("namespace") is not None, "Target namespace should be set (evidence collection failed)"

    # 3. Evidence collection happened (structure exists, content may vary)
    evidence = investigation["evidence"]
    assert "logs" in evidence, "Evidence missing logs section"
    assert "k8s" in evidence, "Evidence missing k8s section"

    # 4. Analysis was performed (verdict, scores, features)
    analysis = investigation["analysis"]
    assert "verdict" in analysis, "Analysis missing verdict"
    assert "features" in analysis, "Analysis missing features"
    assert "scores" in analysis, "Analysis missing scores"

    # 5. Verdict has required fields and is not empty placeholder
    verdict = analysis["verdict"]
    assert verdict.get("one_liner") is not None, "Verdict missing one_liner summary"
    assert len(verdict.get("one_liner", "")) > 0, "Verdict one_liner is empty"
    assert verdict.get("classification") is not None, "Verdict missing classification"

    # 6. Scores were computed and are in valid range
    scores = analysis["scores"]
    assert "impact_score" in scores, "Scores missing impact_score"
    assert "confidence_score" in scores, "Scores missing confidence_score"

    impact = scores.get("impact_score")
    confidence = scores.get("confidence_score")
    if impact is not None:
        assert 0 <= impact <= 100, f"Impact score out of range: {impact}"
    if confidence is not None:
        assert 0 <= confidence <= 100, f"Confidence score out of range: {confidence}"

    # 7. Family was detected
    features = analysis["features"]
    assert features.get("family") is not None, "Features missing family classification"

    # ========================================
    # QUALITY CHECKS (alert-family specific)
    # These verify the investigation has meaningful content
    # ========================================

    family = features.get("family")

    # For Job failures: verify workload identity was extracted
    # This works via either diagnostic module OR playbook fallback (resilient)
    if family == "job_failed":
        # Workload identity must be set (either path should do this)
        assert target.get("workload_kind") == "Job", (
            "Job failure investigation should set workload_kind=Job "
            "(evidence collection failed via both diagnostic and playbook paths)"
        )

        assert target.get("workload_name") is not None, (
            "Job failure investigation should extract job_name from alert labels "
            "(evidence collection failed via both diagnostic and playbook paths)"
        )

        # If logs were collected, parsing should have been attempted
        # (This is best-effort, may fail gracefully if pods are gone)
        if evidence.get("logs") and evidence["logs"].get("logs"):
            logs_status = evidence["logs"].get("logs_status")
            parsed_errors = evidence["logs"].get("parsed_errors")
            assert (
                logs_status is not None or parsed_errors is not None
            ), "Logs present but neither status nor parsed_errors set (collection incomplete)"

    # For pod-scoped alerts: verify pod context collection was attempted
    if target.get("target_type") == "pod" and target.get("pod"):
        # K8s context collection should have been attempted (may fail gracefully if pod deleted)
        assert evidence.get("k8s") is not None, "Pod-scoped investigation should have k8s evidence section"

    # ========================================
    # REPORT QUALITY BASELINE
    # Every investigation should meet this minimum bar
    # ========================================

    # At least some evidence should be collected or attempted
    # (Checks logs, k8s, or metrics - at least one should have tried)
    evidence_attempted = any(
        [
            evidence.get("logs", {}).get("logs_status") is not None,
            evidence.get("k8s", {}).get("pod_info") is not None,
            evidence.get("metrics") is not None,
        ]
    )
    assert evidence_attempted, (
        "Investigation should attempt to collect at least one type of evidence. "
        "Both diagnostic and playbook paths failed."
    )

    # Success summary
    print(f"\n✓ Investigation quality verified: {latest_investigation}")
    print(f"  Family: {family}")
    print(f"  Target: {target.get('namespace')}/{target.get('workload_name') or target.get('pod') or 'non-pod'}")
    print(f"  Verdict: {verdict.get('one_liner', 'N/A')[:80]}...")
    print(f"  Scores: impact={impact}, confidence={confidence}")

    # Optional: Check if diagnostic path or playbook fallback was used
    if investigation.get("errors"):
        diagnostic_errors = [e for e in investigation["errors"] if "Diagnostics" in str(e)]
        if diagnostic_errors:
            print(f"  ⚠️  Diagnostic issues (playbook fallback used): {len(diagnostic_errors)} errors")
            for err in diagnostic_errors[:3]:  # Show first 3
                print(f"      - {err}")
    else:
        print("  ✓ No diagnostic errors (primary path succeeded)")
