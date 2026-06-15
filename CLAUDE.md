# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Market-quality monitoring for the SSV token: collects public order-book data from 5 CEXes (Binance, Bybit, Gate, KuCoin, OKX) via CCXT Pro WebSockets, computes spread + depth (±100/±200 bps of mid) every 5s, aggregates to daily per-exchange averages in TimescaleDB, and shows them in Grafana. The product spec is `specs/PRD_SSV_market_quality_monitoring.md`; this implements the P0 scope (PRD requirement IDs like P0-2 are referenced in module docstrings).

## Commands

```bash
uv venv && uv pip install -e ".[dev]"   # setup (uses uv; .venv already exists)
uv run pytest                            # all tests (pytest config in pyproject.toml, asyncio_mode=auto)
uv run pytest tests/test_metrics.py -k crossed   # single file / single test
uv run ruff check src tests             # lint (line-length 100, rules E,F,I,UP,B)

docker compose up -d --build            # full stack: TimescaleDB + collector + aggregator + Grafana (:3000)
docker compose run --rm aggregator python -m ssv_mqm.aggregator --date 2026-06-09   # recompute one UTC day
```

Services need `DATABASE_URL` from `.env` (copy from `.env.example`). Tests do not — the tested modules are deliberately DB-free.

## Architecture

Two long-running services sharing one package (`src/ssv_mqm/`), wired by `docker-compose.yml`:

1. **Collector + sampler** (`main.py` entrypoint): `collector.py` runs one supervised asyncio task per (exchange, symbol) doing `watch_order_book`, keeping only the latest book in an in-memory `BookStore`. `sampler.py` ticks every `cadence_seconds`, computes metrics from each live book, and batch-inserts into the `samples` hypertable. A market with no live book is *skipped* for that tick — gaps surface as reduced coverage, never fabricated data.
2. **Aggregator** (`aggregator.py`): daily job (or `--schedule` daemon, or `--date` recompute) that lets SQL in `db.py` do the per-day `GROUP BY` averaging, then upserts into `daily_aggregates`.

**Purity boundary (the key design rule):** `metrics.py` (per-sample math) and `aggregate.py` (coverage/low-coverage rules) are pure — no DB, network, or clocks — so the published numbers are deterministic and unit-testable. Tests cover exactly these plus `config.py`. Keep new metric/aggregation logic on the pure side of this line; `db.py`, `collector.py`, and `sampler.py` are the I/O shells around it.

Other pieces: `config.py` (pydantic-validated `config/config.yaml`, path overridable via `SSV_MQM_CONFIG`), `models.py` (plain dataclasses), `db.py` (asyncpg pool + idempotent schema bootstrap from `migrations/001_init.sql`, path overridable via `SSV_MQM_MIGRATIONS`), `log.py` (structlog). Grafana dashboard + datasource are provisioned from `grafana/`.

## Conventions and gotchas

- **Spread units:** stored as a *fraction* in `samples.spread`; published/aggregated as a *percentage* (`avg_spread_pct`, 2 dp). Don't mix them up.
- Depth is always stored as separate bid/ask components per band so the published total (bid + ask) is reproducible.
- Crossed/locked books (`best_bid >= best_ask`) are excluded from spread averages and counted in `crossed_excluded`; a thin/empty depth band is 0.0, not an error.
- Everything is UTC: sample timestamps, the aggregate `day`, the aggregator schedule, and Grafana (forced via `GF_USERS_DEFAULT_TIMEZONE`).
- `_EXCHANGE_BOOK_LIMIT` in `collector.py` overrides the book limit per exchange where the default would select a non-public channel (OKX's limit=50 channel is auth-only). Check this map before changing `book_limit`.
- **Benchmarks:** per-`(exchange, symbol)` targets live in `config.yaml` `benchmarks:`, are mirrored into the `benchmark_targets` table on startup (and via `aggregator --seed-benchmarks`), and the `benchmark_comparison` view (`migrations/002_benchmarks.sql`) joins them to `daily_aggregates`. `max_spread_pct` is a ceiling in **percent** (lower = better, like `avg_spread_pct`); `min_depth_*_usd` are floors in **USD** (higher = better). The over/under rule is in pure `benchmark.py` and mirrored in the SQL view — keep them in sync (same Python/SQL duplication as `aggregate.py` ↔ `aggregate_day`). On the Grafana dashboard the chart target overlays are gated by the `show_benchmarks` template variable (a `false`/`true` toggle), but the two benchmark *tables* live in a **collapsed row** (collapsed = zero footprint) rather than being variable-gated — Grafana's scene engine does not reliably hide a panel via a template variable (panel `repeat` over a multi-value var did not render).
- **Migrations:** `db.bootstrap_schema()` applies every `migrations/*.sql` in name order, idempotently; add new schema as `NNN_*.sql` (and mount it for the TimescaleDB init in `docker-compose.yml`).
- **Read-only security posture (P0-8):** exchanges are constructed with no API keys; never add trading/withdrawal credentials anywhere in this system.
- Markets are configured in `config/config.yaml`; ones not listed on a venue are skipped + logged at startup (delisting-safe).
