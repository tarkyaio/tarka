"""
Tests for logs.tail tool support for Kubernetes Jobs.

Verifies that the logs.tail tool can automatically find pods created by a Job
and fetch logs from them, without requiring an explicit pod name.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def test_logs_tail_with_job_workload():
    """logs.tail should automatically find pods for Job workloads."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(
        enabled=True,
        allow_promql=False,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=False,
        allow_report_rerun=False,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
        max_log_lines=100,
        namespace_allowlist=None,
        cluster_allowlist=None,
    )

    analysis_json = {
        "target": {
            "kind": "Job",
            "workload": "my-job",
            "namespace": "default",
            "pod": None,  # No pod name for Jobs
        }
    }

    args = {
        "namespace": "default",
        # No pod specified - should be auto-resolved from Job
    }

    # Mock Kubernetes to return a pod created by the Job
    with patch("agent.chat.tools.get_k8s_provider") as mock_k8s_provider:
        mock_k8s = MagicMock()
        mock_k8s.list_pods.return_value = [
            {
                "metadata": {
                    "name": "my-job-abc123",
                    "creationTimestamp": "2024-01-01T00:00:00Z",
                }
            }
        ]
        mock_k8s_provider.return_value = mock_k8s

        # Mock logs provider
        with patch("agent.chat.tools.fetch_recent_logs") as mock_logs:
            mock_logs.return_value = {
                "status": "ok",
                "entries": [
                    {"timestamp": datetime.now(timezone.utc), "message": "Job started"},
                    {"timestamp": datetime.now(timezone.utc), "message": "Job failed"},
                ],
                "backend": "loki",
            }

            result = run_tool(
                policy=policy,
                action_policy=None,
                tool="logs.tail",
                args=args,
                analysis_json=analysis_json,
                case_id="test",
                run_id="test-run",
            )

            # Should succeed
            assert result.ok is True
            assert result.error is None

            # Should have queried K8s for Job pods
            mock_k8s.list_pods.assert_called_once_with(namespace="default", label_selector="job-name=my-job")

            # Should have fetched logs from the resolved pod
            mock_logs.assert_called_once()
            call_args = mock_logs.call_args
            assert call_args[0][0] == "my-job-abc123"  # pod name
            assert call_args[0][1] == "default"  # namespace


def test_logs_tail_with_job_no_pods_found():
    """logs.tail should return error if Job has no pods."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(
        enabled=True,
        allow_promql=False,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=False,
        allow_report_rerun=False,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
        max_log_lines=100,
        namespace_allowlist=None,
        cluster_allowlist=None,
    )

    analysis_json = {
        "target": {
            "kind": "Job",
            "workload": "my-job",
            "namespace": "default",
            "pod": None,
        }
    }

    args = {"namespace": "default"}

    # Mock Kubernetes to return no pods
    with patch("agent.chat.tools.get_k8s_provider") as mock_k8s_provider:
        mock_k8s = MagicMock()
        mock_k8s.list_pods.return_value = []  # No pods found
        mock_k8s_provider.return_value = mock_k8s

        result = run_tool(
            policy=policy,
            action_policy=None,
            tool="logs.tail",
            args=args,
            analysis_json=analysis_json,
            case_id="test",
            run_id="test-run",
        )

        # Should fail with missing_required_args (no pod found for Job)
        assert result.ok is False
        assert result.error == "missing_required_args:pod_name"


def test_logs_tail_with_job_multiple_pods():
    """logs.tail should use the most recent pod when Job has multiple pods."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(
        enabled=True,
        allow_promql=False,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=False,
        allow_report_rerun=False,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
        max_log_lines=100,
        namespace_allowlist=None,
        cluster_allowlist=None,
    )

    analysis_json = {
        "target": {
            "kind": "Job",
            "workload": "my-job",
            "namespace": "default",
            "pod": None,
        }
    }

    args = {"namespace": "default"}

    # Mock Kubernetes to return multiple pods (Job retried)
    with patch("agent.chat.tools.get_k8s_provider") as mock_k8s_provider:
        mock_k8s = MagicMock()
        mock_k8s.list_pods.return_value = [
            {
                "metadata": {
                    "name": "my-job-old-pod",
                    "creationTimestamp": "2024-01-01T00:00:00Z",
                }
            },
            {
                "metadata": {
                    "name": "my-job-new-pod",
                    "creationTimestamp": "2024-01-01T01:00:00Z",
                }
            },
        ]
        mock_k8s_provider.return_value = mock_k8s

        with patch("agent.chat.tools.fetch_recent_logs") as mock_logs:
            mock_logs.return_value = {
                "status": "ok",
                "entries": [],
                "backend": "loki",
            }

            _ = run_tool(
                policy=policy,
                action_policy=None,
                tool="logs.tail",
                args=args,
                analysis_json=analysis_json,
                case_id="test",
                run_id="test-run",
            )

            # Should use the most recent pod
            mock_logs.assert_called_once()
            call_args = mock_logs.call_args
            assert call_args[0][0] == "my-job-new-pod"


def test_logs_tail_with_regular_pod_still_works():
    """logs.tail should still work normally for regular pods."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(
        enabled=True,
        allow_promql=False,
        allow_k8s_read=True,
        allow_logs_query=True,
        allow_memory_read=False,
        allow_report_rerun=False,
        allow_argocd_read=False,
        redact_secrets=False,
        max_steps=3,
        max_tool_calls=5,
        max_log_lines=100,
        namespace_allowlist=None,
        cluster_allowlist=None,
    )

    analysis_json = {
        "target": {
            "kind": "Deployment",
            "workload": "my-app",
            "namespace": "default",
            "pod": "my-app-abc123",
        }
    }

    args = {
        "pod": "my-app-abc123",
        "namespace": "default",
    }

    # Should NOT query K8s for pods (pod name already provided)
    with patch("agent.chat.tools.get_k8s_provider") as mock_k8s_provider:
        with patch("agent.chat.tools.fetch_recent_logs") as mock_logs:
            mock_logs.return_value = {
                "status": "ok",
                "entries": [],
                "backend": "loki",
            }

            result = run_tool(
                policy=policy,
                action_policy=None,
                tool="logs.tail",
                args=args,
                analysis_json=analysis_json,
                case_id="test",
                run_id="test-run",
            )

            # Should succeed without querying K8s
            assert result.ok is True
            mock_k8s_provider.assert_not_called()

            # Should fetch logs directly
            mock_logs.assert_called_once()
            call_args = mock_logs.call_args
            assert call_args[0][0] == "my-app-abc123"
