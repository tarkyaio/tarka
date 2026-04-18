# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-04-18

### Changed

- **Images**: Harden all images with Chainguard base images; restructure image variants and deploy tooling (#45)
- **Repo**: Remove website from the main app's repository (#44)

### Dependencies

- Bump authlib from 1.6.9 to 1.6.11 (#47)
- Bump langsmith from 0.7.2 to 0.7.31 (#46)
- Bump pytest from 8.3.4 to 9.0.3 (#43)

## [0.3.2] - 2026-04-10

### Added

- **Helm chart**: GPG signing, cosign keyless signing, and SLSA provenance attestation for chart OCI artifacts
- **CI**: Harden runner step for Helm release job

### Removed

- **CI**: Removed `set-public` job; org settings now handle GHCR package visibility automatically

## [0.3.1] - 2026-04-09

### Added

- **Helm chart**: Official Helm chart published to `oci://ghcr.io/tarkyaio/charts/tarka` for cluster deployment (#32)

### Changed

- **CI**: Add NATS Helm repo before building subchart dependencies in release pipeline (#34)
- **Chart**: Bump appVersion to 0.3.1 in Helm chart (#35)

### Fixed

- **CI**: Release workflow `update-site-version` job now correctly handles branch protection on main

### Documentation

- **Site**: Updated landing page, screenshots, and mock data (#31)

## [0.2.0] - 2026-04-05

### Added

- **Slack integration**: AI assistant with working state feedback delivered via Slack (#14)
- **Infra context repos**: Collect service-scoped files and diffs from org-wide Terraform/Argo CD repos during investigations (#16)
- **Token/cost tracking**: Token usage and cost estimation displayed in pipeline reports and UI case screen (#11, #13)
- **GitHub service layer**: Git mirror cache, regression analysis, and structured service layer for GitHub evidence (#7)
- **K8S_VERIFY_SSL flag**: Option to skip TLS verification for EKS clusters with CA cert issues (#15)

### Fixed

- **Infra context**: Improved service name resolution for infra context lookups (#17)
- **UI**: Improved report readability and renamed browser tab title to Tarka (#10)
- **Chat**: Added component grouping to `cases.top` tool (#8)
- **Runtime**: Prevented event loop blocking in streaming chat runtimes (#5)

### Changed

- **Deploy**: Removed External Secrets Operator dependency, simplifying deployment (#9)
- **Infra**: NATS retry improvements, DB query optimization, LLM observability enhancements (#6)

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
