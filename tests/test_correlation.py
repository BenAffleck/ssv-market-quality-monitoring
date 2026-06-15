"""Tests for the pure correlation/beta math."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ssv_mqm.correlation import (
    correlation_points,
    daily_log_returns,
    rolling_correlation,
)

D0 = date(2026, 1, 1)


def _days(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def _series(closes: list[float]) -> list[tuple[date, float]]:
    return list(zip(_days(len(closes)), closes, strict=True))


def test_log_returns_drop_first_and_compute():
    rets = daily_log_returns(_series([100.0, 110.0, 121.0]))
    assert [d for d, _ in rets] == _days(3)[1:]
    # ln(110/100) and ln(121/110) are both ln(1.1).
    assert rets[0][1] == pytest.approx(rets[1][1])


def test_log_returns_skip_nonpositive_close():
    rets = daily_log_returns(_series([100.0, 0.0, 50.0]))
    # The step into and out of the zero close is undefined and skipped; no rows emitted.
    assert rets == []


def test_log_returns_sort_input():
    unsorted = [(D0 + timedelta(days=1), 110.0), (D0, 100.0)]
    rets = daily_log_returns(unsorted)
    assert [d for d, _ in rets] == [D0 + timedelta(days=1)]


def test_perfectly_correlated_gives_corr_one_and_beta_ratio():
    # asset moves exactly 2x the benchmark each day => corr == 1, beta == 2.
    bench = _series([100.0, 101.0, 102.0, 103.5, 104.0, 105.0])
    bench_rets = daily_log_returns(bench)
    asset_rets = [(d, 2.0 * r) for d, r in bench_rets]
    pts = rolling_correlation(
        asset_rets, bench_rets, asset="X", benchmark="ETH", window=5, min_obs=3
    )
    last = pts[-1]
    assert last.correlation == pytest.approx(1.0)
    assert last.beta == pytest.approx(2.0)
    assert last.r2 == pytest.approx(1.0)
    assert last.n_obs == 5


def test_anticorrelated_gives_corr_minus_one_and_negative_beta():
    bench = _series([100.0, 101.0, 102.5, 101.5, 103.0, 104.0])
    bench_rets = daily_log_returns(bench)
    asset_rets = [(d, -1.5 * r) for d, r in bench_rets]
    pts = rolling_correlation(
        asset_rets, bench_rets, asset="X", benchmark="ETH", window=5, min_obs=3
    )
    last = pts[-1]
    assert last.correlation == pytest.approx(-1.0)
    assert last.beta == pytest.approx(-1.5)


def test_beta_is_slope_of_asset_on_benchmark_not_inverse():
    # Asset returns vary far more than the benchmark's. Beta (asset-on-benchmark) must be
    # large; the inverse regression (benchmark-on-asset) would be small. This pins the order.
    bench_rets = [(D0 + timedelta(days=i), x) for i, x in enumerate([0.01, 0.02, 0.03, 0.04])]
    asset_rets = [(D0 + timedelta(days=i), y) for i, y in enumerate([0.10, 0.20, 0.30, 0.40])]
    pts = rolling_correlation(
        asset_rets, bench_rets, asset="X", benchmark="ETH", window=4, min_obs=2
    )
    assert pts[-1].beta == pytest.approx(10.0)


def test_young_window_below_min_obs_is_null():
    bench = _series([100.0, 101.0, 102.0, 103.0])
    bench_rets = daily_log_returns(bench)
    asset_rets = [(d, 2.0 * r) for d, r in bench_rets]
    pts = rolling_correlation(
        asset_rets, bench_rets, asset="X", benchmark="ETH", window=5, min_obs=3
    )
    # 3 return days; first two windows have <3 obs -> NULL, third reaches min_obs.
    assert pts[0].correlation is None and pts[0].n_obs == 1
    assert pts[1].correlation is None and pts[1].n_obs == 2
    assert pts[2].correlation is not None and pts[2].n_obs == 3


def test_alignment_drops_unmatched_days():
    bench_rets = [(D0, 0.01), (D0 + timedelta(days=1), 0.02)]
    # Asset has an extra day the benchmark lacks; it must be dropped, not misaligned.
    asset_rets = [(D0, 0.02), (D0 + timedelta(days=2), 0.05)]
    pts = rolling_correlation(
        asset_rets, bench_rets, asset="X", benchmark="ETH", window=5, min_obs=1
    )
    assert [p.day for p in pts] == [D0]


def test_flat_benchmark_window_is_null():
    # No variance in the benchmark over the window -> correlation/beta undefined.
    bench_rets = [(D0 + timedelta(days=i), 0.0) for i in range(4)]
    asset_rets = [(D0 + timedelta(days=i), 0.01 * i) for i in range(4)]
    pts = rolling_correlation(
        asset_rets, bench_rets, asset="X", benchmark="ETH", window=4, min_obs=2
    )
    assert pts[-1].correlation is None
    assert pts[-1].beta is None


def test_correlation_points_spans_all_windows():
    bench = _series([100.0 + i for i in range(10)])
    asset = _series([200.0 + 2 * i for i in range(10)])
    pts = correlation_points(
        asset="X",
        benchmark="ETH",
        asset_prices=asset,
        benchmark_prices=bench,
        windows=[3, 5],
        min_obs_ratio=0.5,
    )
    assert {p.window for p in pts} == {3, 5}
    assert all(p.asset == "X" and p.benchmark == "ETH" for p in pts)
