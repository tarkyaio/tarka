"""Unit tests for Slack notifier — channel routing and notification filtering."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agent.core.models import (
    AlertInstance,
    Analysis,
    DeterministicScores,
    DeterministicVerdict,
    Evidence,
    Investigation,
    TargetRef,
    TimeWindow,
)
from agent.slack.provider import set_slack_provider


class _MockSlackProvider:
    """In-memory Slack provider for testing."""

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        msg = {"channel": channel, "text": text, "blocks": blocks, "thread_ts": thread_ts, "ts": "1234567890.123456"}
        self.messages.append(msg)
        return {"ok": True, "ts": "1234567890.123456", "channel": channel}

    def update_message(
        self, *, channel: str, ts: str, text: str, blocks: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        return {"ok": True}

    def get_user_info(self, *, user_id: str) -> Dict[str, Any]:
        return {"ok": True, "user": {"id": user_id, "name": "test"}}

    def add_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        pass

    def remove_reaction(self, *, channel: str, timestamp: str, name: str) -> None:
        pass


def _make_investigation(
    *,
    classification: str = "actionable",
    severity: str = "critical",
    labels: Optional[Dict[str, Any]] = None,
) -> Investigation:
    now = datetime.now(timezone.utc)
    default_labels: Dict[str, Any] = {"alertname": "TestAlert", "namespace": "prod", "severity": severity}
    if labels:
        default_labels.update(labels)
    return Investigation(
        alert=AlertInstance(fingerprint="fp1", labels=default_labels, annotations={}),
        time_window=TimeWindow(window="1h", start_time=now, end_time=now),
        target=TargetRef(namespace="prod"),
        evidence=Evidence(),
        analysis=Analysis(
            verdict=DeterministicVerdict(
                classification=classification,
                severity=severity,
                primary_driver="test_driver",
                one_liner="Test summary",
            ),
            scores=DeterministicScores(impact_score=50, confidence_score=70, noise_score=10),
        ),
    )


def test_notifier_sends_actionable(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#sre-alerts")

    inv = _make_investigation(classification="actionable")
    ts = notify_investigation_complete(investigation=inv)

    assert ts == "1234567890.123456"
    assert len(mock.messages) == 1
    assert mock.messages[0]["channel"] == "#sre-alerts"

    # Cleanup
    set_slack_provider(None)


def test_notifier_sends_informational(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#sre-alerts")

    inv = _make_investigation(classification="informational")
    ts = notify_investigation_complete(investigation=inv)

    assert ts is not None
    assert len(mock.messages) == 1

    set_slack_provider(None)


def test_notifier_skips_noisy(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#sre-alerts")

    inv = _make_investigation(classification="noisy")
    ts = notify_investigation_complete(investigation=inv)

    assert ts is None
    assert len(mock.messages) == 0

    set_slack_provider(None)


def test_notifier_skips_artifact(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#sre-alerts")

    inv = _make_investigation(classification="artifact")
    ts = notify_investigation_complete(investigation=inv)

    assert ts is None
    assert len(mock.messages) == 0

    set_slack_provider(None)


def test_notifier_uses_alert_label_channel(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#default")

    inv = _make_investigation(labels={"slack_channel": "#team-payments"})
    ts = notify_investigation_complete(investigation=inv)

    assert ts is not None
    assert mock.messages[0]["channel"] == "#team-payments"

    set_slack_provider(None)


def test_notifier_falls_back_to_default_channel(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#fallback")

    inv = _make_investigation()
    ts = notify_investigation_complete(investigation=inv)

    assert ts is not None
    assert mock.messages[0]["channel"] == "#fallback"

    set_slack_provider(None)


def test_notifier_skips_when_no_channel(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.delenv("SLACK_DEFAULT_CHANNEL", raising=False)

    inv = _make_investigation()
    ts = notify_investigation_complete(investigation=inv)

    assert ts is None
    assert len(mock.messages) == 0

    set_slack_provider(None)


def test_notifier_skips_when_no_provider(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    set_slack_provider(None)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#sre-alerts")

    inv = _make_investigation()
    ts = notify_investigation_complete(investigation=inv)

    assert ts is None


def test_notifier_never_raises(monkeypatch) -> None:
    """Notifier is best-effort — must not raise even if provider throws."""
    from agent.slack.notifier import notify_investigation_complete

    class _ExplodingProvider:
        def post_message(self, **kwargs):
            raise RuntimeError("Boom!")

        def update_message(self, **kwargs):
            raise RuntimeError("Boom!")

        def get_user_info(self, **kwargs):
            raise RuntimeError("Boom!")

        def add_reaction(self, **kwargs):
            raise RuntimeError("Boom!")

        def remove_reaction(self, **kwargs):
            raise RuntimeError("Boom!")

    set_slack_provider(_ExplodingProvider())
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#sre-alerts")

    inv = _make_investigation()
    # Must not raise
    ts = notify_investigation_complete(investigation=inv)
    assert ts is None

    set_slack_provider(None)


def test_notifier_includes_report_url(monkeypatch) -> None:
    from agent.slack.notifier import notify_investigation_complete

    mock = _MockSlackProvider()
    set_slack_provider(mock)
    monkeypatch.setenv("SLACK_DEFAULT_CHANNEL", "#sre-alerts")

    inv = _make_investigation()
    notify_investigation_complete(
        investigation=inv,
        report_url="https://tarka.example.com/cases/123",
        case_id="123",
    )

    assert len(mock.messages) == 1
    # Blocks should contain the report URL
    blocks = mock.messages[0]["blocks"]
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 1

    set_slack_provider(None)
