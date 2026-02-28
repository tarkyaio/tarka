"""Diagnostic module for Kubernetes Job failures.

This is the FIRST module to use parsed log content, establishing the pattern
for other modules to follow. It uses the generic log pattern matching framework
to convert parsed errors into specific, actionable hypotheses.
"""

from typing import List

from agent.core.models import ActionProposal, Hypothesis, Investigation
from agent.diagnostics.base import DiagnosticModule
from agent.diagnostics.log_pattern_matcher import LogPatternMatcher
from agent.diagnostics.patterns import ALL_PATTERNS


class JobFailureDiagnosticModule(DiagnosticModule):
    """
    Diagnose Job failures by parsing log errors using generic pattern matching framework.

    Architecture:
    - Uses generic LogPatternMatcher (reusable across all modules)
    - Loads patterns from extensible library (currently S3, future: RDS, ECR, app errors)
    - Converts matched patterns into Hypothesis objects with confidence scores

    This establishes a new pattern: instead of only checking K8s signals
    (waiting_reason, pod_events), we now actually INTERPRET log content.

    Graceful degradation:
    - Works without AWS (uses logs only)
    - Works without LLM (deterministic pattern matching)
    - Works without logs (generates generic hypothesis)
    """

    module_id = "job_failure"

    def __init__(self):
        # Use generic framework with all available patterns
        self.matcher = LogPatternMatcher(ALL_PATTERNS)

    def applies(self, investigation: Investigation) -> bool:
        """Applies to Job-scoped alerts.

        Checks both:
        1. Target workload_kind == "Job"
        2. Alert family == "job_failed" (for Job alerts from kube-state-metrics)
        """
        return investigation.target.workload_kind == "Job" or investigation.meta.get("family") == "job_failed"

    def collect(self, investigation: Investigation) -> None:
        """Collect Job failure evidence (delegates to job_failure collector).

        This ensures the Job-specific evidence collection happens:
        1. Extract job_name from alert labels
        2. Find actual Job pods using label selector
        3. Collect logs from Job pods
        4. Parse log errors for pattern matching
        """
        from agent.collectors.job_failure import collect_job_failure_evidence

        collect_job_failure_evidence(investigation)

    def diagnose(self, investigation: Investigation) -> List[Hypothesis]:
        """Generate hypotheses from parsed log errors using generic pattern matching.

        Process:
        1. Check if parsed_errors exist
        2. Match against known patterns (S3, RDS, ECR, etc.)
        3. Extract context (bucket names, operations, etc.) from logs
        4. Generate Hypothesis objects with pattern-specific details

        Returns:
            List of Hypothesis objects (empty if no patterns match)
        """
        hypotheses = []

        # Get parsed errors from evidence
        if not investigation.evidence.logs or not investigation.evidence.logs.parsed_errors:
            # No logs available - return empty (base triage will handle this)
            return hypotheses

        parsed_errors = investigation.evidence.logs.parsed_errors

        # Match against known patterns using generic framework
        matches = self.matcher.find_matches(parsed_errors)

        if not matches:
            # No patterns matched - could still be a failure, just not a recognized pattern
            # Base triage will handle this as generic "Job failed"
            return hypotheses

        # Convert matches to hypotheses
        for pattern, context in matches:
            # Fill in additional context from investigation
            full_context = self._build_context(investigation, context)

            # Count how many errors matched this pattern
            matching_error_count = sum(1 for e in parsed_errors if pattern.matches(e.get("message", "")))

            # Build why bullets
            # Use safe formatting - missing keys replaced with "unknown"
            from collections import defaultdict

            safe_context = defaultdict(lambda: "unknown", full_context)
            try:
                why_message = pattern.why_template.format_map(safe_context)
            except (KeyError, ValueError):
                # Fallback to pattern title if formatting fails
                why_message = pattern.title

            why_bullets = [
                why_message,
                f"Found {matching_error_count} matching error pattern(s) in logs",
            ]

            # Add log sample if available
            sample_error = next(
                (e for e in parsed_errors if pattern.matches(e.get("message", ""))),
                None,
            )
            if sample_error:
                why_bullets.append(f"Sample: {sample_error.get('message', '')[:200]}")

            # Build next_tests: combine remediation (fixes) and diagnostics
            # Remediation steps come first (what to do to fix), diagnostics second (how to verify)
            combined_next_tests = []

            # Add remediation steps first (the actual fixes)
            if pattern.remediation_steps:
                combined_next_tests.extend([cmd.format_map(safe_context) for cmd in pattern.remediation_steps])

            # Add diagnostic steps (for verification/investigation)
            if pattern.next_tests:
                if pattern.remediation_steps:
                    combined_next_tests.append("")  # Separator
                combined_next_tests.extend([cmd.format_map(safe_context) for cmd in pattern.next_tests])

            # Build proposed actions (pattern-specific)
            proposed_actions = self._build_proposed_actions(pattern, full_context)

            # Resolve placeholders in next_tests (replace 'unknown', 'ERROR', etc. with actual values)
            from agent.utils.placeholder_resolver import PlaceholderResolver

            resolver = PlaceholderResolver(investigation)
            resolved_next_tests = [resolver.resolve(cmd) for cmd in combined_next_tests]

            # Build hypothesis
            hypothesis = Hypothesis(
                hypothesis_id=pattern.pattern_id,
                title=pattern.title,
                confidence_0_100=pattern.confidence,
                why=why_bullets,
                supporting_refs=["evidence.logs.parsed_errors"],
                next_tests=resolved_next_tests,
                proposed_actions=proposed_actions,
            )
            hypotheses.append(hypothesis)

        return hypotheses

    def _build_context(self, investigation: Investigation, extracted_context: dict) -> dict:
        """Build full context dict with investigation details + extracted fields.

        Args:
            investigation: Current investigation
            extracted_context: Fields extracted from logs by pattern (e.g., {"bucket": "my-bucket"})

        Returns:
            Full context dict with all fields needed for template interpolation
        """
        context = {
            "namespace": investigation.target.namespace or "default",
            "pod": investigation.target.pod or "unknown",
            "sa": "unknown",
            "role_name": "unknown",
            "role_arn": "unknown",
            "cluster_name": "unknown",
        }

        # Add service account if available
        if investigation.evidence.k8s and investigation.evidence.k8s.pod_info:
            context["sa"] = investigation.evidence.k8s.pod_info.get("service_account", "unknown")

        # Add AWS context if available
        if investigation.evidence.aws and investigation.evidence.aws.metadata:
            aws_meta = investigation.evidence.aws.metadata
            if "iam_validation" in aws_meta:
                iam = aws_meta["iam_validation"]
                context["role_name"] = iam.get("role_name", "unknown")
                context["role_arn"] = iam.get("role_arn", "unknown")

        # Merge extracted context (overrides defaults)
        context.update(extracted_context)

        return context

    def _build_proposed_actions(self, pattern, context: dict) -> List[ActionProposal]:
        """Build pattern-specific proposed actions for UI Actions section.

        Args:
            pattern: The matched LogPattern
            context: Full context dict with investigation + extracted fields

        Returns:
            List of ActionProposal objects
        """
        actions = []

        # S3 access denied - add IAM diagnostic and remediation actions
        if pattern.pattern_id == "s3_access_denied":
            sa_name = context.get("sa", "unknown")
            ns = context.get("namespace", "default")
            bucket = context.get("bucket", "unknown")

            # Action 1: Diagnose IAM role (low risk diagnostic)
            actions.append(
                ActionProposal(
                    action_type="diagnose_iam_role",
                    title=f"Get IAM role for service account {sa_name}",
                    risk="low",
                    preconditions=[],
                    execution_payload={
                        "command": f"kubectl get sa {sa_name} -n {ns} -o jsonpath='{{.metadata.annotations.eks\\.amazonaws\\.com/role-arn}}'",
                        "namespace": ns,
                        "service_account": sa_name,
                    },
                )
            )

            # Action 2: Validate S3 bucket access (low risk diagnostic)
            if bucket != "unknown":
                actions.append(
                    ActionProposal(
                        action_type="validate_s3_access",
                        title=f"Validate S3 bucket access: {bucket}",
                        risk="low",
                        preconditions=[],
                        execution_payload={
                            "command": f"aws s3api head-bucket --bucket {bucket}",
                            "bucket": bucket,
                        },
                    )
                )

            # Action 3: Attach IAM policy (medium risk fix)
            actions.append(
                ActionProposal(
                    action_type="attach_iam_policy",
                    title="Attach S3 access policy to IAM role",
                    risk="medium",
                    preconditions=["Verify IAM role ARN", "Confirm bucket name"],
                    execution_payload={
                        "policy_name": "S3Access",
                        "bucket": bucket,
                        "permissions": ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
                    },
                )
            )

        return actions
