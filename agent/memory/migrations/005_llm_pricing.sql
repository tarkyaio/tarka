-- LLM pricing: model-level token cost lookup with auto-discovery support.

CREATE TABLE IF NOT EXISTS llm_pricing (
  model_pattern      TEXT PRIMARY KEY,
  provider           TEXT,                              -- "anthropic", "google", "openai", ...
  input_cost_per_1m  DOUBLE PRECISION NOT NULL,         -- USD per 1M input tokens
  output_cost_per_1m DOUBLE PRECISION NOT NULL,         -- USD per 1M output tokens
  source             TEXT NOT NULL DEFAULT 'seed',      -- "seed" | "litellm" | "manual"
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed: family-level prefixes for day-1 coverage without internet
INSERT INTO llm_pricing (model_pattern, provider, input_cost_per_1m, output_cost_per_1m, source) VALUES
  ('gemini-2.5-flash', 'google',    0.15,  0.60,  'seed'),
  ('gemini-2.0-flash', 'google',    0.10,  0.40,  'seed'),
  ('gemini-1.5-flash', 'google',    0.075, 0.30,  'seed'),
  ('gemini-1.5-pro',   'google',    1.25,  5.00,  'seed'),
  ('gemini-2.5-pro',   'google',    1.25,  10.00, 'seed'),
  ('claude-sonnet',    'anthropic', 3.00,  15.00, 'seed'),
  ('claude-haiku',     'anthropic', 0.80,  4.00,  'seed'),
  ('claude-opus',      'anthropic', 15.00, 75.00, 'seed')
ON CONFLICT (model_pattern) DO NOTHING;
