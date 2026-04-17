-- Migration 006: LLM output quality log
-- Captures per-email telemetry from the post-LLM validation pipeline:
-- schema validation, semantic flags, retries, and fallback usage.
--
-- Token accounting note:
--   prompt_tokens / completion_tokens represent the TOTAL across all
--   attempts (original + retries). These are the numbers that should
--   match billing. prompt_tokens_successful / completion_tokens_successful
--   hold just the accepted attempt's tokens, for retry-efficiency analysis.

CREATE TABLE IF NOT EXISTS llm_quality_log (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    email_message_id TEXT,
    kind TEXT NOT NULL,  -- classification | summary | action
    model TEXT,
    retries INTEGER DEFAULT 0,
    flags TEXT[],
    json_parse_failed BOOLEAN DEFAULT FALSE,
    schema_valid BOOLEAN DEFAULT TRUE,
    fallback_used BOOLEAN DEFAULT FALSE,
    -- Totals (sum of all attempts including retries):
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    -- Granular fields added for retry-cost visibility:
    prompt_tokens_successful INTEGER,
    completion_tokens_successful INTEGER,
    prompt_tokens_total INTEGER,
    completion_tokens_total INTEGER,
    cost_total_usd NUMERIC(10, 6),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_quality_log_account_kind
    ON llm_quality_log(account_id, kind, created_at DESC);

-- Idempotent ALTERs for databases that already have the pre-retry version of
-- this table (e.g. PR #9 deployed before this follow-up). Safe to run twice.
ALTER TABLE llm_quality_log
    ADD COLUMN IF NOT EXISTS prompt_tokens_successful INTEGER,
    ADD COLUMN IF NOT EXISTS completion_tokens_successful INTEGER,
    ADD COLUMN IF NOT EXISTS prompt_tokens_total INTEGER,
    ADD COLUMN IF NOT EXISTS completion_tokens_total INTEGER,
    ADD COLUMN IF NOT EXISTS cost_total_usd NUMERIC(10, 6);
