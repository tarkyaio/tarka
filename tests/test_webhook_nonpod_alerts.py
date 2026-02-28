from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import agent.api.webhook as ws
from agent.core.models import AlertInstance, Analysis, Investigation, TargetRef, TimeWindow


class _FakeStorage:
    def __init__(self) -> None:
        self.markdown_put: List[tuple[str, str]] = []
        self.json_put: List[tuple[str, Any]] = []

    def exists(self, rel_key: str) -> bool:  # noqa: D401
        return False

    def put_markdown(self, rel_key: str, body: str) -> None:
        self.markdown_put.append((rel_key, body))

    def put_json(self, rel_key: str, obj: Any) -> None:
        self.json_put.append((rel_key, obj))

    def key(self, rel_key: str) -> str:
        return rel_key


def test_webhook_does_not_skip_non_pod_alert(monkeypatch) -> None:
    now = datetime.now(timezone.utc)

    def _fake_run_investigation(*, alert: Dict[str, Any], time_window: str) -> Investigation:
        return Investigation(
            alert=AlertInstance(
                fingerprint=str(alert.get("fingerprint") or "fp"),
                labels=dict(alert.get("labels") or {}),
                annotations=dict(alert.get("annotations") or {}),
                starts_at=alert.get("starts_at"),
                ends_at=alert.get("ends_at"),
                generator_url=alert.get("generator_url"),
                state=(alert.get("status") or {}).get("state"),
                normalized_state="firing",
                ends_at_kind="expires_at",
            ),
            time_window=TimeWindow(window=time_window, start_time=now, end_time=now),
            target=TargetRef(target_type="unknown"),
            analysis=Analysis(),
        )

    def _fake_render_report(investigation: Investigation, *, generated_at: Optional[datetime] = None) -> str:
        return "ok"

    monkeypatch.setattr(ws, "run_investigation", _fake_run_investigation)
    monkeypatch.setattr(ws, "render_report", _fake_render_report)

    storage = _FakeStorage()
    raw_alert = {
        "labels": {"alertname": "NonPodAlert", "severity": "info"},
        "annotations": {"summary": "x"},
        "startsAt": now.isoformat(),
        "endsAt": None,
        "status": {"state": "firing"},
        "fingerprint": "fp-nonpod",
    }

    stats, created = ws.process_alerts(
        [raw_alert],
        time_window="15m",
        storage=storage,  # type: ignore[arg-type]
        allowlist=None,
        parent_status=None,
    )

    assert stats.processed_firing == 1
    assert stats.stored_new == 1
    assert created
    assert storage.markdown_put
