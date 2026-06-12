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
from .log import get_logger
from .models import DailyAggregate, SampleMetrics

log = get_logger(__name__)


def _resolve_migration_path() -> Path:
    """Locate 001_init.sql across the dev (editable) and installed (container) layouts.

    In an editable install ``db.py`` lives under the repo tree, so the migration sits at
    ``<repo>/migrations``. When the package is pip-installed into site-packages that
    relative path no longer points at the repo, so we also honour an explicit env override
    and the container's working directory (Dockerfile copies migrations to /app/migrations).
    """
    candidates = []
    env_override = os.environ.get("SSV_MQM_MIGRATIONS")
    if env_override:
        candidates.append(Path(env_override))
    candidates.append(Path(__file__).resolve().parents[2] / "migrations" / "001_init.sql")
    candidates.append(Path.cwd() / "migrations" / "001_init.sql")
    for path in candidates:
        if path.is_file():
            return path
    # Fall back to the first candidate so the error message is actionable.
    return candidates[0]


MIGRATION_PATH = _resolve_migration_path()


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
        """Apply the schema migration idempotently (CREATE ... IF NOT EXISTS)."""
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        async with self.pool.acquire() as conn:
            await conn.execute(sql)
        log.info("db.schema_ready")

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
