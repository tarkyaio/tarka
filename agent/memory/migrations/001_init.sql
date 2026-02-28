-- 001_init.sql: squashed schema for dev experimentation (case store + investigation runs + skills).
-- NOTE: This is intended for fresh/dev databases (e.g., emptyDir-backed Postgres).
-- If you need long-lived RDS migrations later, re-introduce incremental migrations.

-- UUIDs
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Optional: vectors for semantic retrieval. If not available, ignore.
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION
  WHEN undefined_file THEN
    RAISE NOTICE 'pgvector extension not available; skipping';
  WHEN insufficient_privilege THEN
    RAISE NOTICE 'insufficient privilege to CREATE EXTENSION vector; skipping';
END$$;

CREATE TABLE IF NOT EXISTS cases (
  case_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Stable case identifier used for concurrency-safe upserts
  case_key text NOT NULL,
  status text NOT NULL DEFAULT 'open',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  cluster text,
  target_type text,
  namespace text,
  workload_kind text,
  workload_name text,
  service text,
  instance text,

  family text,
  primary_driver text,
  latest_one_liner text,

  -- Canonical pointers for this case (one per case).
  s3_report_key text,
  s3_investigation_key text,

  resolved_at timestamptz,
  resolution_summary text,
  resolution_category text,
  postmortem_link text
);

CREATE UNIQUE INDEX IF NOT EXISTS cases_case_key_uq_all
  ON cases (case_key);

CREATE INDEX IF NOT EXISTS cases_lookup_idx
  ON cases (status, updated_at DESC, cluster, namespace, workload_kind, workload_name, service, instance, family);

CREATE TABLE IF NOT EXISTS investigation_runs (
  run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),

  alert_fingerprint text,
  alertname text,
  severity text,
  starts_at text,
  normalized_state text,

  target_type text,
  cluster text,
  namespace text,
  pod text,
  container text,
  workload_kind text,
  workload_name text,
  service text,
  instance text,

  family text,
  classification text,
  primary_driver text,
  one_liner text,
  reason_codes text[],

  s3_report_key text,
  s3_investigation_key text,

  analysis_json jsonb,
  report_text text,

  case_match_reason text NOT NULL DEFAULT 'unknown'
);

CREATE INDEX IF NOT EXISTS investigation_runs_fp_idx
  ON investigation_runs (alert_fingerprint);

CREATE INDEX IF NOT EXISTS investigation_runs_case_idx
  ON investigation_runs (case_id, created_at DESC);

CREATE INDEX IF NOT EXISTS investigation_runs_lookup_idx
  ON investigation_runs (created_at DESC, cluster, namespace, workload_kind, workload_name, service, instance, family, alertname);

CREATE TABLE IF NOT EXISTS skills (
  skill_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  version int NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  -- Predicate expressed as constrained JSON logic evaluated by the agent.
  when_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Template rendered into report markdown.
  template text NOT NULL DEFAULT '',
  -- Freeform provenance (e.g., case IDs / run IDs / notes)
  provenance jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS skills_name_version_uq
  ON skills (name, version);

CREATE INDEX IF NOT EXISTS skills_status_idx
  ON skills (status);

CREATE TABLE IF NOT EXISTS skill_feedback (
  feedback_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),

  case_id uuid REFERENCES cases(case_id) ON DELETE SET NULL,
  run_id uuid REFERENCES investigation_runs(run_id) ON DELETE SET NULL,
  skill_id uuid REFERENCES skills(skill_id) ON DELETE SET NULL,

  outcome text NOT NULL,
  notes text,
  actor text
);
