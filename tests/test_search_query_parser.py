from __future__ import annotations

from agent.core.search_query import parse_search_query


def test_parse_empty() -> None:
    q = parse_search_query("")
    assert q.filters == {}
    assert q.tokens == []


def test_parse_free_text_tokens_and_semantics_is_external() -> None:
    # Parser splits into tokens; caller can apply AND semantics.
    q = parse_search_query("payments   prod")
    assert q.filters == {}
    assert q.tokens == ["payments", "prod"]


def test_parse_filters_basic() -> None:
    q = parse_search_query(
        "ns:payments pod:api-123 deploy:orders-api svc:billing cluster:prod alert:KubePodCrashLooping"
    )
    assert q.tokens == []
    assert q.filters["namespace"] == ["payments"]
    assert q.filters["pod"] == ["api-123"]
    assert q.filters["workload"] == ["orders-api"]
    assert q.filters["service"] == ["billing"]
    assert q.filters["cluster"] == ["prod"]
    assert q.filters["alertname"] == ["KubePodCrashLooping"]


def test_parse_filters_mixed_with_free_text() -> None:
    q = parse_search_query("ns:payments crashloop prod")
    assert q.filters["namespace"] == ["payments"]
    assert q.tokens == ["crashloop", "prod"]


def test_parse_repeated_key_accumulates_values() -> None:
    q = parse_search_query("ns:payments ns:checkout")
    assert q.filters["namespace"] == ["payments", "checkout"]
    assert q.tokens == []


def test_parse_is_case_insensitive_for_keys() -> None:
    q = parse_search_query("NS:payments SVC:billing ALERT:Foo")
    assert q.filters["namespace"] == ["payments"]
    assert q.filters["service"] == ["billing"]
    assert q.filters["alertname"] == ["Foo"]


def test_parse_quoted_values() -> None:
    q = parse_search_query('ns:"payments-prod" pod:"api 123"')
    assert q.filters["namespace"] == ["payments-prod"]
    assert q.filters["pod"] == ["api 123"]
    assert q.tokens == []


def test_unknown_key_becomes_token() -> None:
    q = parse_search_query("foo:bar ns:payments")
    assert q.filters["namespace"] == ["payments"]
    assert q.tokens == ["foo:bar"]
