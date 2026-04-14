-- Migration 003: Add owner_name to accounts
-- Allows the LLM to know who owns each email account,
-- preventing nonsensical responses like replying to yourself.

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS owner_name VARCHAR(255);
