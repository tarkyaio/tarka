# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-06-01

### Added

- **Investigation pipeline**: Converts Prometheus/Alertmanager alerts into structured triage reports
- **Playbook system**: Alert-specific investigation logic for CPU throttling, OOM kills, HTTP 5xx, pod health, job failures, and crash loops
- **Diagnostic engine**: Universal failure mode detection with confidence-scored hypotheses
- **Deterministic base triage**: Evidence-backed verdicts with explicit unknowns (never guesses)
- **Multi-source evidence collection**: Prometheus metrics, Kubernetes context, and log correlation
- **Webhook mode**: In-cluster deployment with Alertmanager webhook receiver and NATS JetStream worker pool
- **Console UI**: React-based case browser with investigation details and assistant chat
- **Chat runtime**: Tool-using conversational interface for deeper investigation (PromQL, K8s, logs, memory)
- **Case-based reasoning**: PostgreSQL-backed memory system for learning from past incidents
- **Multi-provider LLM support**: Optional enrichment via Vertex AI (Gemini) or Anthropic (Claude)
- **AWS evidence collection**: EC2, EBS, ELB, RDS, ECR, CloudTrail, and IAM/IRSA diagnostics
- **GitHub integration**: Recent commits, deployments, and PR correlation for change context
- **Read-only design**: All operations are strictly read-only; no cluster mutations
