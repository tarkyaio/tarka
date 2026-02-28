from datetime import datetime, timedelta


def test_analyze_changes_sets_change_correlation() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow
    from agent.pipeline.changes import analyze_changes

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)

    investigation = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "X"}, annotations={}),
        time_window=tw,
        target={"namespace": "prod", "pod": "p1", "playbook": "default"},
        evidence={
            "k8s": {
                "rollout_status": {
                    "kind": "Deployment",
                    "name": "demo-api",
                    "creation_timestamp": (end - timedelta(days=3)).isoformat(),
                    "conditions": [
                        {
                            "type": "Available",
                            "status": "True",
                            "reason": "MinimumReplicasAvailable",
                            "message": "ok",
                            "last_update_time": end.isoformat(),
                        }
                    ],
                    "images": [{"name": "app", "image": "demo-api:1.2.3"}],
                }
            }
        },
    )

    analyze_changes(investigation)

    assert investigation.analysis.change is not None
    assert investigation.analysis.change.score is not None
    assert investigation.analysis.change.summary is not None
    assert investigation.analysis.change.timeline is not None
