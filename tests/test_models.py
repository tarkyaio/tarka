from datetime import datetime, timedelta


def test_investigation_model_parses_minimal() -> None:
    from agent.core.models import AlertInstance, Investigation, TimeWindow

    now = datetime.now()
    tw = TimeWindow(window="1h", start_time=now - timedelta(hours=1), end_time=now)

    b = Investigation(
        alert=AlertInstance(fingerprint="fp1", labels={"alertname": "X"}, annotations={}),
        time_window=tw,
        meta={"source": "test"},
    )

    d = b.model_dump()
    assert d["alert"]["fingerprint"] == "fp1"
    assert d["time_window"]["window"] == "1h"
    assert d["meta"]["source"] == "test"
