# Chat Tools Reference

This document provides detailed documentation for all chat tools available in Tarka. Tools are read-only and policy-gated, allowing safe interactive investigation during incidents.

## Table of Contents

- [PromQL Tools](#promql-tools)
- [Kubernetes Tools](#kubernetes-tools)
- [Logs Tools](#logs-tools)
- [AWS Tools](#aws-tools)
- [GitHub Tools](#github-tools)
- [Memory Tools](#memory-tools)
- [Report Tools](#report-tools)

---

## PromQL Tools

### `promql.instant`

Query Prometheus for instant vector values at a specific time.

**Policy gate**: `CHAT_ALLOW_PROMQL=1`

**Arguments**:
- `query` (required): PromQL query string
- `at` (optional): ISO8601 timestamp (defaults to current time)

**Example**:
```
User: "What's the current CPU usage for this pod?"
Agent: [calls promql.instant with query="rate(container_cpu_usage_seconds_total{pod='my-pod'}[5m])"]
```

**Limits**:
- Max series: `CHAT_MAX_PROMQL_SERIES` (default: 200)
- Results truncated if exceeded

**Auto-scoping**:
- Automatically injects pod/namespace/cluster labels from investigation context
- Example: `rate(...)` becomes `rate(...{pod="my-pod",namespace="default"})`

---

## Kubernetes Tools

### `k8s.pod_context`

Get detailed pod information: status, conditions, resource requests/limits, container states.

**Policy gate**: `CHAT_ALLOW_K8S_READ=1`

**Arguments**:
- `pod` (optional): Pod name (defaults to investigation target)
- `namespace` (optional): Namespace (defaults to investigation target)

**Example**:
```
User: "Show me the pod's resource limits"
Agent: [calls k8s.pod_context]
Agent: "Pod has CPU limit of 1000m, memory limit of 512Mi. Currently using 850m CPU (85% of limit)."
```

**Returns**:
- Pod phase, conditions, QoS class
- Container statuses, restart counts
- Resource requests and limits
- Node name, IP addresses

### `k8s.rollout_status`

Get rollout status for Deployments, StatefulSets, DaemonSets.

**Policy gate**: `CHAT_ALLOW_K8S_READ=1`

**Arguments**:
- `kind` (required): Workload kind (`Deployment`, `StatefulSet`, `DaemonSet`)
- `name` (required): Workload name
- `namespace` (required): Namespace

**Example**:
```
User: "Is the deployment currently rolling out?"
Agent: [calls k8s.rollout_status with kind=Deployment, name=my-app]
Agent: "Deployment is rolling out: 2/3 replicas updated, 1 old replica still terminating."
```

**Returns**:
- Desired/current/ready/available replicas
- Update revision
- Rollout progress

### `k8s.events`

Query Kubernetes events for any resource type.

**Policy gate**: `CHAT_ALLOW_K8S_EVENTS=1` (default: true)

**Arguments**:
- `resource_type` (optional): `pod`, `deployment`, `statefulset`, `job`, `node`, `namespace`
- `resource_name` (optional): Resource name (defaults to investigation target)
- `namespace` (optional): Namespace (defaults to investigation target)
- `limit` (optional): Max events to return (5-100, default: 30)

**Example**:
```
User: "Show me recent events for this deployment"
Agent: [calls k8s.events with resource_type=deployment, resource_name=my-app]
Agent: "Recent events show ImagePullBackOff errors 5 times in last 10 minutes for image 'myregistry.io/myapp:v1.2.3'."
```

**Returns**:
- Event type (Normal/Warning)
- Reason (e.g., `FailedMount`, `Unhealthy`, `BackOff`)
- Message
- Count (how many times event occurred)
- First/last seen timestamps

**Use cases**:
- See failure progression: `ImagePullBackOff` → `CrashLoopBackOff` → `OOMKilled`
- Find configuration errors: volume mount failures, secret not found
- Detect resource constraints: node pressure, pod evictions

---

## Logs Tools

### `logs.tail`

Fetch recent logs from a pod/container.

**Policy gate**: `CHAT_ALLOW_LOGS_QUERY=1`

**Arguments**:
- `pod` (optional): Pod name (defaults to investigation target)
- `namespace` (optional): Namespace (defaults to investigation target)
- `container` (optional): Container name (for multi-container pods)
- `start_time` (optional): ISO8601 timestamp (defaults to 15m ago)
- `end_time` (optional): ISO8601 timestamp (defaults to now)
- `limit` (optional): Max log lines (10-max_log_lines, default: policy.max_log_lines)

**Example**:
```
User: "Show me the last 50 lines of logs"
Agent: [calls logs.tail with limit=50]
Agent: "Logs show repeated 'connection timeout' errors to database at 10.0.1.5:5432."
```

**Limits**:
- Max lines: `CHAT_MAX_LOG_LINES` (default: 200)
- Max time window: `CHAT_MAX_TIME_WINDOW_SECONDS` (default: 6h)

**Redaction**:
- If `CHAT_REDACT_SECRETS=1`, bearer tokens and API keys are redacted from output

**Job support**:
- Automatically finds pod for Kubernetes Jobs using `job-name` label selector
- Uses most recent pod if Job has multiple attempts

---

## AWS Tools

AWS tools provide infrastructure health context during investigations. Requires AWS provider setup and IAM permissions.

**Policy gate**: `CHAT_ALLOW_AWS_READ=1` (default: false)

**Region allowlist**: `CHAT_AWS_REGION_ALLOWLIST=us-east-1,us-west-2` (optional)

**Setup**: See [Authentication Guide](guides/authentication.md) for IAM role configuration.

### `aws.ec2_status`

Get EC2 instance status, system checks, and scheduled events.

**Arguments**:
- `instance_id` (optional): EC2 instance ID (auto-discovered from investigation if available)
- `region` (optional): AWS region (defaults to `AWS_REGION` or investigation metadata)

**Example**:
```
User: "What's the status of the EC2 instance?"
Agent: [calls aws.ec2_status with instance_id=i-abc123]
Agent: "Instance i-abc123 is running. System checks: OK. Scheduled maintenance: None."
```

**Returns**:
- Instance state (running, stopped, terminated, etc.)
- System status checks (ok, impaired, insufficient-data)
- Instance status checks (ok, impaired, insufficient-data)
- Scheduled events (reboot, retirement, etc.)

**Use cases**:
- Distinguish infrastructure vs. application failures
- Find scheduled AWS maintenance causing disruptions
- Detect failing system checks (hypervisor issues)

### `aws.ebs_health`

Get EBS volume health, IOPS performance, and throttling status.

**Arguments**:
- `volume_id` (optional): EBS volume ID (auto-discovered from PVC if available)
- `region` (optional): AWS region

**Example**:
```
User: "Is the EBS volume being throttled?"
Agent: [calls aws.ebs_health with volume_id=vol-xyz789]
Agent: "Volume vol-xyz789 (gp3, 3000 IOPS) is OK. No throttling detected."
```

**Returns**:
- Volume status (ok, impaired, insufficient-data)
- Volume type (gp2, gp3, io1, io2, etc.)
- Provisioned IOPS
- Performance warnings (throttling, degraded)

**Use cases**:
- Diagnose I/O performance issues
- Identify volume throttling (IOPS/throughput limits)
- Check for degraded volumes

### `aws.elb_health`

Get load balancer target health status.

**Arguments**:
- `load_balancer` (optional): Classic Load Balancer name (auto-discovered if available)
- `target_group_arn` (optional): ALB/NLB target group ARN (auto-discovered if available)
- `region` (optional): AWS region

**Example**:
```
User: "Are all load balancer targets healthy?"
Agent: [calls aws.elb_health with target_group_arn=arn:aws:...]
Agent: "3/5 targets healthy. 2 targets failing health checks: i-123 (connection timeout), i-456 (unhealthy threshold breached)."
```

**Returns**:
- Healthy/unhealthy target counts
- Per-target health status
- Health check failure reasons
- Target IDs (instance IDs or IP addresses)

**Use cases**:
- Diagnose load balancer routing issues
- Find unhealthy targets causing 502/503 errors
- Correlate pod issues with LB health checks

### `aws.rds_status`

Get RDS instance status and pending maintenance events.

**Arguments**:
- `db_instance_id` (optional): RDS instance identifier (auto-discovered if available)
- `region` (optional): AWS region

**Example**:
```
User: "Is the RDS instance available?"
Agent: [calls aws.rds_status with db_instance_id=mydb]
Agent: "RDS instance 'mydb' is available. Pending maintenance: OS update scheduled for Feb 20 at 02:00 UTC."
```

**Returns**:
- Instance status (available, backing-up, maintenance, etc.)
- Database engine and version
- Pending maintenance events
- Multi-AZ status

**Use cases**:
- Correlate database issues with maintenance windows
- Check if database is in backup/maintenance mode
- Verify database availability

### `aws.ecr_image`

Get ECR image scan findings and repository policies.

**Arguments**:
- `repository` (optional): ECR repository name (auto-discovered from pod image)
- `image_tag` (optional): Image tag (auto-discovered from pod image)
- `region` (optional): AWS region

**Example**:
```
User: "Are there vulnerabilities in the container image?"
Agent: [calls aws.ecr_image with repository=myapp, image_tag=v1.2.3]
Agent: "Image scan found 3 critical, 7 high vulnerabilities. Most recent: CVE-2024-1234 (OpenSSL)."
```

**Returns**:
- Image scan findings (critical, high, medium, low, informational)
- CVE details
- Repository policy (for ImagePullBackOff troubleshooting)

**Use cases**:
- Diagnose ImagePullBackOff due to repository permissions
- Find security vulnerabilities in running images
- Verify image scan status

### `aws.security_group`

Get security group inbound/outbound rules.

**Arguments**:
- `security_group_id` (optional): Security group ID (auto-discovered from node labels)
- `region` (optional): AWS region

**Example**:
```
User: "What are the security group rules for this instance?"
Agent: [calls aws.security_group with security_group_id=sg-abc123]
Agent: "Security group allows inbound: 80/tcp from 0.0.0.0/0, 443/tcp from 0.0.0.0/0. Outbound: all traffic."
```

**Returns**:
- Inbound rules (protocol, port, source CIDR/SG)
- Outbound rules (protocol, port, destination CIDR/SG)

**Use cases**:
- Diagnose connectivity issues (blocked ports)
- Verify network security posture
- Troubleshoot inter-service communication failures

### `aws.nat_gateway`

Get NAT gateway status (for egress connectivity issues).

**Arguments**:
- `nat_gateway_id` (optional): NAT gateway ID (auto-discovered from VPC config)
- `region` (optional): AWS region

**Example**:
```
User: "Is the NAT gateway healthy?"
Agent: [calls aws.nat_gateway with nat_gateway_id=nat-abc123]
Agent: "NAT gateway nat-abc123 is available. No connection issues detected."
```

**Returns**:
- NAT gateway state (available, pending, deleting, deleted, failed)
- VPC ID
- Subnet ID

**Use cases**:
- Diagnose egress connectivity failures
- Verify NAT gateway availability
- Troubleshoot outbound API call timeouts

### `aws.vpc_endpoint`

Get VPC endpoint status (for private link issues).

**Arguments**:
- `vpc_endpoint_id` (optional): VPC endpoint ID (auto-discovered from VPC config)
- `region` (optional): AWS region

**Example**:
```
User: "Is the VPC endpoint for S3 working?"
Agent: [calls aws.vpc_endpoint with vpc_endpoint_id=vpce-abc123]
Agent: "VPC endpoint vpce-abc123 (S3 gateway) is available."
```

**Returns**:
- Endpoint state (available, pending, deleting, deleted, failed)
- Endpoint type (Interface, Gateway)
- Service name
- Route table IDs

**Use cases**:
- Diagnose private link connectivity issues
- Verify endpoint availability for AWS services
- Troubleshoot S3/DynamoDB access from VPC

---

## GitHub Tools

GitHub tools provide code change context during investigations. Requires GitHub App setup.

**Policy gate**: `CHAT_ALLOW_GITHUB_READ=1` (default: false)

**Repo allowlist**: `CHAT_GITHUB_REPO_ALLOWLIST=myorg/repo1,myorg/repo2` (optional)

**Setup**: See [GitHub App Setup Guide](guides/github-app-setup.md).

### `github.recent_commits`

Query commit history for a repository.

**Arguments**:
- `repo` (optional): GitHub repo in `org/repo` format (auto-discovered if available)
- `since` (optional): ISO8601 timestamp (defaults to 2h ago)

**Example**:
```
User: "What commits were made recently?"
Agent: [calls github.recent_commits with repo=myorg/my-service]
Agent: "3 commits in the last 2 hours:
- abc1234 by alice: 'fix: increase connection pool size' (15m ago)
- def5678 by bob: 'feat: add retry logic' (1h ago)
- ghi9012 by charlie: 'chore: update dependencies' (1h30m ago)"
```

**Returns**:
- Commit SHA (short form)
- Author
- Commit message
- Timestamp
- URL

**Limits**:
- Max commits: 10 (most recent)
- Time window: configurable via `since` argument

**Use cases**:
- Correlate failures with recent code changes
- Find commits that might have introduced issues
- Understand what changed before alert fired

### `github.workflow_runs`

Query GitHub Actions workflow run status.

**Arguments**:
- `repo` (optional): GitHub repo in `org/repo` format (auto-discovered)
- `since` (optional): ISO8601 timestamp (defaults to 2h ago)

**Example**:
```
User: "Did any builds fail recently?"
Agent: [calls github.workflow_runs with repo=myorg/my-service]
Agent: "Recent builds:
- ✅ CI #12345: success (10m ago)
- ❌ Deploy #12346: failure (15m ago) - failed job: build-and-push
- ✅ Tests #12344: success (30m ago)"
```

**Returns**:
- Workflow name
- Run ID
- Status (queued, in_progress, completed)
- Conclusion (success, failure, cancelled, skipped)
- Created/updated timestamps
- Job details (for failed runs)

**Limits**:
- Max runs: 5 (most recent)

**Use cases**:
- Diagnose deployment failures
- Correlate incidents with build failures
- Find recent CI/CD issues

### `github.workflow_logs`

Fetch logs from a specific workflow run.

**Arguments**:
- `repo` (required): GitHub repo in `org/repo` format
- `run_id` (required): Workflow run ID
- `job_id` (optional): Specific job ID (defaults to first failed job)

**Example**:
```
User: "Show me the logs from the failed Deploy workflow"
Agent: [calls github.workflow_logs with repo=myorg/my-service, run_id=12346]
Agent: "Failed job 'build-and-push' logs:
Error: buildx failed with exit code 1
context canceled: connection timeout to registry.io"
```

**Returns**:
- First/last 20 lines of logs (40 total)
- Truncated for readability

**Use cases**:
- Diagnose build/deployment failures
- Find root cause of CI/CD issues
- Understand why deployments failed

### `github.read_file`

Fetch specific file contents from repository (e.g., README, runbook).

**Arguments**:
- `repo` (required): GitHub repo in `org/repo` format
- `path` (required): File path (e.g., `README.md`, `docs/runbook.md`)
- `ref` (optional): Branch/tag/commit (defaults to `main`)

**Example**:
```
User: "Show me the runbook for this service"
Agent: [calls github.read_file with repo=myorg/my-service, path=docs/runbook.md]
Agent: "Runbook says to check database connection pool size (currently 10, should be 50)."
```

**Returns**:
- File contents (raw text/markdown)

**Security**:
- Path validation prevents directory traversal
- Only files in allowed repos are accessible

**Use cases**:
- Access service documentation during incidents
- Read runbooks for troubleshooting steps
- Check configuration files

---

## Memory Tools

Memory tools provide case-based reasoning and learned patterns from past incidents.

**Policy gate**: `CHAT_ALLOW_MEMORY_READ=1`

**Requirements**: PostgreSQL with `MEMORY_ENABLED=1`

### `memory.similar_cases`

Find similar past incidents based on alert pattern, target, and symptoms.

**Arguments**:
- `limit` (optional): Max similar cases to return (default: 5)

**Example**:
```
User: "Has this happened before?"
Agent: [calls memory.similar_cases]
Agent: "Found 3 similar cases:
- Case #123 (2 weeks ago): Same OOMKilled alert, resolved by increasing memory limit
- Case #124 (1 month ago): Similar symptoms, resolved by fixing memory leak in v1.2.0
- Case #125 (3 months ago): Same service, resolved by database connection pool tuning"
```

**Returns**:
- Case ID and run ID
- One-liner summary
- Resolution category (config_change, code_fix, infra_issue, etc.)
- Resolution summary
- Postmortem link (if available)
- S3 report key

**Use cases**:
- Learn from past incidents
- Find proven solutions
- Avoid repeating investigations

### `memory.skills`

Match learned skills/patterns from past incidents.

**Arguments**:
- `query` (optional): Search query for skill matching

**Example**:
```
User: "What have we learned about OOMKilled alerts?"
Agent: [calls memory.skills with query=OOMKilled]
Agent: "Learned skill: 'OOMKilled alerts for service X are often caused by missing memory limits. Fix: add resources.limits.memory to Deployment.'"
```

**Returns**:
- Skill title
- Description
- Confidence score
- Evidence (which cases contributed to this skill)

**Use cases**:
- Surface institutional knowledge
- Find recurring patterns
- Learn team-specific troubleshooting techniques

---

## Report Tools

### `rerun.investigation`

Re-run investigation with different time window and reference time.

**Policy gate**: `CHAT_ALLOW_REPORT_RERUN=1`

**Arguments**:
- `time_window` (required): New time window (e.g., `30m`, `1h`, `2h`)
- `reference_time` (optional): Time reference mode
  - `original` (default): Investigate historical state when alert fired
  - `now`: Investigate current system state

**Modes**:

**Historical Mode** (`reference_time="original"`, default):
- Investigates system state at the time the alert originally fired
- Time window is calculated backward from original alert timestamp
- Useful for post-mortem analysis and understanding what caused the alert
- Example: Alert fired at 10:00am. `time_window="2h"` → investigate 8:00am-10:00am

**Current State Mode** (`reference_time="now"`):
- Investigates current system state using "now" as reference
- Time window is calculated backward from current time
- Useful to check if issue persists, has resolved, or evolved
- Example: Alert fired yesterday. `time_window="30m", reference_time="now"` → investigate last 30 minutes

**Examples**:

Historical investigation (default):
```
User: "Can you re-run with a 2 hour window to see earlier symptoms?"
Agent: [calls rerun.investigation with time_window="2h"]
Agent: "Re-ran investigation with 2h window from when alert fired. Found CPU throttling started 90m before alert."
```

Current state investigation:
```
User: "Has this issue been resolved? Show me current state."
Agent: [calls rerun.investigation with time_window="30m", reference_time="now"]
Agent: "Checked current state. CPU usage is normal now (45%), issue appears resolved."
```

Check if recurring issue:
```
User: "Is the job still failing?"
Agent: [calls rerun.investigation with time_window="15m", reference_time="now"]
Agent: "Checked recent jobs. No failures in last 15m, issue has not recurred."
```

**Use cases**:
- **Historical mode**: Post-mortem analysis, understand root cause, see what triggered alert
- **Current state mode**: Check if issue persists, verify resolution, monitor for recurrence
- Expand time window to find earlier symptoms
- Narrow time window to reduce noise

---

## Tool Auto-Discovery

Many tools support **auto-discovery** of arguments from the investigation context:

- **Pod/namespace**: Extracted from investigation target
- **AWS resource IDs**: Extracted from alert labels, node names, PVC metadata, container images
- **GitHub repo**: Discovered via 8-step fallback chain (annotations → alert labels → naming convention → catalogs)
- **Region**: Extracted from alert labels or environment variables

This allows users to ask natural questions like "What's the EC2 instance status?" without specifying instance IDs.

## Policy Enforcement

All tools respect policy configuration:

1. **Tool category gates**: Tools are disabled unless explicitly enabled via env vars
2. **Scope allowlists**: Restrict queries to specific namespaces/clusters/regions/repos
3. **Rate limits**: Max steps, max tool calls per conversation
4. **Redaction**: Secrets are redacted from tool outputs

See [Chat Features](features/chat.md) for policy configuration details.

## Error Handling

Tools return structured errors when operations fail:

- `tool_not_allowed`: Tool disabled by policy
- `pod_namespace_required`: Missing required arguments
- `region_not_allowed`: Region not in allowlist
- `repo_not_allowed`: Repo not in allowlist
- `aws_error:...`: AWS API error (e.g., `AccessDenied`, `ResourceNotFound`)
- `github_error:...`: GitHub API error (e.g., `NotFound`, `RateLimited`)
- `logs_error:...`: Logs query error (e.g., `Unavailable`, `Timeout`)

The agent will explain errors to the user and suggest next steps.
