-- SSV market-quality benchmarks: per-(exchange, symbol) targets + comparison view.
-- Idempotent: safe to run on every startup and as a docker init script.
-- The table mirrors config.yaml `benchmarks` (seeded on service startup); the view is
-- what Grafana JOINs actuals against. The over/under rule mirrors ssv_mqm.benchmark.

-- ---------------------------------------------------------------------------
-- Benchmark targets. Each metric target is optional (NULL = not benchmarked).
-- Spread target is a max in percent (lower = better, like avg_spread_pct);
-- depth targets are mins in USD (higher = better).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS benchmark_targets (
    exchange          TEXT             NOT NULL,
    symbol            TEXT             NOT NULL,
    max_spread_pct    NUMERIC(8,2),
    min_depth_100_usd DOUBLE PRECISION,
    min_depth_200_usd DOUBLE PRECISION,
    updated_at        TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (exchange, symbol)
);

-- ---------------------------------------------------------------------------
-- Target vs. actual comparison. LEFT JOIN so markets without a target still show
-- their actuals (with NULL target/status). delta = actual - target (signed);
-- *_met is the rule outcome; *_status is 'over' (beats target) / 'under' (misses).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW benchmark_comparison AS
SELECT
    da.day,
    da.exchange,
    da.symbol,
    da.avg_spread_pct,
    da.avg_depth_100_usd,
    da.avg_depth_200_usd,
    da.coverage_pct,
    da.low_coverage,
    bt.max_spread_pct,
    bt.min_depth_100_usd,
    bt.min_depth_200_usd,
    (da.avg_spread_pct    - bt.max_spread_pct)    AS spread_delta,
    (da.avg_depth_100_usd - bt.min_depth_100_usd) AS depth_100_delta,
    (da.avg_depth_200_usd - bt.min_depth_200_usd) AS depth_200_delta,
    CASE WHEN bt.max_spread_pct    IS NULL THEN NULL
         ELSE da.avg_spread_pct    <= bt.max_spread_pct    END AS spread_met,
    CASE WHEN bt.min_depth_100_usd IS NULL THEN NULL
         ELSE da.avg_depth_100_usd >= bt.min_depth_100_usd END AS depth_100_met,
    CASE WHEN bt.min_depth_200_usd IS NULL THEN NULL
         ELSE da.avg_depth_200_usd >= bt.min_depth_200_usd END AS depth_200_met,
    CASE WHEN bt.max_spread_pct    IS NULL THEN NULL
         WHEN da.avg_spread_pct    <= bt.max_spread_pct    THEN 'over' ELSE 'under' END
        AS spread_status,
    CASE WHEN bt.min_depth_100_usd IS NULL THEN NULL
         WHEN da.avg_depth_100_usd >= bt.min_depth_100_usd THEN 'over' ELSE 'under' END
        AS depth_100_status,
    CASE WHEN bt.min_depth_200_usd IS NULL THEN NULL
         WHEN da.avg_depth_200_usd >= bt.min_depth_200_usd THEN 'over' ELSE 'under' END
        AS depth_200_status
FROM daily_aggregates da
LEFT JOIN benchmark_targets bt USING (exchange, symbol);
