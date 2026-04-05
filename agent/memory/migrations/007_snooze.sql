-- 007_snooze.sql
-- Adds snooze support and resolution attribution to cases.

ALTER TABLE cases ADD COLUMN IF NOT EXISTS snoozed_until timestamptz;

-- Partial index — only indexes rows that are actually snoozed (small, fast)
CREATE INDEX IF NOT EXISTS cases_snoozed_idx ON cases (snoozed_until)
  WHERE snoozed_until IS NOT NULL;

ALTER TABLE cases ADD COLUMN IF NOT EXISTS resolution_method text;
-- 'manual'  = SRE explicitly closed it via the UI
-- 'auto'    = Alertmanager sent a resolved webhook and Tarka auto-closed it
-- NULL      = legacy cases created before this migration
