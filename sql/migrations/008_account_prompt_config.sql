-- Migration 008: per-account prompt customization (Layer 3 of the
-- 3-layer prompt architecture).
--
-- Stores per-account customization applied on top of the hardcoded
-- Layer 1 (system rules) and Layer 2 (task config). An empty / missing
-- row means "use defaults" — backward compatible.

CREATE TABLE IF NOT EXISTS account_prompt_config (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_account_prompt_config_updated
    ON account_prompt_config(updated_at DESC);
