from datetime import datetime, timedelta


def _base_investigation():
    from agent.core.models import AlertInstance, Investigation, TimeWindow

    end = datetime(2025, 1, 1, 0, 0, 0)
    start = end - timedelta(hours=1)
    tw = TimeWindow(window="1h", start_time=start, end_time=end)
    return Investigation(
        alert=AlertInstance(fingerprint="fp", labels={"alertname": "A"}, annotations={}),
        time_window=tw,
        target={"namespace": "ns1", "pod": "p1", "playbook": "default"},
    )


def test_llm_disabled_by_default_does_not_render_section() -> None:
    from agent.report import render_report

    investigation = _base_investigation()
    md = render_report(investigation, generated_at=datetime(2025, 1, 1, 0, 0, 0))
    assert "## LLM Insights" not in md


def test_llm_ok_adds_section(monkeypatch) -> None:
    from agent.llm.enrich_investigation import maybe_enrich_investigation
    from agent.report import render_report

    investigation = _base_investigation()

    # Stub provider call
    import agent.llm.client as llm_client

    monkeypatch.setattr(llm_client, "generate_json", lambda _p, schema=None: ({"summary": "ok"}, None))

    maybe_enrich_investigation(investigation, enabled=True)
    assert investigation.analysis.llm is not None
    assert investigation.analysis.llm.status == "ok"

    md = render_report(investigation, generated_at=datetime(2025, 1, 1, 0, 0, 0))
    assert "## LLM Insights" in md


def test_llm_rate_limited_sets_status(monkeypatch) -> None:
    from agent.llm.enrich_investigation import maybe_enrich_investigation

    investigation = _base_investigation()

    import agent.llm.client as llm_client

    monkeypatch.setattr(llm_client, "generate_json", lambda _p, schema=None: (None, "rate_limited"))

    maybe_enrich_investigation(investigation, enabled=True)
    assert investigation.analysis.llm is not None
    assert investigation.analysis.llm.status == "rate_limited"
