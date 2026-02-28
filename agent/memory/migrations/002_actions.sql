-- 002_actions.sql: case action proposals + approval/execution audit trail

CREATE TABLE IF NOT EXISTS case_actions (
  action_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
  run_id uuid REFERENCES investigation_runs(run_id) ON DELETE SET NULL,

  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  status text NOT NULL DEFAULT 'proposed', -- proposed|approved|rejected|executed

  hypothesis_id text,

  action_type text NOT NULL,
  title text NOT NULL,
  risk text,
  preconditions jsonb NOT NULL DEFAULT '[]'::jsonb,
  execution_payload jsonb NOT NULL DEFAULT '{}'::jsonb,

  proposed_by text,
  approved_at timestamptz,
  approved_by text,
  approval_notes text,

  executed_at timestamptz,
  executed_by text,
  execution_notes text
);

CREATE INDEX IF NOT EXISTS case_actions_case_idx
  ON case_actions (case_id, created_at DESC);

CREATE INDEX IF NOT EXISTS case_actions_status_idx
  ON case_actions (status, updated_at DESC);
