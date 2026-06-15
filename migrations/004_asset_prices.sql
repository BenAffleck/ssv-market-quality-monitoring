-- SSV correlation/beta: daily reference-asset close prices + returns view.
-- Idempotent: safe to run on every startup and as a docker init script.
-- One row per (day, asset). Prices are single-venue daily UTC closes (CCXT 1d candle), so
-- all assets align on the same UTC `day` by construction. `source` records the venue:symbol
-- used (audit, like samples.fx_rate). The asset_returns view derives daily returns and
-- mirrors ssv_mqm.correlation's return math for Grafana (same Python<->SQL split as
-- aggregate_day / benchmark_comparison).

CREATE TABLE IF NOT EXISTS asset_prices (
    day         DATE             NOT NULL,
    asset       TEXT             NOT NULL,   -- logical id: SSV, ETH, RPL, ...
    close_usd   DOUBLE PRECISION NOT NULL,
    source      TEXT             NOT NULL,   -- audit: "<exchange>:<symbol>"
    computed_at TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (day, asset)
);

CREATE INDEX IF NOT EXISTS asset_prices_asset_day_idx
    ON asset_prices (asset, day DESC);

-- ---------------------------------------------------------------------------
-- Daily returns per asset. The first day per asset has no prior close -> NULL (dropped
-- downstream). log_ret is the basis for correlation/beta (additive, standard); ret (simple)
-- is exposed for price-change panels. NULLIF guards against a zero prior close.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW asset_returns AS
SELECT
    day,
    asset,
    close_usd,
    close_usd / NULLIF(lag(close_usd) OVER w, 0) - 1.0 AS ret,
    ln(close_usd / NULLIF(lag(close_usd) OVER w, 0))   AS log_ret
FROM asset_prices
WINDOW w AS (PARTITION BY asset ORDER BY day);
