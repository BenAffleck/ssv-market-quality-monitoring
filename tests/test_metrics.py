"""Unit tests for the pure metric core (PRD P0-2 + edge cases)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ssv_mqm.metrics import EmptyBookError, compute_sample

NOW = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)


def _sample(bids, asks, bands=(100, 200)):
    return compute_sample("binance", "SSV/USDT", NOW, bids, asks, bands)


def test_basic_spread_and_mid():
    # best_bid=10.00, best_ask=10.02 -> mid=10.01, spread=0.02/10.01
    s = _sample([[10.00, 5]], [[10.02, 5]])
    assert s.mid == pytest.approx(10.01)
    assert s.spread == pytest.approx(0.02 / 10.01)
    assert s.spread_pct == pytest.approx(0.02 / 10.01 * 100)
    assert s.is_crossed is False


def test_depth_components_and_band_filtering():
    mid = 10.0  # bids 9.99/9.90/9.70, asks 10.01/10.10/10.30
    bids = [[9.99, 100], [9.90, 100], [9.70, 100]]
    asks = [[10.01, 100], [10.10, 100], [10.30, 100]]
    s = compute_sample("binance", "SSV/USDT", NOW, bids, asks, (100, 200))
    assert s.mid == pytest.approx(mid)

    # +/-100 bps = +/-1%: bid >= 9.90, ask <= 10.10 -> two levels each side.
    d100_bid, d100_ask = s.depth[100]
    assert d100_bid == pytest.approx(9.99 * 100 + 9.90 * 100)
    assert d100_ask == pytest.approx(10.01 * 100 + 10.10 * 100)

    # +/-200 bps = +/-2%: bid >= 9.80 (9.70 excluded), ask <= 10.20 (10.30 excluded).
    d200_bid, d200_ask = s.depth[200]
    assert d200_bid == pytest.approx(9.99 * 100 + 9.90 * 100)
    assert d200_ask == pytest.approx(10.01 * 100 + 10.10 * 100)

    # Total is reproducible as bid + ask.
    assert s.depth_total(100) == pytest.approx(d100_bid + d100_ask)


def test_thin_band_returns_zero_not_error():
    # Wide market: best bid 9.00 / best ask 11.00 -> mid 10.0. Both best levels sit
    # outside the +/-1% and +/-2% bands, so all band depths are legitimately zero,
    # not an error (PRD edge case: thin book within a band).
    s = _sample([[9.00, 100]], [[11.00, 100]])
    assert s.depth[100] == (0.0, 0.0)
    assert s.depth[200] == (0.0, 0.0)
    assert s.depth_total(100) == pytest.approx(0.0)


def test_zero_size_levels_contribute_zero_depth():
    s = _sample([[10.00, 0]], [[10.02, 0]])
    assert s.depth_total(100) == pytest.approx(0.0)
    assert s.depth_total(200) == pytest.approx(0.0)


def test_crossed_book_flagged_not_negative_consumer():
    # best_bid >= best_ask -> flagged so the aggregator can exclude it.
    s = _sample([[10.05, 5]], [[10.00, 5]])
    assert s.is_crossed is True
    assert s.spread < 0  # raw value is negative; exclusion happens downstream


def test_locked_book_flagged():
    s = _sample([[10.00, 5]], [[10.00, 5]])  # bid == ask
    assert s.is_crossed is True
    assert s.spread == pytest.approx(0.0)


def test_empty_side_raises():
    with pytest.raises(EmptyBookError):
        _sample([], [[10.0, 1]])
    with pytest.raises(EmptyBookError):
        _sample([[10.0, 1]], [])
