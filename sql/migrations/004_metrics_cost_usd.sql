-- Migration 004: Add cost_usd to metrics table
-- Tracks API cost per event for daily/weekly cost reports.

ALTER TABLE metrics ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10,6) DEFAULT 0;

-- Index for cost aggregation queries (by date + account)
CREATE INDEX IF NOT EXISTS idx_metrics_account_created
    ON metrics(account_id, created_at);
