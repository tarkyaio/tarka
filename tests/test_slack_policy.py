"""Unit tests for Slack provider configuration and policy loading from env."""

from __future__ import annotations


def test_provider_returns_none_without_token(monkeypatch) -> None:
    from agent.slack.provider import get_slack_provider, set_slack_provider

    # Reset singleton
    set_slack_provider(None)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    provider = get_slack_provider()
    assert provider is None


def test_set_provider_overrides_singleton() -> None:
    from agent.slack.provider import get_slack_provider, set_slack_provider

    class _FakeProvider:
        def post_message(self, **kwargs):
            return {"ok": True}

        def update_message(self, **kwargs):
            return {"ok": True}

        def get_user_info(self, **kwargs):
            return {"ok": True}

        def add_reaction(self, **kwargs):
            pass

        def remove_reaction(self, **kwargs):
            pass

    fake = _FakeProvider()
    set_slack_provider(fake)
    assert get_slack_provider() is fake

    # Cleanup
    set_slack_provider(None)


def test_notifier_should_notify_filters_correctly() -> None:
    from datetime import datetime, timezone

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
    from agent.slack.notifier import _should_notify

    now = datetime.now(timezone.utc)

    def _inv(classification: str) -> Investigation:
        return Investigation(
            alert=AlertInstance(fingerprint="fp", labels={"alertname": "X"}, annotations={}),
            time_window=TimeWindow(window="1h", start_time=now, end_time=now),
            target=TargetRef(),
            evidence=Evidence(),
            analysis=Analysis(
                verdict=DeterministicVerdict(
                    classification=classification,
                    primary_driver="test",
                    one_liner="test",
                ),
                scores=DeterministicScores(impact_score=50, confidence_score=70, noise_score=10),
            ),
        )

    assert _should_notify(_inv("actionable")) is True
    assert _should_notify(_inv("informational")) is True
    assert _should_notify(_inv("noisy")) is False
    assert _should_notify(_inv("artifact")) is False


def test_notifier_should_notify_false_without_verdict() -> None:
    from datetime import datetime, timezone

    from agent.core.models import (
        AlertInstance,
        Analysis,
        Evidence,
        Investigation,
        TargetRef,
        TimeWindow,
    )
    from agent.slack.notifier import _should_notify

    now = datetime.now(timezone.utc)
    inv = Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "X"}, annotations={}),
        time_window=TimeWindow(window="1h", start_time=now, end_time=now),
        target=TargetRef(),
        evidence=Evidence(),
        analysis=Analysis(),
    )
    assert _should_notify(inv) is False
