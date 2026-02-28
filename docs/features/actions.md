# Action proposals (policy-gated)

This feature adds a **safe workflow** for the agent to suggest mitigations without executing them automatically.

## Goals

- **No autonomous execution by default**
- **Approval required** (auditable)
- **Scoped** by namespace/cluster allowlists (optional)

## Policy (env / ConfigMap)

Set:
- `ACTIONS_ENABLED=1` to enable the feature

Optional hardening:
- `ACTIONS_TYPE_ALLOWLIST=restart_pod,rollout_restart,scale_workload,rollback_workload`
- `ACTIONS_REQUIRE_APPROVAL=1` (default)
- `ACTIONS_ALLOW_EXECUTE=0` (default)
- `ACTIONS_NAMESPACE_ALLOWLIST=prod,staging`
- `ACTIONS_CLUSTER_ALLOWLIST=cluster-a,cluster-b`

## API

- `GET /api/v1/actions/config`
- `GET /api/v1/cases/{case_id}/actions`
- `POST /api/v1/cases/{case_id}/actions/propose`
- `POST /api/v1/cases/{case_id}/actions/{action_id}/approve`
- `POST /api/v1/cases/{case_id}/actions/{action_id}/reject`
- `POST /api/v1/cases/{case_id}/actions/{action_id}/execute` (only when `ACTIONS_ALLOW_EXECUTE=1`)

## Storage

Backed by Postgres table `case_actions` (see `agent/memory/migrations/002_actions.sql`).
