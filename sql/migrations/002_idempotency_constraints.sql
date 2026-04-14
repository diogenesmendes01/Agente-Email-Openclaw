-- Migration 002: Add idempotency constraints for decisions and playbooks.
-- Safe to re-run: uses CREATE UNIQUE INDEX IF NOT EXISTS and DO NOTHING approach.
-- Run: psql $DATABASE_URL -f sql/migrations/002_idempotency_constraints.sql

-- ── decisions: account_id NOT NULL + unique per (account_id, email_id) ──

-- Backfill: remove orphan rows without account_id before adding NOT NULL
DELETE FROM decisions WHERE account_id IS NULL;

ALTER TABLE decisions ALTER COLUMN account_id SET NOT NULL;

-- Deduplicate before adding unique constraint (keep the latest row per pair)
DELETE FROM decisions d1
USING decisions d2
WHERE d1.account_id = d2.account_id
  AND d1.email_id = d2.email_id
  AND d1.id < d2.id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_account_email
    ON decisions(account_id, email_id);

-- ── playbooks: unique per (company_id, trigger_description) ──

-- Deduplicate before adding unique constraint (keep the latest row per pair)
DELETE FROM playbooks p1
USING playbooks p2
WHERE p1.company_id = p2.company_id
  AND p1.trigger_description = p2.trigger_description
  AND p1.id < p2.id;

CREATE UNIQUE INDEX IF NOT EXISTS idx_playbooks_company_trigger
    ON playbooks(company_id, trigger_description);
