"""Tests for service account annotation extraction (IRSA support)."""

from unittest.mock import MagicMock, patch


def test_get_service_account_info_includes_annotations():
    """get_service_account_info should return annotations including IAM role ARN."""
    from agent.providers.k8s_provider import get_service_account_info

    # Mock the K8s API response
    with patch("agent.providers.k8s_provider._get_core_v1") as mock_core_v1:
        mock_api = MagicMock()
        mock_core_v1.return_value = mock_api

        # Create mock service account with IAM role annotation
        mock_sa = MagicMock()
        mock_sa.metadata.name = "test-sa"
        mock_sa.metadata.namespace = "test-ns"
        mock_sa.metadata.annotations = {
            "eks.amazonaws.com/role-arn": "arn:aws:iam::123456789:role/test-role",
            "other-annotation": "other-value",
        }
        mock_sa.image_pull_secrets = []
        mock_sa.automount_service_account_token = True

        mock_api.read_namespaced_service_account.return_value = mock_sa

        # Call the function
        result = get_service_account_info(namespace="test-ns", name="test-sa")

        # Verify annotations are included
        assert "annotations" in result
        assert isinstance(result["annotations"], dict)
        assert "eks.amazonaws.com/role-arn" in result["annotations"]
        assert result["annotations"]["eks.amazonaws.com/role-arn"] == "arn:aws:iam::123456789:role/test-role"
        assert result["annotations"]["other-annotation"] == "other-value"

        # Verify other fields still work
        assert result["name"] == "test-sa"
        assert result["namespace"] == "test-ns"
        assert result["automount_service_account_token"] is True


def test_get_service_account_info_handles_no_annotations():
    """get_service_account_info should handle service accounts without annotations."""
    from agent.providers.k8s_provider import get_service_account_info

    with patch("agent.providers.k8s_provider._get_core_v1") as mock_core_v1:
        mock_api = MagicMock()
        mock_core_v1.return_value = mock_api

        # Create mock service account WITHOUT annotations
        mock_sa = MagicMock()
        mock_sa.metadata.name = "test-sa"
        mock_sa.metadata.namespace = "test-ns"
        mock_sa.metadata.annotations = None  # No annotations
        mock_sa.image_pull_secrets = []
        mock_sa.automount_service_account_token = False

        mock_api.read_namespaced_service_account.return_value = mock_sa

        result = get_service_account_info(namespace="test-ns", name="test-sa")

        # Should return empty annotations dict (not None)
        assert "annotations" in result
        assert result["annotations"] == {}


def test_get_service_account_info_handles_empty_annotations():
    """get_service_account_info should handle service accounts with empty annotations dict."""
    from agent.providers.k8s_provider import get_service_account_info

    with patch("agent.providers.k8s_provider._get_core_v1") as mock_core_v1:
        mock_api = MagicMock()
        mock_core_v1.return_value = mock_api

        mock_sa = MagicMock()
        mock_sa.metadata.name = "test-sa"
        mock_sa.metadata.namespace = "test-ns"
        mock_sa.metadata.annotations = {}  # Empty annotations
        mock_sa.image_pull_secrets = []
        mock_sa.automount_service_account_token = False

        mock_api.read_namespaced_service_account.return_value = mock_sa

        result = get_service_account_info(namespace="test-ns", name="test-sa")

        assert "annotations" in result
        assert result["annotations"] == {}


def test_iam_role_extraction_now_works_end_to_end():
    """Integration test: IAM role extraction from service account should now work."""
    from agent.authz.policy import ChatPolicy
    from agent.chat.tools import run_tool

    policy = ChatPolicy(allow_aws_read=True)
    analysis_json = {"target": {"namespace": "test-ns"}}

    # Mock get_service_account_info to return service account WITH IAM annotation
    with patch("agent.providers.k8s_provider.get_service_account_info") as mock_sa_info:
        mock_sa_info.return_value = {
            "name": "test-sa",
            "namespace": "test-ns",
            "annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::123456789:role/my-app-role"},
            "image_pull_secrets": [],
            "automount_service_account_token": True,
        }

        # Mock AWS provider
        with patch("agent.providers.aws_provider.get_aws_provider") as mock_aws:
            mock_aws_instance = MagicMock()
            mock_aws_instance.get_iam_role_permissions.return_value = {
                "role_name": "my-app-role",
                "permissions_by_service": {
                    "s3": ["s3:GetObject", "s3:PutObject", "s3:GetBucketLocation"],
                    "ecr": ["ecr:GetAuthorizationToken", "ecr:BatchGetImage"],
                },
            }
            mock_aws.return_value = mock_aws_instance

            # Call the tool with service_account+namespace
            result = run_tool(
                policy=policy,
                action_policy=None,
                tool="aws.iam_role_permissions",
                args={"service_account": "test-sa", "namespace": "test-ns"},
                analysis_json=analysis_json,
            )

            # Should successfully extract role and query permissions
            assert result.ok is True
            assert result.error is None
            mock_aws_instance.get_iam_role_permissions.assert_called_once_with("my-app-role")
            assert "permissions_by_service" in result.result
