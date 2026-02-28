#!/usr/bin/env python3
"""
Incident Investigation Agent - Alert-Driven Investigation
A minimal POC for investigating incidents from Prometheus alerts.
"""

import argparse
import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from dateutil import parser as date_parser

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", stream=sys.stderr
)

#
# NOTE: Keep agent imports lazy (inside functions) to reduce baseline RSS for modes
# that don't need them (e.g. webhook server startup).
#


def format_timestamp_for_display(timestamp_str: str) -> str:
    """Format ISO timestamp to compact display format (HH:MMZ)."""
    if not timestamp_str:
        return "N/A"
    try:
        dt = date_parser.isoparse(timestamp_str)
        return dt.strftime("%H:%MZ")
    except (ValueError, TypeError, AttributeError):
        return timestamp_str[:16]  # Fallback: first 16 chars


def extract_container_from_labels(labels: Dict[str, Any]) -> Optional[str]:
    """Extract container name from alert labels."""
    return labels.get("container") or labels.get("Container") or None


def investigate_from_alert(
    alert: Dict[str, Any],
    time_window: str,
    *,
    llm: bool = False,
    dump_json: Optional[str] = None,
) -> None:
    """
    Investigate an incident from a Prometheus alert.

    Args:
        alert: Alert dictionary from Alertmanager
        time_window: Time window string (e.g., '1h', '30m')
    """
    # Extract alert context
    import json

    from agent.dump import investigation_to_json_dict
    from agent.llm.enrich_investigation import maybe_enrich_investigation
    from agent.pipeline.pipeline import run_investigation
    from agent.providers.alertmanager_provider import extract_pod_info_from_alert, get_alert_context
    from agent.report import render_report

    alert_context = get_alert_context(alert)
    alertname = alert_context.get("alertname", "Unknown")

    # Optional JSON dump: emit ONLY JSON on stdout (suppress all human-readable prints).
    # This makes `> file.json` produce valid JSON for editor tooling.
    if dump_json:
        investigation = run_investigation(alert=alert, time_window=time_window)
        if llm:
            maybe_enrich_investigation(investigation, enabled=True)
        payload = investigation_to_json_dict(investigation, mode=dump_json)  # type: ignore[arg-type]
        print(json.dumps(payload, indent=2, sort_keys=False))
        return

    print(f"🔍 Investigating alert: {alertname}")
    print(f"📋 Severity: {alert_context.get('severity', 'unknown')}")
    if alert_context.get("summary"):
        print(f"📝 Summary: {alert_context['summary']}")
    print(f"⏰ Time window: {time_window}\n")

    # Extract pod information from alert (pod-scoped) when available, but do NOT abort for non-pod alerts.
    pod_info = extract_pod_info_from_alert(alert)
    labels = alert.get("labels", {}) if isinstance(alert, dict) else {}
    if pod_info:
        pod_name = pod_info["pod"]
        namespace = pod_info["namespace"]
        print(f"🎯 Target: Pod `{pod_name}` in namespace `{namespace}`\n")
    else:
        # Non-pod: show best-effort identity hints
        instance = labels.get("instance") if isinstance(labels, dict) else None
        service = labels.get("service") if isinstance(labels, dict) else None
        cluster = labels.get("cluster") if isinstance(labels, dict) else None
        bits = []
        if service:
            bits.append(f"service={service}")
        if instance:
            bits.append(f"instance={instance}")
        if cluster:
            bits.append(f"cluster={cluster}")
        hint = ", ".join(bits) if bits else "no target labels"
        print(f"🎯 Target: (non-pod) {hint}\n")

    print("📊 Gathering investigation data...")
    investigation = run_investigation(alert=alert, time_window=time_window)

    # Keep the existing logs status line
    if (investigation.evidence.logs.logs_status or "") == "unavailable":
        print("Logs: unavailable")
    else:
        print(f"Logs: {len(investigation.evidence.logs.logs or [])} entries")

    # Optional LLM enrichment (additive; default off)
    if llm:
        maybe_enrich_investigation(investigation, enabled=True)

    report = render_report(investigation)

    print("\n" + "=" * 80)
    print(report)
    print("=" * 80)


def list_alerts(alertname_filter: Optional[str] = None, severity_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List active alerts from Alertmanager in indexed format.

    Args:
        alertname_filter: Optional filter by alert name
        severity_filter: Optional filter by severity

    Returns:
        Sorted list of alerts (most recent first) for use by investigate_by_index
    """
    try:
        from agent.providers.alertmanager_provider import extract_pod_info_from_alert, fetch_active_alerts

        alerts = fetch_active_alerts(alertname=alertname_filter, severity=severity_filter)

        if not alerts:
            print("✅ No active alerts found")
            if alertname_filter or severity_filter:
                print(f"   (with filters: alertname={alertname_filter}, severity={severity_filter})")
            return []

        # Sort by start time (most recent first)
        def get_start_time(alert):
            starts_at = alert.get("starts_at", "")
            if starts_at:
                try:
                    return date_parser.isoparse(starts_at)
                except (ValueError, TypeError, AttributeError):
                    return datetime.min
            return datetime.min

        alerts_sorted = sorted(alerts, key=get_start_time, reverse=True)

        print(f"📊 Found {len(alerts_sorted)} active alert(s) (sorted by most recent first):\n")

        for i, alert in enumerate(alerts_sorted):
            labels = alert.get("labels", {})
            alertname = labels.get("alertname", "Unknown")
            fingerprint = alert.get("fingerprint", "N/A")

            # Extract key identifying information
            pod_info = extract_pod_info_from_alert(alert)
            container = extract_container_from_labels(labels)
            since = format_timestamp_for_display(alert.get("starts_at", ""))

            # Build compact display line
            parts = [f"[{i}] {alertname}"]
            parts.append(f"fp={fingerprint[:8]}...")

            if pod_info:
                parts.append(f"ns={pod_info['namespace']}")
                parts.append(f"pod={pod_info['pod']}")
            else:
                # Fallback: show namespace if available
                ns = labels.get("namespace") or labels.get("Namespace")
                if ns:
                    parts.append(f"ns={ns}")

            if container:
                parts.append(f"container={container}")

            parts.append(f"since={since}")

            print("  ".join(parts))

        print("\n💡 Investigate with: `python main.py --alert <index>` or `--fingerprint <fp>`")

        return alerts_sorted

    except Exception as e:
        print(f"❌ Error fetching alerts: {e}", file=sys.stderr)
        raise


def investigate_by_index(index: int, time_window: str, *, llm: bool = False, dump_json: Optional[str] = None) -> None:
    """
    Investigate an alert by its index from --list-alerts.

    Args:
        index: Alert index (0-based)
        time_window: Time window string
    """
    from agent.providers.alertmanager_provider import fetch_active_alerts

    alerts = fetch_active_alerts()

    if not alerts:
        print("❌ No active alerts found")
        print("   Use `--list-alerts` to see available alerts")
        return

    # Sort the same way as list_alerts (most recent first)
    def get_start_time(alert):
        starts_at = alert.get("starts_at", "")
        if starts_at:
            try:
                return date_parser.isoparse(starts_at)
            except (ValueError, TypeError, AttributeError):
                return datetime.min
        return datetime.min

    alerts_sorted = sorted(alerts, key=get_start_time, reverse=True)

    if index < 0 or index >= len(alerts_sorted):
        print(f"❌ Invalid alert index: {index}")
        print(f"   Valid range: 0-{len(alerts_sorted) - 1}")
        print("   Use `--list-alerts` to see available alerts")
        return

    alert = alerts_sorted[index]
    investigate_from_alert(alert, time_window, llm=llm, dump_json=dump_json)


def investigate_by_fingerprint(
    fingerprint: str, time_window: str, *, llm: bool = False, dump_json: Optional[str] = None
) -> None:
    """
    Investigate an alert by its fingerprint.

    Args:
        fingerprint: Alert fingerprint from Alertmanager
        time_window: Time window string
    """
    from agent.providers.alertmanager_provider import fetch_active_alerts

    alerts = fetch_active_alerts()

    fp = (fingerprint or "").strip()
    if fp.endswith("..."):
        fp = fp[:-3]

    # Find alert by fingerprint (allow prefix match since list view truncates)
    alert = None
    for a in alerts:
        afp = a.get("fingerprint") or ""
        if afp == fp or afp.startswith(fp):
            alert = a
            break

    if not alert:
        print(f"❌ Alert with fingerprint '{fingerprint}' not found")
        print("   Use `--list-alerts` to see available alerts")
        return

    investigate_from_alert(alert, time_window, llm=llm, dump_json=dump_json)


def investigate_by_name(
    alertname: str, time_window: str, *, llm: bool = False, dump_json: Optional[str] = None
) -> None:
    """
    Investigate the most recent matching alert by name.

    Args:
        alertname: Alert name
        time_window: Time window string
    """
    from agent.providers.alertmanager_provider import fetch_active_alerts

    alerts = fetch_active_alerts(alertname=alertname)

    if not alerts:
        print(f"❌ No active alerts found with name '{alertname}'")
        print("   Use `--list-alerts` to see available alerts")
        return

    # Sort by start time (most recent first)
    # Parse ISO format timestamps and sort descending
    def get_start_time(alert):
        starts_at = alert.get("starts_at", "")
        if starts_at:
            try:
                # Parse ISO format: "2024-01-01T12:00:00Z" or similar (RFC3339)
                return date_parser.isoparse(starts_at)
            except (ValueError, TypeError, AttributeError):
                return datetime.min
        return datetime.min

    alerts_sorted = sorted(alerts, key=get_start_time, reverse=True)
    selected_alert = alerts_sorted[0]

    if len(alerts) > 1:
        print(f"⚠️ Found {len(alerts)} alerts with name '{alertname}', investigating the most recent one")
        print(f"   Started: {selected_alert.get('starts_at', 'Unknown')}")
        print("   Use `--fingerprint` to investigate a specific alert\n")

    investigate_from_alert(selected_alert, time_window, llm=llm, dump_json=dump_json)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Investigate incidents from Prometheus alerts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all active alerts (shows indexed list)
  python main.py --list-alerts

  # Investigate alert by index
  python main.py --alert 0

  # Investigate alert by fingerprint
  python main.py --fingerprint ab12cd34...
        """,
    )

    # Alert-based investigation
    parser.add_argument(
        "--list-alerts", action="store_true", help="List all active alerts from Alertmanager with indices"
    )
    parser.add_argument(
        "--serve-webhook",
        action="store_true",
        help="Run an HTTP server to receive Alertmanager webhook notifications (in-cluster)",
    )
    parser.add_argument(
        "--run-job",
        action="store_true",
        help="Run a single investigation job from JSON (stdin by default). Phase 1 dev helper.",
    )
    parser.add_argument(
        "--job-file",
        help="Path to a JSON file containing an AlertJob payload (used with --run-job). If omitted, reads stdin.",
    )
    parser.add_argument(
        "--run-worker",
        action="store_true",
        help="Run JetStream worker loop (Phase 3). Consumes AlertJob messages and runs investigations.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Webhook server bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Webhook server listen port (default: 8080)")
    parser.add_argument(
        "--alert", type=int, metavar="INDEX", help="Investigate alert by index from --list-alerts (e.g., --alert 0)"
    )
    parser.add_argument(
        "--fingerprint", metavar="FP", help="Investigate alert by fingerprint (e.g., --fingerprint ab12cd34...)"
    )

    # Common options
    parser.add_argument(
        "--time-window",
        "-t",
        default="1h",
        help="Time window for investigation (e.g., '1h', '30m', '2h30m') (default: 1h)",
    )
    parser.add_argument(
        "--llm", action="store_true", help="Enable optional LLM enrichment for the investigation (default: off)"
    )
    parser.add_argument(
        "--dump-json",
        nargs="?",
        const="analysis",
        choices=["analysis", "investigation"],
        help="Print investigation JSON to stdout instead of the markdown report (default: analysis). Use `investigation` for full raw investigation.",
    )
    parser.add_argument("--severity", help="Filter alerts by severity (for --list-alerts)")

    args = parser.parse_args()

    try:
        # Alert-based modes
        if args.list_alerts:
            list_alerts(severity_filter=args.severity)
            return

        if args.serve_webhook:
            from agent.api.webhook import run as run_webhook

            run_webhook(host=args.host, port=args.port)
            return

        if args.run_job:
            import json

            from agent.api.worker import load_job, run_job_from_env

            if args.job_file:
                with open(args.job_file, "r", encoding="utf-8") as f:
                    payload = f.read()
            else:
                payload = sys.stdin.read()

            job = load_job(payload)
            stats, created = run_job_from_env(job)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "received": getattr(stats, "received", None),
                        "processed_firing": getattr(stats, "processed_firing", None),
                        "stored_new": getattr(stats, "stored_new", None),
                        "errors": getattr(stats, "errors", None),
                        "created_keys": created[:10],
                    },
                    indent=2,
                    sort_keys=False,
                )
            )
            return

        if args.run_worker:
            import asyncio

            from agent.api.worker_jetstream import run_worker_forever

            asyncio.run(run_worker_forever())
            return

        if args.alert is not None:
            investigate_by_index(args.alert, args.time_window, llm=args.llm, dump_json=args.dump_json)
            return

        if args.fingerprint:
            investigate_by_fingerprint(args.fingerprint, args.time_window, llm=args.llm, dump_json=args.dump_json)
            return

        # No arguments provided
        parser.print_help()
        print("\n💡 Tip: Use `--list-alerts` to see available alerts")

    except Exception as e:
        print(f"❌ Error during investigation: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
