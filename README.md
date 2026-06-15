# SSV Token Market-Quality Monitoring

An in-house system that continuously collects public order-book data for the **SSV token**
from five centralized exchanges (Binance, Bybit, Gate.io, KuCoin, OKX) via **CCXT Pro**
WebSocket feeds, computes per-sample **spread** and **depth** (within ±100 bps and ±200 bps
of mid), aggregates to **daily averages per exchange**, and surfaces them in a **Grafana**
dashboard.

See [`specs/PRD_SSV_market_quality_monitoring.md`](specs/PRD_SSV_market_quality_monitoring.md)
for the full product spec. This implementation covers the **P0 (must-have)** scope.

## Architecture

| Component | Module | Role |
|-----------|--------|------|
| Collector + Sampler | `ssv_mqm.main` | One CCXT Pro `watch_order_book` task per `(exchange, symbol)`, plus a periodic sampler that computes & persists metrics every `cadence_seconds`. |
| Aggregator | `ssv_mqm.aggregator` | Daily job: per-venue average spread % and ±100/±200 bps depth (USD) + coverage, into `daily_aggregates`. |
| Store | TimescaleDB | `samples` hypertable + `daily_aggregates` table + `benchmark_targets` and the `benchmark_comparison` view. |
| Dashboard | Grafana | Provisioned time-series + cross-exchange comparison + target-vs-actual panels. |

The metric math lives in `ssv_mqm.metrics` and the aggregation rules in `ssv_mqm.aggregate`
— both pure and fully unit-tested, so the published numbers are deterministic and auditable.

## Metric definitions

For each sample taken from a live book:

```
mid        = (best_ask + best_bid) / 2
spread     = (best_ask - best_bid) / mid            # published as a percentage, 2 dp
depth(b)   bid side = Σ price·size for bids with price ≥ mid·(1 − b)
           ask side = Σ price·size for asks with price ≤ mid·(1 + b)   (b = 1% or 2%)
```

USDT/USDC are treated as ≈ USD for depth notional. Depth is stored as **bid and ask
components** so the total (`bid + ask`) is reproducible.

**Daily aggregate** (per exchange, per symbol, per UTC day): simple mean over fixed-cadence
samples. Crossed/locked samples (`best_bid ≥ best_ask`) are excluded from the spread average
and counted separately. Each row records `samples_captured / samples_expected` coverage;
days below the coverage threshold (default 90%) are flagged `low_coverage` and hidden from
trend/comparison panels by default.

## Quick start

```bash
cp .env.example .env          # adjust passwords for your environment
docker compose up -d --build  # TimescaleDB + collector + aggregator + Grafana
```

- Grafana: <http://localhost:3000> (default `admin`/`admin`) → folder **SSV** → dashboard
  **SSV Market Quality**.
- The collector begins maintaining live books immediately; samples land every 5 s.
- The aggregator service runs daily at 00:30 UTC (configurable). To compute/recompute a day
  on demand:

```bash
docker compose run --rm aggregator python -m ssv_mqm.aggregator --date 2026-06-09
```

## Benchmarks (target vs. actual)

Define per-`(exchange, symbol)` targets under `benchmarks:` in `config/config.yaml` to
compare each venue against what you expect of it:

```yaml
benchmarks:
  - { exchange: binance, symbol: "SSV/USDT", max_spread_pct: 0.15, min_depth_100_usd: 50000, min_depth_200_usd: 120000 }
```

Each metric target is optional. `max_spread_pct` is a **ceiling** in percent (lower is
better, matching `avg_spread_pct`); `min_depth_*_usd` are **floors** in USD (higher is
better). Targets are synced into the `benchmark_targets` table on service startup, so a
market **overperforms** when it beats its target and **underperforms** when it misses.

The `benchmark_comparison` view joins actuals against targets (per-metric delta + over/under
status). To keep the default view uncluttered, benchmarks are off by default and surfaced two
ways on the dashboard:

- **Overlay benchmark targets** toggle (`false`/`true`, like *Include low-coverage days*) —
  adds dashed per-venue target lines to the spread/depth trend charts when set to `true`.
- A collapsed **Benchmarks — target vs. actual & breaches** row at the bottom holding two
  tables (*Target vs. actual (latest day)* and *Recent benchmark breaches*). Collapsed rows
  render nothing until expanded, so the tables are fully hidden by default — click the row
  header to reveal them. (A collapsed row, rather than a variable, is used because Grafana's
  scene engine does not reliably hide a panel via a template variable.)

The aggregator logs each miss as an `aggregator.benchmark_breach` warning regardless of the
dashboard state. To re-sync targets
after editing config without waiting for a restart:

```bash
docker compose run --rm aggregator python -m ssv_mqm.aggregator --seed-benchmarks
```

## Configuration

`config/config.yaml` controls sampling cadence, the stale-book cutoff, depth bands, book
limit, coverage threshold, aggregator schedule, the list of `(exchange, symbol)` markets,
and per-market `benchmarks` (see above). Markets not listed on a
venue are skipped + logged at startup (delisting-safe). Environment variables (`DATABASE_URL`,
`LOG_LEVEL`, DB/Grafana credentials) come from `.env`.

## Security posture (read-only)

Public market-data feeds only. Exchanges are constructed **without any API keys**; no
trade- or withdrawal-enabled credentials exist anywhere in the system. KuCoin's public
WebSocket token is fetched automatically by CCXT Pro without authentication. No secrets in
source control — they live in `.env` (gitignored) / your secrets manager.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest          # unit tests for metric math, aggregation, config
uv run ruff check src tests
```

## Scope

**Included (P0):** ingestion, per-sample metrics, daily aggregation, persistent storage,
Grafana dashboard, coverage/quality handling, reconnect/resync reliability, read-only
security, and per-venue benchmark targets (target vs. actual + over/under).

**Out of scope (deferred P1/P2):** threshold alerting, raw L2 snapshot retention,
time-weighted averaging, derivatives/perps, DEX/on-chain, multi-token, historical backfill,
external/public access. The schema and architecture leave room for these without building
them now.
