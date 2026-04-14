-- Migration 002: Idempotency constraints and playbook uniqueness
-- Run: psql $DATABASE_URL -f sql/migrations/002_idempotency_constraints.sql
-- Safe to re-run: uses DO $$ blocks with IF NOT EXISTS checks.

-- ── decisions: prevent duplicate processing of the same email ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'decisions_account_id_email_id_key'
    ) THEN
        -- Remove duplicates first (keep the earliest)
        DELETE FROM decisions d1
        USING decisions d2
        WHERE d1.account_id = d2.account_id
          AND d1.email_id = d2.email_id
          AND d1.id > d2.id;

        ALTER TABLE decisions ADD CONSTRAINT decisions_account_id_email_id_key UNIQUE (account_id, email_id);
    END IF;
END $$;

-- ── playbooks: prevent duplicate triggers per company ──
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'playbooks_company_id_trigger_description_key'
    ) THEN
        -- Remove duplicates first (keep the earliest)
        DELETE FROM playbooks p1
        USING playbooks p2
        WHERE p1.company_id = p2.company_id
          AND p1.trigger_description = p2.trigger_description
          AND p1.id > p2.id;

        ALTER TABLE playbooks ADD CONSTRAINT playbooks_company_id_trigger_description_key UNIQUE (company_id, trigger_description);
    END IF;
END $$;
