"""
Tests for deterministic fast-path intents in agent/chat/intents.py.

Covers:
- Greetings (case + global) return handled=True with zero tool events
- Greetings with follow-up questions return handled=False
- Summary intent returns case data without tools
- Summary with qualifiers returns handled=False
- Status check returns verdict data without tools
- Empty analysis_json edge cases don't crash
- Full run_chat() path: greeting skips LLM entirely
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.authz.policy import ChatPolicy
from agent.chat.intents import (
    try_handle_case_intents,
    try_handle_global_intents,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def policy():
    return ChatPolicy(enabled=True, redact_secrets=False)


@pytest.fixture
def rich_analysis_json():
    """Full analysis_json with verdict, hypotheses, scores."""
    return {
        "target": {
            "kind": "StatefulSet",
            "name": "mysql",
            "namespace": "prod",
            "service": "mysql",
        },
        "analysis": {
            "verdict": {
                "label": "MySQL StatefulSet experiencing high CPU throttling",
                "why": ["Container hitting CPU limits", "Throttle ratio > 0.8"],
                "next": ["Increase CPU limits", "Check resource requests"],
            },
            "hypotheses": [
                {
                    "hypothesis_id": "cpu_throttling",
                    "title": "CPU throttling detected",
                    "confidence_0_100": 85,
                },
                {
                    "hypothesis_id": "resource_contention",
                    "title": "Resource contention on node",
                    "confidence_0_100": 40,
                },
            ],
            "scores": {
                "classification": "actionable",
                "severity": "high",
                "confidence": 85,
            },
            "features": {},
        },
    }


@pytest.fixture
def empty_analysis_json():
    """Minimal analysis_json with no data."""
    return {}


# ---------------------------------------------------------------------------
# Case greeting intent
# ---------------------------------------------------------------------------


class TestCaseGreetingIntent:
    @pytest.mark.parametrize(
        "msg",
        [
            "hi",
            "Hi",
            "HI",
            "hello",
            "Hello",
            "hey",
            "Hey!",
            "thanks",
            "Thank you",
            "thank you!",
            "thx",
            "ty",
            "ok",
            "okay",
            "got it",
            "understood",
            "sounds good",
            "cool",
            "great",
            "awesome",
            "nice",
            "perfect",
            "noted",
            "bye",
            "goodbye",
            "see ya",
            "later",
            "good night",
            "good morning",
            "good afternoon",
            "good evening",
            "cheers",
            "makes sense",
        ],
    )
    def test_greeting_handled(self, rich_analysis_json, msg):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message=msg)
        assert result.handled is True
        assert result.intent_id == "case.greeting"
        assert result.tool_events == []
        assert "mysql" in result.reply.lower()

    @pytest.mark.parametrize(
        "msg",
        [
            "hello check the logs",
            "hi can you show me the metrics",
            "hey what's the CPU usage",
            "thanks but can you also check the pods",
            "ok now check kubernetes",
        ],
    )
    def test_greeting_with_followup_not_handled(self, rich_analysis_json, msg):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message=msg)
        assert result.handled is False

    def test_greeting_empty_analysis(self, empty_analysis_json):
        result = try_handle_case_intents(analysis_json=empty_analysis_json, user_message="hello")
        assert result.handled is True
        assert result.intent_id == "case.greeting"
        assert "this case" in result.reply.lower()


# ---------------------------------------------------------------------------
# Case summary intent
# ---------------------------------------------------------------------------


class TestCaseSummaryIntent:
    @pytest.mark.parametrize(
        "msg",
        [
            "what happened",
            "what happened?",
            "What happened",
            "summarize",
            "summary",
            "tldr",
            "tl;dr",
            "TLDR",
            "overview",
            "recap",
            "brief me",
            "catch me up",
            "give me the summary",
            "give me the tldr",
            "give me the overview",
            "give me the rundown",
            "give me the gist",
            "explain this case",
            "explain the alert",
            "explain the incident",
            "explain the issue",
            "what's going on",
            "what's the issue",
            "what's the problem",
            "what's the situation",
            "what's the story",
            "what's the deal",
        ],
    )
    def test_summary_handled(self, rich_analysis_json, msg):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message=msg)
        assert result.handled is True
        assert result.intent_id == "case.summary"
        assert result.tool_events == []
        assert "mysql" in result.reply.lower()
        assert "cpu throttling" in result.reply.lower()

    @pytest.mark.parametrize(
        "msg",
        [
            "summarize the logs",
            "what happened in the last commit",
            "what happened to the pod after restart",
            "give me a summary of kubernetes events",
        ],
    )
    def test_summary_with_qualifier_not_handled(self, rich_analysis_json, msg):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message=msg)
        assert result.handled is False

    def test_summary_includes_hypotheses(self, rich_analysis_json):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message="what happened")
        assert "85/100" in result.reply
        assert "CPU throttling detected" in result.reply

    def test_summary_includes_next_steps(self, rich_analysis_json):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message="tldr")
        assert "next steps" in result.reply.lower()

    def test_summary_empty_analysis(self, empty_analysis_json):
        result = try_handle_case_intents(analysis_json=empty_analysis_json, user_message="what happened")
        assert result.handled is True
        assert result.intent_id == "case.summary"
        # Should not crash; should have fallback text
        assert "no verdict" in result.reply.lower()


# ---------------------------------------------------------------------------
# Case status intent
# ---------------------------------------------------------------------------


class TestCaseStatusIntent:
    @pytest.mark.parametrize(
        "msg",
        [
            "what's the status",
            "what's the status?",
            "whats the status",
            "whats status",
            "is it resolved",
            "is it fixed",
            "is it still firing",
            "is this still active",
            "still happening",
            "still firing",
            "still active",
            "are we ok",
            "are we good",
            "are we safe",
            "how bad is it",
            "how bad is this",
        ],
    )
    def test_status_handled(self, rich_analysis_json, msg):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message=msg)
        assert result.handled is True
        assert result.intent_id == "case.status"
        assert result.tool_events == []
        assert "mysql" in result.reply.lower()

    def test_status_includes_classification(self, rich_analysis_json):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message="what's the status")
        assert "actionable" in result.reply.lower()
        assert "high" in result.reply.lower()

    def test_status_includes_confidence(self, rich_analysis_json):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message="what's the status")
        assert "85/100" in result.reply

    def test_status_nudges_live_data(self, rich_analysis_json):
        result = try_handle_case_intents(analysis_json=rich_analysis_json, user_message="is it resolved")
        assert "live" in result.reply.lower() or "re-check" in result.reply.lower()

    def test_status_empty_analysis(self, empty_analysis_json):
        result = try_handle_case_intents(analysis_json=empty_analysis_json, user_message="what's the status")
        assert result.handled is True
        assert result.intent_id == "case.status"
        # Should not crash
        assert "no verdict" in result.reply.lower() or "unknown" in result.reply.lower()


# ---------------------------------------------------------------------------
# Global greeting intent
# ---------------------------------------------------------------------------


class TestGlobalGreetingIntent:
    @pytest.mark.parametrize(
        "msg",
        [
            "hi",
            "hello",
            "hey",
            "thanks",
            "bye",
            "good morning",
        ],
    )
    def test_global_greeting_handled(self, policy, msg):
        result = try_handle_global_intents(policy=policy, user_message=msg)
        assert result.handled is True
        assert result.intent_id == "global.greeting"
        assert result.tool_events == []

    @pytest.mark.parametrize(
        "msg",
        [
            "hello how many cases",
            "hi show me top teams",
        ],
    )
    def test_global_greeting_with_followup_not_handled(self, policy, msg):
        result = try_handle_global_intents(policy=policy, user_message=msg)
        # Either handled as greeting (False because it has extra words) or
        # handled as a different intent — either way it should NOT be a greeting
        if result.handled:
            assert result.intent_id != "global.greeting"

    def test_global_empty_message(self, policy):
        result = try_handle_global_intents(policy=policy, user_message="")
        assert result.handled is False


# ---------------------------------------------------------------------------
# Existing intents still work
# ---------------------------------------------------------------------------


class TestExistingIntentsUnchanged:
    def test_case_family_count_still_works(self, rich_analysis_json):
        """The 'how many' family count intent should still fall through."""
        # This requires Postgres, so it should either handle or not — just shouldn't crash
        result = try_handle_case_intents(
            analysis_json=rich_analysis_json,
            user_message="how many cpu throttling cases in the last 7 days",
        )
        # We can't test the full DB path without Postgres, but it should not
        # be intercepted by greeting/summary/status
        assert result.intent_id != "case.greeting"
        assert result.intent_id != "case.summary"
        assert result.intent_id != "case.status"


# ---------------------------------------------------------------------------
# Full run_chat() path: greeting skips LLM
# ---------------------------------------------------------------------------


class TestRunChatGreetingSkipsLLM:
    def test_greeting_skips_llm_entirely(self):
        """When user says 'hello', run_chat should return immediately without calling generate_json."""
        policy = ChatPolicy(
            enabled=True,
            allow_promql=True,
            allow_k8s_read=True,
            max_steps=5,
            max_tool_calls=12,
            redact_secrets=False,
        )
        analysis_json = {
            "target": {"kind": "Pod", "name": "test-pod", "namespace": "default"},
            "analysis": {"verdict": {}, "hypotheses": []},
        }

        with patch("agent.chat.runtime.generate_json") as mock_generate_json:
            from agent.chat.runtime import run_chat

            result = run_chat(
                policy=policy,
                analysis_json=analysis_json,
                user_message="hello",
                history=[],
            )

            # LLM should NOT have been called
            mock_generate_json.assert_not_called()

            # Should get a greeting reply
            assert result.reply
            assert "test-pod" in result.reply.lower()
            assert result.tool_events == []

    def test_summary_skips_llm_entirely(self):
        """When user says 'what happened', run_chat should return from SSOT without LLM."""
        policy = ChatPolicy(
            enabled=True,
            max_steps=5,
            max_tool_calls=12,
            redact_secrets=False,
        )
        analysis_json = {
            "target": {"kind": "Pod", "name": "api-server", "namespace": "prod"},
            "analysis": {
                "verdict": {"label": "API server crash loop", "why": [], "next": []},
                "hypotheses": [{"hypothesis_id": "crash_loop", "title": "CrashLoopBackOff", "confidence_0_100": 90}],
            },
        }

        with patch("agent.chat.runtime.generate_json") as mock_generate_json:
            from agent.chat.runtime import run_chat

            result = run_chat(
                policy=policy,
                analysis_json=analysis_json,
                user_message="what happened",
                history=[],
            )

            mock_generate_json.assert_not_called()
            assert "api-server" in result.reply.lower()
            assert "crash" in result.reply.lower()
            assert result.tool_events == []

    def test_real_question_still_hits_llm(self):
        """A real investigation question should still go to the LLM."""
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

        mock_tool_result = MagicMock()
        mock_tool_result.ok = True
        mock_tool_result.result = {"phase": "Running"}
        mock_tool_result.error = None
        mock_tool_result.updated_analysis = None

        with patch("agent.chat.runtime.generate_json") as mock_generate_json, patch(
            "agent.chat.runtime.run_tool", return_value=mock_tool_result
        ):

            mock_generate_json.side_effect = [
                ({"reply": "Let me check.", "tool_calls": [{"tool": "k8s.pod_context", "args": {}}]}, None),
                ({"reply": "Pod is running.", "tool_calls": []}, None),
            ]

            from agent.chat.runtime import run_chat

            run_chat(
                policy=policy,
                analysis_json=analysis_json,
                user_message="check the current pod status for me",
                history=[],
            )

            # LLM SHOULD have been called for a real investigation question
            assert mock_generate_json.call_count >= 1
