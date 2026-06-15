-- SSV market-quality: per-sample FX audit column.
-- Idempotent: safe to run on every startup and as a docker init script.
-- Depth in `samples` is always stored in USD; for a fiat-quoted market (e.g. SSV/EUR)
-- the quote-currency notional is multiplied by a live FX rate before storage. `fx_rate`
-- records the multiplier used so USD depth is reproducible / auditable. It is 1.0 for
-- USDT/USDC markets and NULL only for rows written before this column existed.

ALTER TABLE samples ADD COLUMN IF NOT EXISTS fx_rate DOUBLE PRECISION;
