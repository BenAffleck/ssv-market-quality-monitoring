-- SSV market-quality monitoring schema (PRD P0-4).
-- Idempotent: safe to run on every startup and as the docker init script.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Per-sample computed metrics. Depth is stored as bid/ask components per band
-- so the published total (bid + ask) is reproducible (P0-2).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS samples (
    time          TIMESTAMPTZ      NOT NULL,
    exchange      TEXT             NOT NULL,
    symbol        TEXT             NOT NULL,
    best_bid      DOUBLE PRECISION NOT NULL,
    best_ask      DOUBLE PRECISION NOT NULL,
    mid           DOUBLE PRECISION NOT NULL,
    spread        DOUBLE PRECISION NOT NULL,   -- fraction; (ask-bid)/mid
    depth_100_bid DOUBLE PRECISION NOT NULL,   -- USD notional, +/-100 bps, bid side
    depth_100_ask DOUBLE PRECISION NOT NULL,
    depth_200_bid DOUBLE PRECISION NOT NULL,   -- USD notional, +/-200 bps, bid side
    depth_200_ask DOUBLE PRECISION NOT NULL,
    is_crossed    BOOLEAN          NOT NULL DEFAULT FALSE,
    PRIMARY KEY (exchange, symbol, time)
);

-- Convert to a hypertable partitioned on time (no-op if already one).
SELECT create_hypertable('samples', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS samples_market_time_idx
    ON samples (exchange, symbol, time DESC);

-- Retention: keep computed samples 90 days. Raw L2 snapshots are NOT stored (P1).
SELECT add_retention_policy('samples', INTERVAL '90 days', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Daily aggregates per (exchange, symbol) (P0-3). Spread published as a
-- percentage rounded to 2 decimals; coverage protects the source-of-truth goal.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_aggregates (
    day                      DATE             NOT NULL,
    exchange                 TEXT             NOT NULL,
    symbol                   TEXT             NOT NULL,
    avg_spread_pct           NUMERIC(8,2)     NOT NULL,
    avg_depth_100_usd        DOUBLE PRECISION NOT NULL,
    avg_depth_200_usd        DOUBLE PRECISION NOT NULL,
    avg_depth_100_bid        DOUBLE PRECISION NOT NULL,
    avg_depth_100_ask        DOUBLE PRECISION NOT NULL,
    avg_depth_200_bid        DOUBLE PRECISION NOT NULL,
    avg_depth_200_ask        DOUBLE PRECISION NOT NULL,
    samples_captured         INTEGER          NOT NULL,
    samples_expected         INTEGER          NOT NULL,
    coverage_pct             NUMERIC(5,2)     NOT NULL,
    crossed_samples_excluded INTEGER          NOT NULL DEFAULT 0,
    low_coverage             BOOLEAN          NOT NULL DEFAULT FALSE,
    computed_at              TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (day, exchange, symbol)
);

CREATE INDEX IF NOT EXISTS daily_aggregates_day_idx
    ON daily_aggregates (day DESC);
