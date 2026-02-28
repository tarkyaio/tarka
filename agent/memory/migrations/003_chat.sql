-- 003_chat.sql: per-user chat threads + messages (+ optional tool events)
-- This enables server-persisted chat history for the Console UI.

-- Threads are per-user and either:
-- - kind='global' (one per user)
-- - kind='case'   (one per (user, case))
CREATE TABLE IF NOT EXISTS chat_threads (
  thread_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_key text NOT NULL,
  kind text NOT NULL, -- 'global' | 'case'
  case_id uuid REFERENCES cases(case_id) ON DELETE CASCADE,
  title text,

  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_message_at timestamptz
);

-- Ensure thread kind is valid and case_id presence matches kind.
-- NOTE: This avoids ambiguous semantics and helps keep UI switching reliable.
ALTER TABLE chat_threads
  ADD CONSTRAINT chat_threads_kind_check
  CHECK (kind IN ('global', 'case'));

ALTER TABLE chat_threads
  ADD CONSTRAINT chat_threads_case_id_check
  CHECK (
    (kind = 'global' AND case_id IS NULL)
    OR
    (kind = 'case' AND case_id IS NOT NULL)
  );

-- Enforce uniqueness:
-- - one global thread per user (case_id is NULL, so we must use a partial unique index)
-- - one case thread per (user, case_id)
CREATE UNIQUE INDEX IF NOT EXISTS chat_threads_user_global_uq
  ON chat_threads (user_key)
  WHERE kind = 'global';

CREATE UNIQUE INDEX IF NOT EXISTS chat_threads_user_case_uq
  ON chat_threads (user_key, case_id)
  WHERE kind = 'case';

CREATE INDEX IF NOT EXISTS chat_threads_user_updated_idx
  ON chat_threads (user_key, updated_at DESC);

CREATE INDEX IF NOT EXISTS chat_threads_case_idx
  ON chat_threads (case_id, updated_at DESC);

-- Messages are stored in strict order per thread (`seq`).
CREATE TABLE IF NOT EXISTS chat_messages (
  message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id uuid NOT NULL REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
  seq int NOT NULL,
  role text NOT NULL, -- 'user' | 'assistant'
  content text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_thread_seq_uq
  ON chat_messages (thread_id, seq);

CREATE INDEX IF NOT EXISTS chat_messages_thread_created_idx
  ON chat_messages (thread_id, created_at DESC);

-- Optional: tool events, useful for debug/prod support (not always displayed).
CREATE TABLE IF NOT EXISTS chat_tool_events (
  event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  thread_id uuid NOT NULL REFERENCES chat_threads(thread_id) ON DELETE CASCADE,
  message_id uuid REFERENCES chat_messages(message_id) ON DELETE SET NULL,
  tool text NOT NULL,
  args jsonb NOT NULL DEFAULT '{}'::jsonb,
  ok bool NOT NULL DEFAULT false,
  result jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_tool_events_thread_created_idx
  ON chat_tool_events (thread_id, created_at DESC);
