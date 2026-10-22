-- 007_record_keys.sql
-- What the engine's record needs that 001-006 didn't have.

-- IBKR reports fractional shares (ETHA 986.3333 after its 1-for-3 reverse
-- split): positions and fills hold numbers, not integers.
ALTER TABLE positions ALTER COLUMN qty TYPE DOUBLE PRECISION;
ALTER TABLE fills ALTER COLUMN qty TYPE DOUBLE PRECISION;

-- A fill names its order by IBKR's permId, unique across clients and days
-- (orderId repeats per client).
ALTER TABLE fills ADD COLUMN IF NOT EXISTS broker_perm_id BIGINT;
CREATE INDEX IF NOT EXISTS ix_fills_perm_id ON fills (broker_perm_id);
CREATE INDEX IF NOT EXISTS ix_orders_perm_id ON orders (broker_perm_id);

-- A run is keyed by the runner's run_id (YYYYmmdd_HHMMSS), so recording it
-- again (a backfill, a retry) updates the row instead of adding one.
ALTER TABLE run_logs ADD COLUMN IF NOT EXISTS run_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_run_logs_job_profile_run ON run_logs (job, profile, run_id);
