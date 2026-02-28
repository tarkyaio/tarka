from __future__ import annotations

from types import SimpleNamespace

from agent.api.worker_jetstream import decide_disposition, should_retry_from_stats


def test_should_retry_from_stats_true_on_errors() -> None:
    stats = SimpleNamespace(errors=1)
    assert should_retry_from_stats(stats) is True


def test_should_retry_from_stats_false_on_no_errors() -> None:
    stats = SimpleNamespace(errors=0)
    assert should_retry_from_stats(stats) is False


def test_should_retry_from_stats_conservative_on_weird_stats() -> None:
    class Weird:
        errors = "NaN"

    assert should_retry_from_stats(Weird()) is True


def test_decide_disposition_ack_on_success() -> None:
    assert decide_disposition(errors=0, delivery_count=1, max_deliver=5) == "ack"


def test_decide_disposition_nak_on_transient_failure() -> None:
    assert decide_disposition(errors=1, delivery_count=1, max_deliver=5) == "nak"


def test_decide_disposition_dlq_on_final_attempt() -> None:
    assert decide_disposition(errors=1, delivery_count=5, max_deliver=5) == "dlq"
