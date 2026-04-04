-- Slack thread ↔ Tarka chat thread mapping (Phase 2 chat bridging).
CREATE TABLE IF NOT EXISTS slack_thread_map (
    id            SERIAL PRIMARY KEY,
    slack_channel TEXT        NOT NULL,
    slack_thread_ts TEXT      NOT NULL,
    tarka_thread_id UUID,
    case_id       UUID,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(slack_channel, slack_thread_ts)
);

CREATE INDEX IF NOT EXISTS idx_slack_thread_map_case
    ON slack_thread_map (case_id) WHERE case_id IS NOT NULL;
