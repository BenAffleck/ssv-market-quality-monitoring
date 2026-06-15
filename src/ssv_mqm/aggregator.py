"""Daily aggregation job (PRD P0-3, P0-6).

Computes per-(exchange, symbol) daily averages for a UTC day and upserts them into
``daily_aggregates``. Runnable three ways:

    python -m ssv_mqm.aggregator                 # aggregate yesterday (UTC)
    python -m ssv_mqm.aggregator --date 2026-06-09   # recompute a specific day
    python -m ssv_mqm.aggregator --schedule      # daemon: run daily after midnight UTC

The ``--date`` form supports recomputation if the methodology changes, given retained
samples (PRD analyst story).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone

from .config import AppConfig, load_config
from .db import Database
from .log import configure_logging, get_logger

log = get_logger(__name__)


def _yesterday_utc() -> date:
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


async def aggregate_for_day(db: Database, config: AppConfig, day: date) -> int:
    """Compute and persist aggregates for one UTC day. Returns rows written."""
    aggregates = await db.aggregate_day(
        day,
        samples_expected=config.samples_per_day,
        coverage_threshold_pct=config.coverage.threshold_pct,
    )
    await db.upsert_daily_aggregates(aggregates)
    low = [a for a in aggregates if a.low_coverage]
    log.info(
        "aggregator.day_complete",
        day=day.isoformat(),
        markets=len(aggregates),
        low_coverage_markets=len(low),
    )
    for a in low:
        log.warning(
            "aggregator.low_coverage",
            day=a.day,
            exchange=a.exchange,
            symbol=a.symbol,
            coverage_pct=a.coverage_pct,
        )
    await _log_benchmark_breaches(db, day)
    return len(aggregates)


# Each benchmarked metric and its columns in the benchmark_comparison view. The over/under
# rule itself lives in ssv_mqm.benchmark and the view; here we only surface the misses.
_BREACH_METRICS = (
    ("spread", "avg_spread_pct", "max_spread_pct", "spread_met"),
    ("depth_100_usd", "avg_depth_100_usd", "min_depth_100_usd", "depth_100_met"),
    ("depth_200_usd", "avg_depth_200_usd", "min_depth_200_usd", "depth_200_met"),
)


async def _log_benchmark_breaches(db: Database, day: date) -> None:
    """Warn for each market/metric that missed its configured target on ``day``."""
    breaches = await db.fetch_benchmark_breaches(day)
    for row in breaches:
        for metric, actual_col, target_col, met_col in _BREACH_METRICS:
            if row[met_col] is False:
                log.warning(
                    "aggregator.benchmark_breach",
                    day=day.isoformat(),
                    exchange=row["exchange"],
                    symbol=row["symbol"],
                    metric=metric,
                    actual=float(row[actual_col]),
                    target=float(row[target_col]),
                )


async def _seconds_until_next_run(config: AppConfig) -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(
        hour=config.aggregator.run_hour_utc,
        minute=config.aggregator.run_minute_utc,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_scheduled(db: Database, config: AppConfig) -> None:
    log.info(
        "aggregator.scheduled",
        run_hour_utc=config.aggregator.run_hour_utc,
        run_minute_utc=config.aggregator.run_minute_utc,
    )
    # Catch-up on startup: if the container was down/restarting across the scheduled
    # time, yesterday would otherwise stay missing until a manual --date run. The
    # upsert is idempotent, so recomputing an already-aggregated day is harmless.
    try:
        await aggregate_for_day(db, config, _yesterday_utc())
    except Exception as exc:  # noqa: BLE001 - keep the scheduler alive
        log.error("aggregator.run_failed", error=str(exc))
    while True:
        await asyncio.sleep(await _seconds_until_next_run(config))
        try:
            await aggregate_for_day(db, config, _yesterday_utc())
        except Exception as exc:  # noqa: BLE001 - keep the scheduler alive
            log.error("aggregator.run_failed", error=str(exc))


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SSV daily market-quality aggregator")
    parser.add_argument("--date", help="UTC day to (re)compute, YYYY-MM-DD. Default: yesterday.")
    parser.add_argument("--schedule", action="store_true", help="Run as a daily scheduled daemon.")
    parser.add_argument(
        "--seed-benchmarks",
        action="store_true",
        help="Sync benchmark targets from config into the DB, then exit.",
    )
    args = parser.parse_args(argv)

    configure_logging()
    config = load_config()
    db = Database(config.database_url)
    await db.connect()
    await db.bootstrap_schema()
    await db.seed_benchmark_targets(config.benchmarks)
    try:
        if args.seed_benchmarks:
            return
        if args.schedule:
            await run_scheduled(db, config)
        else:
            day = date.fromisoformat(args.date) if args.date else _yesterday_utc()
            await aggregate_for_day(db, config, day)
    finally:
        await db.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
