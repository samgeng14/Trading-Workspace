-- 006_nav.sql
-- Daily NAV, one row per profile, day and source: the account's net
-- liquidation value at the close from IBKR Flex, from the one-time
-- PortfolioAnalyst import (the account's first weeks, before Flex has it),
-- or recorded by the API at the close. The equity curve and the Phase 0 exit
-- test (NAV matching IBKR's statement) read this once Postgres is wired.

CREATE TABLE IF NOT EXISTS nav (
    id              BIGSERIAL    PRIMARY KEY,
    profile         TEXT         NOT NULL,
    nav_date        DATE         NOT NULL,
    nav             DOUBLE PRECISION NOT NULL,
    cash            DOUBLE PRECISION,
    source          TEXT         NOT NULL,            -- 'flex' | 'portfolio_analyst' | 'api_close'
    inserted_at     TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The same day may come from two sources (Flex and the API's close); readers
-- prefer Flex. One row per (profile, day, source).
CREATE UNIQUE INDEX IF NOT EXISTS ux_nav_profile_date_source
    ON nav (profile, nav_date, source);

CREATE INDEX IF NOT EXISTS ix_nav_profile_date
    ON nav (profile, nav_date);
