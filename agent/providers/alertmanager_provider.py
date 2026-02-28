"""Alertmanager client for fetching active alerts."""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import requests

# Default Alertmanager URL (can be overridden via environment variable)
ALERTMANAGER_URL = "http://localhost:19093"


@runtime_checkable
class AlertmanagerProvider(Protocol):
    def fetch_active_alerts(
        self, alertname: Optional[str] = None, severity: Optional[str] = None
    ) -> List[Dict[str, Any]]: ...

    def extract_pod_info_from_alert(self, alert: Dict[str, Any]) -> Optional[Dict[str, str]]: ...

    def get_alert_context(self, alert: Dict[str, Any]) -> Dict[str, Any]: ...


class DefaultAlertmanagerProvider:
    def fetch_active_alerts(
        self, alertname: Optional[str] = None, severity: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return fetch_active_alerts(alertname=alertname, severity=severity)

    def extract_pod_info_from_alert(self, alert: Dict[str, Any]) -> Optional[Dict[str, str]]:
        return extract_pod_info_from_alert(alert)

    def get_alert_context(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        return get_alert_context(alert)


def get_alertmanager_provider() -> AlertmanagerProvider:
    """Seam for swapping provider implementations later (e.g., MCP-backed)."""
    return DefaultAlertmanagerProvider()


def fetch_active_alerts(alertname: Optional[str] = None, severity: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetch active alerts from Alertmanager.

    Args:
        alertname: Optional filter by alert name
        severity: Optional filter by severity (warning, critical, etc.)

    Returns:
        List of active alerts with labels, annotations, and status
    """
    import os

    alertmanager_url = os.getenv("ALERTMANAGER_URL", ALERTMANAGER_URL)

    # Alertmanager API v2 endpoint for active alerts
    url = f"{alertmanager_url}/api/v2/alerts"

    params = {
        "active": "true",  # Only get active alerts
        "silenced": "false",  # Exclude silenced alerts
        "inhibited": "false",  # Exclude inhibited alerts
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        alerts = response.json()

        # Filter alerts if filters provided
        filtered_alerts = []
        for alert in alerts:
            labels = alert.get("labels", {})

            # Apply filters
            if alertname and labels.get("alertname") != alertname:
                continue
            if severity and labels.get("severity") != severity:
                continue

            # Parse alert structure
            alert_data = {
                "fingerprint": alert.get("fingerprint"),
                "labels": labels,
                "annotations": alert.get("annotations", {}),
                "starts_at": alert.get("startsAt"),
                "ends_at": alert.get("endsAt"),
                "generator_url": alert.get("generatorURL", ""),
                "status": alert.get("status", {}),
            }

            filtered_alerts.append(alert_data)

        return filtered_alerts

    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to fetch alerts from Alertmanager: {str(e)}")


def extract_pod_info_from_alert(alert: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Extract pod name and namespace from alert labels.

    Common label patterns:
    - pod, namespace (direct)
    - pod_name, namespace
    - instance (may contain pod info)

    Args:
        alert: Alert dictionary with labels

    Returns:
        Dictionary with 'pod' and 'namespace' keys, or None if not found
    """
    labels = alert.get("labels", {})

    # Try common label patterns (safe: explicitly pod-scoped labels)
    pod_name = (
        labels.get("pod")
        or labels.get("pod_name")
        or labels.get("podName")
        or labels.get("kubernetes_pod_name")
        or labels.get("pod_name")
        or None
    )

    namespace = (
        labels.get("namespace")
        or labels.get("Namespace")
        or labels.get("kubernetes_namespace_name")
        or labels.get("k8s_namespace")
        or labels.get("kube_namespace")
        or None
    )

    if pod_name and namespace:
        return {"pod": pod_name, "namespace": namespace}

    # IMPORTANT: Do NOT infer pod/namespace from `instance` by default.
    # Many environments use node/service DNS names (e.g. ip-1-2-3-4.ec2.internal:9100),
    # and guessing here can create dangerously misleading investigations.
    #
    # If you want instance-based mapping, prefer adding explicit `pod` and `namespace`
    # labels to the alert in Prometheus/Alertmanager rule templates.
    return None


def get_alert_context(alert: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract context information from an alert for investigation.

    Args:
        alert: Alert dictionary

    Returns:
        Context dictionary with alert name, severity, description, etc.
    """
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    return {
        "alertname": labels.get("alertname", "Unknown"),
        "severity": labels.get("severity", "unknown"),
        "summary": annotations.get("summary", ""),
        "description": annotations.get("description", ""),
        "runbook_url": annotations.get("runbook_url", ""),
        "starts_at": alert.get("starts_at"),
        "all_labels": labels,
        "all_annotations": annotations,
    }
