"""Unit tests for the pure daily-aggregation logic (PRD P0-3, P0-6)."""

from __future__ import annotations

from datetime import date

from ssv_mqm.aggregate import build_daily_aggregate, coverage_pct

DAY = date(2026, 6, 9)
EXPECTED_5S = 86_400 // 5  # 17280


def _row(captured, crossed=0, spread=0.05, **depth):
    base = dict(
        exchange="binance",
        symbol="SSV/USDT",
        avg_spread_pct=spread,
        avg_depth_100_bid=1000.0,
        avg_depth_100_ask=900.0,
        avg_depth_200_bid=2000.0,
        avg_depth_200_ask=1800.0,
        samples_captured=captured,
        crossed_excluded=crossed,
    )
    base.update(depth)
    return base


def test_coverage_pct():
    assert coverage_pct(EXPECTED_5S, EXPECTED_5S) == 100.0
    assert coverage_pct(0, EXPECTED_5S) == 0.0
    assert coverage_pct(8640, EXPECTED_5S) == 50.0
    assert coverage_pct(5, 0) == 0.0  # guard against divide-by-zero


def test_full_coverage_not_flagged():
    agg = build_daily_aggregate(DAY, _row(EXPECTED_5S), EXPECTED_5S, 90.0)
    assert agg.coverage_pct == 100.0
    assert agg.low_coverage is False
    assert agg.day == "2026-06-09"


def test_low_coverage_flagged():
    # 50% coverage is below the 90% threshold.
    agg = build_daily_aggregate(DAY, _row(EXPECTED_5S // 2), EXPECTED_5S, 90.0)
    assert agg.coverage_pct == 50.0
    assert agg.low_coverage is True


def test_depth_totals_are_bid_plus_ask():
    agg = build_daily_aggregate(DAY, _row(EXPECTED_5S), EXPECTED_5S, 90.0)
    assert agg.avg_depth_100_usd == 1000.0 + 900.0
    assert agg.avg_depth_200_usd == 2000.0 + 1800.0


def test_crossed_excluded_count_passthrough():
    agg = build_daily_aggregate(DAY, _row(EXPECTED_5S, crossed=12), EXPECTED_5S, 90.0)
    assert agg.crossed_samples_excluded == 12


def test_all_crossed_day_yields_zero_spread_not_crash():
    # If every sample was crossed, SQL AVG(spread) FILTER(...) returns NULL.
    agg = build_daily_aggregate(DAY, _row(EXPECTED_5S, spread=None), EXPECTED_5S, 90.0)
    assert agg.avg_spread_pct == 0.0


def test_spread_rounded_two_decimals():
    agg = build_daily_aggregate(DAY, _row(EXPECTED_5S, spread=0.123456), EXPECTED_5S, 90.0)
    assert agg.avg_spread_pct == 0.12
