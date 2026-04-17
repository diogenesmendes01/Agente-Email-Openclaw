-- Migration 006: LLM output quality log
-- Captures per-email telemetry from the post-LLM validation pipeline:
-- schema validation, semantic flags, retries, and fallback usage.

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
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_quality_log_account_kind
    ON llm_quality_log(account_id, kind, created_at DESC);
