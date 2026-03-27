-- Migration: 005_alerts_prospect_id
-- Add prospect_id column to alerts table for score-change alert tracking.
-- Also add a type column for categorizing alert types (score_spike, tier_up, tier_down).
-- Run in Supabase SQL Editor or via migration script.

-- 1. Add prospect_id column (nullable, no FK to avoid circular deps)
ALTER TABLE alerts
ADD COLUMN IF NOT EXISTS prospect_id uuid;

CREATE INDEX IF NOT EXISTS idx_alerts_prospect_id ON alerts(prospect_id)
WHERE prospect_id IS NOT NULL;

-- 2. Add type column for alert categorization (score_spike, tier_up, tier_down, etc.)
ALTER TABLE alerts
ADD COLUMN IF NOT EXISTS type text DEFAULT 'general';

CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(type);

-- 3. Add title and message columns for richer alert display
ALTER TABLE alerts
ADD COLUMN IF NOT EXISTS title text;

ALTER TABLE alerts
ADD COLUMN IF NOT EXISTS message text;

ALTER TABLE alerts
ADD COLUMN IF NOT EXISTS payload jsonb DEFAULT '{}';

-- 4. RLS: allow service role to insert alerts with prospect_id
-- (RLS policies already allow authenticated users to insert alerts)

COMMENT ON COLUMN alerts.prospect_id IS 'UUID of the related prospect (for score-change alerts). Null for entity-based alerts.';
COMMENT ON COLUMN alerts.type IS 'Alert category: score_spike, tier_up, tier_down, keyword, threshold, etc.';
COMMENT ON COLUMN alerts.title IS 'Human-readable alert title for UI display.';
COMMENT ON COLUMN alerts.message IS 'Human-readable alert message/body.';
COMMENT ON COLUMN alerts.payload IS 'Structured alert data (score values, tier values, change amounts).';
