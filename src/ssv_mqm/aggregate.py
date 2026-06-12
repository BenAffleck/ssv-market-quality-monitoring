"""Pure daily-aggregation logic (PRD P0-3, P0-6).

The SQL ``GROUP BY`` in :mod:`ssv_mqm.db` produces one row per (exchange, symbol) with the
heavy averaging already done. This module turns such a row into a :class:`DailyAggregate`,
applying the coverage and low-coverage rules. Kept DB-free so it is unit-testable.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from .models import DailyAggregate


def coverage_pct(samples_captured: int, samples_expected: int) -> float:
    if samples_expected <= 0:
        return 0.0
    return round(100.0 * samples_captured / samples_expected, 2)


def build_daily_aggregate(
    day: date,
    row: Mapping[str, Any],
    samples_expected: int,
    coverage_threshold_pct: float,
) -> DailyAggregate:
    """Build a :class:`DailyAggregate` from one aggregated SQL row.

    Expected keys: exchange, symbol, avg_spread_pct (may be None if every sample was
    crossed), avg_depth_{100,200}_{bid,ask}, samples_captured, crossed_excluded.
    """
    captured = int(row["samples_captured"])
    cov = coverage_pct(captured, samples_expected)
    d100_bid = float(row["avg_depth_100_bid"] or 0.0)
    d100_ask = float(row["avg_depth_100_ask"] or 0.0)
    d200_bid = float(row["avg_depth_200_bid"] or 0.0)
    d200_ask = float(row["avg_depth_200_ask"] or 0.0)
    spread = row["avg_spread_pct"]
    return DailyAggregate(
        day=day.isoformat(),
        exchange=row["exchange"],
        symbol=row["symbol"],
        avg_spread_pct=round(float(spread), 2) if spread is not None else 0.0,
        avg_depth_100_usd=d100_bid + d100_ask,
        avg_depth_200_usd=d200_bid + d200_ask,
        avg_depth_100_bid=d100_bid,
        avg_depth_100_ask=d100_ask,
        avg_depth_200_bid=d200_bid,
        avg_depth_200_ask=d200_ask,
        samples_captured=captured,
        samples_expected=samples_expected,
        coverage_pct=cov,
        crossed_samples_excluded=int(row["crossed_excluded"]),
        low_coverage=cov < coverage_threshold_pct,
    )
