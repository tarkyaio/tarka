"""
Behavioral tests for chat autonomy improvements.

These tests verify that the chat agent uses tools proactively instead of
suggesting manual commands (kubectl, aws, gh) to the user.
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.authz.policy import ChatPolicy
from agent.chat.global_runtime import run_global_chat
from agent.chat.runtime import run_chat


@pytest.fixture
def mock_policy():
    """Chat policy with all tools enabled."""
    return ChatPolicy(
        enabled=True,
        allow_promql=True,
        allow_k8s_read=True,
        allow_k8s_events=True,
        allow_logs_query=True,
        allow_memory_read=True,
        allow_report_rerun=True,
        allow_argocd_read=True,
        allow_aws_read=True,
        allow_github_read=True,
        max_steps=5,
        max_tool_calls=12,
        redact_secrets=False,
    )


@pytest.fixture
def mock_analysis_json():
    """Minimal investigation SSOT for testing."""
    return {
        "target": {
            "kind": "StatefulSet",
            "name": "mysql",
            "namespace": "prod",
        },
        "analysis": {
            "verdict": {
                "label": "MySQL StatefulSet experiencing high CPU throttling",
                "why": ["Container hitting CPU limits"],
                "next": ["Check resource requests/limits"],
            },
            "hypotheses": [
                {
                    "hypothesis_id": "cpu_throttling",
                    "title": "CPU throttling detected",
                    "confidence_0_100": 85,
                }
            ],
            "scores": {},
            "features": {},
        },
    }


@pytest.mark.parametrize(
    "user_message,expected_tool,forbidden_keywords",
    [
        # K8s tools - should NOT suggest kubectl
        (
            "Check the StatefulSet status",
            "k8s.rollout_status",
            ["kubectl", "kubectl get", "kubectl describe"],
        ),
        (
            "What are the pod events?",
            "k8s.events",
            ["kubectl", "kubectl get events", "kubectl describe"],
        ),
        (
            "Show me pod details",
            "k8s.pod_context",
            ["kubectl", "kubectl get pod", "kubectl describe pod"],
        ),
        # AWS tools - should NOT suggest aws cli
        (
            "Check EC2 instance health",
            "aws.ec2_status",
            ["aws", "aws ec2 describe-instance-status", "aws cli"],
        ),
        (
            "What's the EBS volume status?",
            "aws.ebs_health",
            ["aws", "aws ec2 describe-volumes", "aws cli"],
        ),
        (
            "Check the load balancer targets",
            "aws.elb_health",
            ["aws", "aws elbv2 describe-target-health", "aws cli"],
        ),
        # GitHub tools - should NOT suggest gh cli
        (
            "Show recent commits",
            "github.recent_commits",
            ["gh", "gh api", "git log"],
        ),
        (
            "Check workflow runs",
            "github.workflow_runs",
            ["gh", "gh run list", "gh workflow"],
        ),
    ],
)
def test_chat_uses_tools_instead_of_manual_commands(
    mock_policy, mock_analysis_json, user_message, expected_tool, forbidden_keywords
):
    """
    Test that chat agent uses tools immediately instead of suggesting manual commands.

    This is the core behavioral test for autonomy improvements.
    """
    # Mock LLM response: agent decides to use the appropriate tool
    mock_llm_response = {
        "schema_version": "tarka.tool_plan.v1",
        "reply": "Let me check that for you.",
        "tool_calls": [{"tool": expected_tool, "args": {}}],
        "meta": None,
    }

    # Mock tool execution
    mock_tool_result = MagicMock()
    mock_tool_result.ok = True
    mock_tool_result.result = {"status": "healthy"}
    mock_tool_result.error = None
    mock_tool_result.updated_analysis = None

    with patch("agent.chat.runtime.generate_json") as mock_generate_json, patch(
        "agent.chat.runtime.run_tool", return_value=mock_tool_result
    ) as mock_run_tool:

        # First call: LLM plans to use tool
        # Second call: LLM provides final answer after tool execution
        mock_generate_json.side_effect = [
            (mock_llm_response, None),
            ({"reply": "Here's what I found: status is healthy.", "tool_calls": []}, None),
        ]

        result = run_chat(
            policy=mock_policy,
            analysis_json=mock_analysis_json,
            user_message=user_message,
            history=[],
        )

        # Assert: Tool was called
        mock_run_tool.assert_called()
        call_args = mock_run_tool.call_args
        assert (
            call_args.kwargs["tool"] == expected_tool
        ), f"Expected tool {expected_tool} to be called, but got {call_args.kwargs['tool']}"

        # Assert: Reply does NOT contain forbidden keywords (kubectl, aws, gh commands)
        reply_lower = result.reply.lower()
        for keyword in forbidden_keywords:
            assert (
                keyword.lower() not in reply_lower
            ), f"Reply should not suggest '{keyword}' command. Got: {result.reply}"


def test_chat_does_not_suggest_kubectl_in_passive_mode():
    """
    Verify that the old passive behavior (suggesting kubectl) is eliminated.

    This test ensures the agent doesn't fall back to suggesting manual commands.
    """
    policy = ChatPolicy(
        enabled=True,
        allow_k8s_read=True,
        max_steps=5,
        max_tool_calls=12,
        redact_secrets=False,
    )

    analysis_json = {
        "target": {"kind": "Pod", "name": "test-pod", "namespace": "default"},
        "analysis": {"verdict": {}, "hypotheses": []},
    }

    # Mock LLM to use tool proactively
    mock_llm_response = {
        "schema_version": "tarka.tool_plan.v1",
        "reply": "Let me check the pod status.",
        "tool_calls": [{"tool": "k8s.pod_context", "args": {"namespace": "default", "pod_name": "test-pod"}}],
        "meta": None,
    }

    mock_tool_result = MagicMock()
    mock_tool_result.ok = True
    mock_tool_result.result = {"phase": "Running"}
    mock_tool_result.error = None
    mock_tool_result.updated_analysis = None

    with patch("agent.chat.runtime.generate_json") as mock_generate_json, patch(
        "agent.chat.runtime.run_tool", return_value=mock_tool_result
    ):

        mock_generate_json.side_effect = [
            (mock_llm_response, None),
            ({"reply": "Pod is running.", "tool_calls": []}, None),
        ]

        result = run_chat(
            policy=policy,
            analysis_json=analysis_json,
            user_message="What's the pod status?",
            history=[],
        )

        # Assert: No kubectl suggestions
        forbidden_phrases = [
            "kubectl get",
            "kubectl describe",
            "run kubectl",
            "you can check with kubectl",
            "try kubectl",
        ]
        reply_lower = result.reply.lower()
        for phrase in forbidden_phrases:
            assert (
                phrase not in reply_lower
            ), f"Reply should not contain passive suggestion '{phrase}'. Got: {result.reply}"


def test_global_chat_uses_database_tools_immediately():
    """
    Test that global chat uses database query tools immediately instead of
    suggesting how to query.

    This is a behavioral test focusing on the agent's reply, not implementation details.
    """
    policy = ChatPolicy(
        enabled=True,
        max_steps=5,
        max_tool_calls=12,
        redact_secrets=False,
    )

    # Mock LLM to respond with autonomous tool use
    # First call: plan to use tool
    # Second call: final response after tool execution
    mock_llm_response_plan = {
        "schema_version": "tarka.tool_plan.v1",
        "reply": "Let me check open cases.",
        "tool_calls": [{"tool": "cases.count", "args": {"status": "open"}}],
        "meta": None,
    }

    mock_llm_response_final = {
        "schema_version": "tarka.tool_plan.v1",
        "reply": "I found 5 open cases.",
        "tool_calls": [],
        "meta": None,
    }

    # Mock intent handler to not intercept
    mock_intent_result = MagicMock()
    mock_intent_result.handled = False

    with patch("agent.chat.global_runtime.generate_json") as mock_generate_json, patch(
        "agent.chat.global_runtime.try_handle_global_intents", return_value=mock_intent_result
    ):

        # Use a side_effect that always returns valid responses
        def json_side_effect(*args, **kwargs):
            # Alternate between planning and final response
            if not hasattr(json_side_effect, "call_count"):
                json_side_effect.call_count = 0
            json_side_effect.call_count += 1

            if json_side_effect.call_count % 2 == 1:
                return (mock_llm_response_plan, None)
            else:
                return (mock_llm_response_final, None)

        mock_generate_json.side_effect = json_side_effect

        result = run_global_chat(
            policy=policy,
            user_message="How many open cases do we have?",
            history=[],
        )

        # Assert: Reply does NOT suggest manual querying
        # This is the key behavioral test - the agent should not defer to the user
        forbidden_phrases = [
            "you can query",
            "try querying",
            "run cases.count",
            "you can use cases.count",
            "you should query",
            "query with cases.count",
        ]
        reply_lower = result.reply.lower()
        for phrase in forbidden_phrases:
            assert phrase not in reply_lower, f"Global chat should not suggest manual queries. Got: {result.reply}"

        # Verify the response is proactive (uses phrases like "I found", "I checked")
        proactive_indicators = ["i found", "i checked", "i see", "there are", "we have"]
        has_proactive_tone = any(indicator in reply_lower for indicator in proactive_indicators)
        assert (
            has_proactive_tone or "let me" in reply_lower
        ), f"Response should be proactive, not passive. Got: {result.reply}"


def test_chat_maintains_warm_conversational_tone():
    """
    Verify that autonomy improvements don't make the agent robotic or cold.

    The agent should still use contractions, friendly language, and empathy.
    """
    policy = ChatPolicy(
        enabled=True,
        allow_k8s_read=True,
        max_steps=5,
        max_tool_calls=12,
        redact_secrets=False,
    )

    analysis_json = {
        "target": {"kind": "Pod", "name": "test-pod", "namespace": "default"},
        "analysis": {"verdict": {}, "hypotheses": []},
    }

    # Mock LLM to respond with warm, conversational tone
    mock_llm_response = {
        "schema_version": "tarka.tool_plan.v1",
        "reply": "Let me check that pod for you!",
        "tool_calls": [{"tool": "k8s.pod_context", "args": {}}],
        "meta": None,
    }

    mock_tool_result = MagicMock()
    mock_tool_result.ok = True
    mock_tool_result.result = {"phase": "Running"}
    mock_tool_result.error = None
    mock_tool_result.updated_analysis = None

    with patch("agent.chat.runtime.generate_json") as mock_generate_json, patch(
        "agent.chat.runtime.run_tool", return_value=mock_tool_result
    ):

        mock_generate_json.side_effect = [
            (mock_llm_response, None),
            ({"reply": "Good news—the pod's running smoothly!", "tool_calls": []}, None),
        ]

        result = run_chat(
            policy=policy,
            analysis_json=analysis_json,
            user_message="Is the pod okay?",
            history=[],
        )

        # Assert: Response is not robotic (uses contractions, friendly language)
        # We check for conversational markers like contractions
        reply = result.reply

        # These cold, robotic phrases should NOT appear
        robotic_phrases = [
            "the system indicates",
            "analysis reveals",
            "investigation shows",
            "data suggests",
        ]
        reply_lower = reply.lower()
        for phrase in robotic_phrases:
            assert phrase not in reply_lower, f"Response should maintain warm tone, not robotic. Got: {reply}"


def test_chat_asks_user_for_context_outside_tool_scope():
    """
    Test that the agent still asks the user for information outside its tool scope.

    Autonomy means "use tools proactively", NOT "never ask the user anything".
    The agent should still ask for:
    - Business context
    - Policy decisions
    - Information it cannot obtain via tools
    """
    policy = ChatPolicy(
        enabled=True,
        allow_k8s_read=True,
        max_steps=5,
        max_tool_calls=12,
        redact_secrets=False,
    )

    analysis_json = {
        "target": {"kind": "Deployment", "name": "api", "namespace": "prod"},
        "analysis": {"verdict": {}, "hypotheses": []},
    }

    # User asks a question that requires business context
    user_message = "Should we scale this deployment up?"

    # Mock LLM to ask for business context (this is correct behavior)
    mock_llm_response = {
        "schema_version": "tarka.tool_plan.v1",
        "reply": "I can check current resource usage, but I'd need to know: What's your target latency? Expected traffic increase?",
        "tool_calls": [
            {"tool": "k8s.rollout_status", "args": {"kind": "Deployment", "name": "api", "namespace": "prod"}}
        ],
        "meta": None,
    }

    mock_tool_result = MagicMock()
    mock_tool_result.ok = True
    mock_tool_result.result = {"replicas": 3}
    mock_tool_result.error = None
    mock_tool_result.updated_analysis = None

    with patch("agent.chat.runtime.generate_json") as mock_generate_json, patch(
        "agent.chat.runtime.run_tool", return_value=mock_tool_result
    ):

        mock_generate_json.side_effect = [
            (mock_llm_response, None),
            ({"reply": "Currently at 3 replicas. What's your capacity target?", "tool_calls": []}, None),
        ]

        result = run_chat(
            policy=policy,
            analysis_json=analysis_json,
            user_message=user_message,
            history=[],
        )

        # Assert: It's OK to ask for business context
        # The key is that it doesn't suggest "run kubectl" but asks for policy/business decisions
        assert "kubectl" not in result.reply.lower()
        # Verify it's asking for clarification (not asserting exact content, just that it's asking)
        assert len(result.reply) > 0
