def test_instance_ec2_internal_is_not_parsed_as_pod() -> None:
    from agent.providers.alertmanager_provider import extract_pod_info_from_alert

    alert = {"labels": {"instance": "ip-172-29-62-243.ec2.internal:9100", "alertname": "InfoInhibitor"}}
    assert extract_pod_info_from_alert(alert) is None


def test_explicit_pod_namespace_are_used() -> None:
    from agent.providers.alertmanager_provider import extract_pod_info_from_alert

    alert = {"labels": {"pod": "p1", "namespace": "ns1", "alertname": "A"}}
    assert extract_pod_info_from_alert(alert) == {"pod": "p1", "namespace": "ns1"}
