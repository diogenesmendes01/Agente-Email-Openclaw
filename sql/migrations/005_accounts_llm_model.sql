-- Migration 005: Add per-account LLM model selection
-- Allows each account to use a different model and fallback.
-- NULL = use global default from LLM_MODEL env var.

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS llm_model VARCHAR(255);
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS llm_fallback_model VARCHAR(255);
