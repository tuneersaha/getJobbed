-- Sprint 2 additions
-- Add last_fetched_at: lets scheduler skip warm/cold companies already fetched this cycle
-- Add consecutive_empty_runs: tracks dead-company detection (≥5 → mark dead)

ALTER TABLE companies ADD COLUMN IF NOT EXISTS last_fetched_at         TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS consecutive_empty_runs  SMALLINT NOT NULL DEFAULT 0;
