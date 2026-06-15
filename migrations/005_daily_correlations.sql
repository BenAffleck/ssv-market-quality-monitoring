-- SSV correlation/beta: materialized rolling correlation & beta of each asset vs a
-- benchmark asset (ETH). Idempotent: safe to run on every startup and as a docker init
-- script. Written by the aggregator from pure ssv_mqm.correlation (the canonical rule);
-- Grafana reads this table directly. beta = slope of the asset's daily log returns regressed
-- on the benchmark's (ETH is the independent variable). r2 = correlation^2. correlation/beta/
-- r2 are NULL until the rolling window holds enough aligned observations.

CREATE TABLE IF NOT EXISTS daily_correlations (
    day         DATE             NOT NULL,
    asset       TEXT             NOT NULL,
    benchmark   TEXT             NOT NULL,   -- the reference asset (e.g. ETH)
    window_days INTEGER          NOT NULL,   -- rolling lookback length in trading days
    correlation DOUBLE PRECISION,            -- Pearson r in [-1, 1]
    beta        DOUBLE PRECISION,            -- slope of asset on benchmark returns
    r2          DOUBLE PRECISION,            -- = correlation^2
    n_obs       INTEGER          NOT NULL,   -- aligned return pairs in the window
    computed_at TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (day, asset, benchmark, window_days)
);

CREATE INDEX IF NOT EXISTS daily_correlations_day_idx
    ON daily_correlations (day DESC);
