"""Tests for IRSA (IAM Roles for Service Accounts) detection in aws.iam_role_permissions tool."""

from unittest.mock import MagicMock, patch


def test_iam_role_permissions_detects_missing_irsa_annotation():
    """Tool should return specific error when service account has no IAM role annotation."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(allow_aws_read=True)
    analysis_json = {"target": {"namespace": "test-ns"}}

    # Mock get_service_account_info to return service account without IAM annotation
    with patch("agent.providers.k8s_provider.get_service_account_info") as mock_sa_info:
        mock_sa_info.return_value = {
            "name": "test-sa",
            "namespace": "test-ns",
            "annotations": {},  # No IAM role annotation
        }

        result = run_tool(
            policy=policy,
            action_policy=None,
            tool="aws.iam_role_permissions",
            args={"service_account": "test-sa", "namespace": "test-ns"},
            analysis_json=analysis_json,
        )

        assert result.ok is False
        assert result.error == "no_iam_role_annotation"
        assert result.result is not None
        assert "IRSA not configured" in result.result["message"]
        assert "test-sa" in result.result["message"]


def test_iam_role_permissions_extracts_role_from_eks_annotation():
    """Tool should extract role name from eks.amazonaws.com/role-arn annotation."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(allow_aws_read=True)
    analysis_json = {"target": {"namespace": "test-ns"}}

    # Mock get_service_account_info to return service account with IRSA annotation
    with patch("agent.providers.k8s_provider.get_service_account_info") as mock_sa_info:
        mock_sa_info.return_value = {
            "name": "test-sa",
            "namespace": "test-ns",
            "annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/my-app-role"},
        }

        # Mock AWS provider
        with patch("agent.providers.aws_provider.get_aws_provider") as mock_aws:
            mock_aws_instance = MagicMock()
            mock_aws_instance.get_iam_role_permissions.return_value = {
                "role_name": "my-app-role",
                "permissions_by_service": {"s3": ["s3:GetObject", "s3:PutObject"]},
            }
            mock_aws.return_value = mock_aws_instance

            result = run_tool(
                tool="aws.iam_role_permissions",
                args={"service_account": "test-sa", "namespace": "test-ns"},
                policy=policy,
                action_policy=None,
                analysis_json=analysis_json,
            )

            assert result.ok is True
            mock_aws_instance.get_iam_role_permissions.assert_called_once_with("my-app-role")


def test_iam_role_permissions_with_direct_role_name():
    """Tool should still work when role_name is provided directly."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(allow_aws_read=True)
    analysis_json = {}

    # Mock AWS provider
    with patch("agent.providers.aws_provider.get_aws_provider") as mock_aws:
        mock_aws_instance = MagicMock()
        mock_aws_instance.get_iam_role_permissions.return_value = {
            "role_name": "direct-role",
            "permissions_by_service": {"s3": ["s3:*"]},
        }
        mock_aws.return_value = mock_aws_instance

        result = run_tool(
            policy=policy,
            action_policy=None,
            tool="aws.iam_role_permissions",
            args={"role_name": "direct-role"},
            analysis_json=analysis_json,
        )

        assert result.ok is True
        mock_aws_instance.get_iam_role_permissions.assert_called_once_with("direct-role")


def test_iam_role_permissions_falls_back_to_pod_annotations():
    """Tool should fall back to pod annotations if service account lookup fails."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(allow_aws_read=True)
    analysis_json = {
        "target": {"namespace": "test-ns"},
        "evidence": {
            "k8s": {
                "pod_info": {"annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789012:role/pod-role"}}
            }
        },
    }

    # Mock get_service_account_info to raise exception (simulate failure)
    with patch("agent.providers.k8s_provider.get_service_account_info") as mock_sa_info:
        mock_sa_info.side_effect = Exception("K8s API error")

        # Mock AWS provider
        with patch("agent.providers.aws_provider.get_aws_provider") as mock_aws:
            mock_aws_instance = MagicMock()
            mock_aws_instance.get_iam_role_permissions.return_value = {
                "role_name": "pod-role",
                "permissions_by_service": {"s3": ["s3:*"]},
            }
            mock_aws.return_value = mock_aws_instance

            result = run_tool(
                tool="aws.iam_role_permissions",
                args={"service_account": "test-sa", "namespace": "test-ns"},
                policy=policy,
                action_policy=None,
                analysis_json=analysis_json,
            )

            # Should fall back to pod annotations and succeed
            assert result.ok is True
            mock_aws_instance.get_iam_role_permissions.assert_called_once_with("pod-role")
