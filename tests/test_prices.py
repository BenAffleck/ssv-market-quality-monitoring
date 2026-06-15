"""Tests for the pure OHLCV -> daily-close mapping."""

from __future__ import annotations

from datetime import date, datetime, timezone

from ssv_mqm.prices import ohlcv_to_closes


def _ts(d: date) -> int:
    """Epoch milliseconds for the UTC midnight bucket of ``d`` (CCXT 1d candle start)."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def test_maps_close_and_day():
    rows = [[_ts(date(2026, 6, 10)), 1.0, 2.0, 0.5, 1.5, 1000.0]]
    closes = ohlcv_to_closes("SSV", "binance:SSV/USDT", rows, date(2026, 6, 15))
    assert len(closes) == 1
    c = closes[0]
    assert c.day == date(2026, 6, 10)
    assert c.asset == "SSV"
    assert c.close_usd == 1.5  # close is OHLCV index 4
    assert c.source == "binance:SSV/USDT"


def test_drops_partial_current_and_future_day():
    today = date(2026, 6, 15)
    rows = [
        [_ts(date(2026, 6, 14)), 1, 2, 0, 1.4, 1],  # closed -> kept
        [_ts(today), 1, 2, 0, 1.5, 1],  # in-progress current day -> dropped
    ]
    closes = ohlcv_to_closes("ETH", "binance:ETH/USDT", rows, today)
    assert [c.day for c in closes] == [date(2026, 6, 14)]


def test_skips_missing_close():
    rows = [[_ts(date(2026, 6, 10)), 1, 2, 0, None, 1]]
    assert ohlcv_to_closes("RPL", "binance:RPL/USDT", rows, date(2026, 6, 15)) == []
