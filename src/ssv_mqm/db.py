"""Database access layer (asyncpg over TimescaleDB/Postgres).

Holds the connection pool, the schema bootstrap (idempotent — also applied by the
docker init script), batch inserts of per-sample metrics, and the daily-aggregate upsert.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg

from .aggregate import build_daily_aggregate
from .config import BenchmarkTarget
from .log import get_logger
from .models import DailyAggregate, SampleMetrics

log = get_logger(__name__)


def _resolve_migrations_dir() -> Path:
    """Locate the migrations directory across the dev (editable) and container layouts.

    In an editable install ``db.py`` lives under the repo tree, so migrations sit at
    ``<repo>/migrations``. When the package is pip-installed into site-packages that
    relative path no longer points at the repo, so we also honour an explicit env override
    (``SSV_MQM_MIGRATIONS`` — a directory, or a file inside it for back-compat) and the
    container's working directory (Dockerfile copies migrations to /app/migrations).
    """
    candidates = []
    env_override = os.environ.get("SSV_MQM_MIGRATIONS")
    if env_override:
        p = Path(env_override)
        candidates.append(p if p.is_dir() else p.parent)
    candidates.append(Path(__file__).resolve().parents[2] / "migrations")
    candidates.append(Path.cwd() / "migrations")
    for path in candidates:
        if path.is_dir():
            return path
    # Fall back to the first candidate so the error message is actionable.
    return candidates[0]


MIGRATIONS_DIR = _resolve_migrations_dir()


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._pool

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        log.info("db.connected")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def bootstrap_schema(self) -> None:
        """Apply all migrations in name order, idempotently (CREATE ... IF NOT EXISTS)."""
        migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
        async with self.pool.acquire() as conn:
            for path in migrations:
                await conn.execute(path.read_text(encoding="utf-8"))
        log.info("db.schema_ready", migrations=len(migrations))

    async def insert_samples(self, samples: list[SampleMetrics]) -> None:
        """Batch-insert per-sample metrics. Duplicate (exchange, symbol, time) is ignored."""
        if not samples:
            return
        rows = [
            (
                s.time,
                s.exchange,
                s.symbol,
                s.best_bid,
                s.best_ask,
                s.mid,
                s.spread,
                s.depth[100][0],
                s.depth[100][1],
                s.depth[200][0],
                s.depth[200][1],
                s.is_crossed,
            )
            for s in samples
        ]
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO samples (
                    time, exchange, symbol, best_bid, best_ask, mid, spread,
                    depth_100_bid, depth_100_ask, depth_200_bid, depth_200_ask, is_crossed
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (exchange, symbol, time) DO NOTHING
                """,
                rows,
            )

    async def aggregate_day(
        self, day: date, samples_expected: int, coverage_threshold_pct: float
    ) -> list[DailyAggregate]:
        """Compute daily aggregates for ``day`` (UTC) directly in SQL.

        Spread is averaged over non-crossed samples only; crossed samples are counted
        separately. Depth is averaged over all samples. Coverage = captured/expected.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    exchange,
                    symbol,
                    ROUND((AVG(spread) FILTER (WHERE NOT is_crossed) * 100)::numeric, 2)
                        AS avg_spread_pct,
                    AVG(depth_100_bid)                                  AS avg_depth_100_bid,
                    AVG(depth_100_ask)                                  AS avg_depth_100_ask,
                    AVG(depth_200_bid)                                  AS avg_depth_200_bid,
                    AVG(depth_200_ask)                                  AS avg_depth_200_ask,
                    COUNT(*)                                            AS samples_captured,
                    COUNT(*) FILTER (WHERE is_crossed)                  AS crossed_excluded
                FROM samples
                WHERE time >= $1::date AND time < ($1::date + INTERVAL '1 day')
                GROUP BY exchange, symbol
                """,
                day,
            )

        return [
            build_daily_aggregate(day, r, samples_expected, coverage_threshold_pct)
            for r in rows
        ]

    async def upsert_daily_aggregates(self, aggregates: list[DailyAggregate]) -> None:
        if not aggregates:
            return
        now = datetime.now(timezone.utc)
        rows = [
            (
                date.fromisoformat(a.day),
                a.exchange,
                a.symbol,
                Decimal(str(a.avg_spread_pct)),  # NUMERIC column wants Decimal
                a.avg_depth_100_usd,
                a.avg_depth_200_usd,
                a.avg_depth_100_bid,
                a.avg_depth_100_ask,
                a.avg_depth_200_bid,
                a.avg_depth_200_ask,
                a.samples_captured,
                a.samples_expected,
                Decimal(str(a.coverage_pct)),  # NUMERIC column wants Decimal
                a.crossed_samples_excluded,
                a.low_coverage,
                now,
            )
            for a in aggregates
        ]
        async with self.pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO daily_aggregates (
                    day, exchange, symbol, avg_spread_pct, avg_depth_100_usd, avg_depth_200_usd,
                    avg_depth_100_bid, avg_depth_100_ask, avg_depth_200_bid, avg_depth_200_ask,
                    samples_captured, samples_expected, coverage_pct, crossed_samples_excluded,
                    low_coverage, computed_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                ON CONFLICT (day, exchange, symbol) DO UPDATE SET
                    avg_spread_pct = EXCLUDED.avg_spread_pct,
                    avg_depth_100_usd = EXCLUDED.avg_depth_100_usd,
                    avg_depth_200_usd = EXCLUDED.avg_depth_200_usd,
                    avg_depth_100_bid = EXCLUDED.avg_depth_100_bid,
                    avg_depth_100_ask = EXCLUDED.avg_depth_100_ask,
                    avg_depth_200_bid = EXCLUDED.avg_depth_200_bid,
                    avg_depth_200_ask = EXCLUDED.avg_depth_200_ask,
                    samples_captured = EXCLUDED.samples_captured,
                    samples_expected = EXCLUDED.samples_expected,
                    coverage_pct = EXCLUDED.coverage_pct,
                    crossed_samples_excluded = EXCLUDED.crossed_samples_excluded,
                    low_coverage = EXCLUDED.low_coverage,
                    computed_at = EXCLUDED.computed_at
                """,
                rows,
            )
        log.info("db.aggregates_upserted", count=len(aggregates))

    async def seed_benchmark_targets(self, targets: list[BenchmarkTarget]) -> None:
        """Mirror the configured benchmark targets into ``benchmark_targets``.

        Upserts each configured row and removes any row no longer in config, so the table
        always reflects ``config.yaml`` (delisting-safe, like the configured markets).
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if targets:
                    rows = [
                        (
                            t.exchange,
                            t.symbol,
                            Decimal(str(t.max_spread_pct))  # NUMERIC column wants Decimal
                            if t.max_spread_pct is not None
                            else None,
                            t.min_depth_100_usd,
                            t.min_depth_200_usd,
                        )
                        for t in targets
                    ]
                    await conn.executemany(
                        """
                        INSERT INTO benchmark_targets (
                            exchange, symbol, max_spread_pct,
                            min_depth_100_usd, min_depth_200_usd, updated_at
                        ) VALUES ($1,$2,$3,$4,$5, now())
                        ON CONFLICT (exchange, symbol) DO UPDATE SET
                            max_spread_pct = EXCLUDED.max_spread_pct,
                            min_depth_100_usd = EXCLUDED.min_depth_100_usd,
                            min_depth_200_usd = EXCLUDED.min_depth_200_usd,
                            updated_at = now()
                        """,
                        rows,
                    )
                if targets:
                    await conn.execute(
                        """
                        DELETE FROM benchmark_targets bt
                        WHERE NOT EXISTS (
                            SELECT 1 FROM unnest($1::text[], $2::text[]) AS k(exchange, symbol)
                            WHERE k.exchange = bt.exchange AND k.symbol = bt.symbol
                        )
                        """,
                        [t.exchange for t in targets],
                        [t.symbol for t in targets],
                    )
                else:
                    await conn.execute("DELETE FROM benchmark_targets")
        log.info("db.benchmarks_seeded", count=len(targets))

    async def fetch_benchmark_breaches(self, day: date) -> list[asyncpg.Record]:
        """Rows for ``day`` where any benchmarked metric missed its target."""
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT * FROM benchmark_comparison
                WHERE day = $1
                  AND (spread_met = FALSE OR depth_100_met = FALSE OR depth_200_met = FALSE)
                """,
                day,
            )
