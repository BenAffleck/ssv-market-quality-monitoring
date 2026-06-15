"""Rolling correlation & beta of a token vs a benchmark (pure — no DB, network, or clocks).

SSV is a beta play on ETH, so we track how tightly SSV's returns move with ETH (correlation)
and how amplified they are (beta), over rolling windows. Comparison projects in the same
staking narrative (e.g. RocketPool / RPL) run through the exact same path against the same
benchmark, so their betas are directly comparable.

This is the canonical rule; the ``asset_returns`` SQL view (migrations/004_asset_prices.sql)
mirrors the return computation for Grafana, the same Python<->SQL split as
:mod:`ssv_mqm.aggregate` (``aggregate_day``) and :mod:`ssv_mqm.benchmark`
(``benchmark_comparison``).

Beta convention (the easy-to-get-wrong bit): ``beta = cov(asset, benchmark) / var(benchmark)``
= the slope of the asset's returns regressed on the benchmark's returns. The **benchmark**
(ETH) is the independent variable. ``r2 = correlation ** 2`` (univariate OLS). Returns are
log returns (additive, standard). The population vs sample distinction cancels in every ratio
here, so plain sums of mean-deviations suffice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CorrelationPoint:
    """Rolling correlation/beta of ``asset`` vs ``benchmark`` ending on ``day``.

    ``correlation``/``beta``/``r2`` are ``None`` when the window holds fewer than ``min_obs``
    aligned return pairs, or when the benchmark has no variance over the window (undefined).
    ``n_obs`` is always the number of aligned pairs actually in the window.
    """

    day: date
    asset: str
    benchmark: str
    window: int
    correlation: float | None
    beta: float | None
    r2: float | None
    n_obs: int


def daily_log_returns(prices: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """Daily log returns from ``(day, close)`` pairs, sorted by day ascending.

    The first day has no prior close and is dropped. A non-positive close on either side of
    a step is skipped (log return undefined) rather than fabricated.
    """
    ordered = sorted(prices)
    out: list[tuple[date, float]] = []
    prev: float | None = None
    for day, close in ordered:
        if prev is not None and prev > 0.0 and close > 0.0:
            out.append((day, math.log(close / prev)))
        prev = close
    return out


def _window_stats(
    pairs: Sequence[tuple[float, float]],
) -> tuple[float | None, float | None, float | None]:
    """Pearson correlation, beta (slope of y on x), and r2 for ``(y, x)`` pairs.

    ``y`` is the asset return, ``x`` the benchmark return. Returns ``(None, None, None)`` when
    the benchmark (x) has no variance, so the slope/correlation are undefined.
    """
    n = len(pairs)
    if n < 2:
        return None, None, None
    mean_y = sum(y for y, _ in pairs) / n
    mean_x = sum(x for _, x in pairs) / n
    syy = sum((y - mean_y) ** 2 for y, _ in pairs)
    sxx = sum((x - mean_x) ** 2 for _, x in pairs)
    sxy = sum((y - mean_y) * (x - mean_x) for y, x in pairs)
    if sxx <= 0.0:
        return None, None, None
    beta = sxy / sxx
    if syy <= 0.0:
        # Asset is flat: correlation undefined, but the slope (beta) is well-defined (0).
        return None, beta, None
    correlation = sxy / math.sqrt(sxx * syy)
    return correlation, beta, correlation**2


def rolling_correlation(
    asset_returns: Sequence[tuple[date, float]],
    benchmark_returns: Sequence[tuple[date, float]],
    *,
    asset: str,
    benchmark: str,
    window: int,
    min_obs: int,
) -> list[CorrelationPoint]:
    """Rolling-window correlation/beta of ``asset`` returns vs ``benchmark`` returns.

    Returns are aligned by ``day`` (a day present for only one side is dropped — never
    misaligned). For each aligned day, stats are computed over the trailing ``window``
    observations (matching the SQL ``ROWS BETWEEN window-1 PRECEDING AND CURRENT ROW``).
    A point is emitted per aligned day; ``correlation``/``beta``/``r2`` are ``None`` until at
    least ``min_obs`` pairs are in the window.
    """
    bench = dict(benchmark_returns)
    aligned: list[tuple[date, float, float]] = sorted(
        (day, a_ret, bench[day]) for day, a_ret in asset_returns if day in bench
    )
    points: list[CorrelationPoint] = []
    for i in range(len(aligned)):
        start = max(0, i - window + 1)
        win = aligned[start : i + 1]
        n_obs = len(win)
        if n_obs >= min_obs:
            correlation, beta, r2 = _window_stats([(y, x) for _, y, x in win])
        else:
            correlation = beta = r2 = None
        points.append(
            CorrelationPoint(
                day=aligned[i][0],
                asset=asset,
                benchmark=benchmark,
                window=window,
                correlation=correlation,
                beta=beta,
                r2=r2,
                n_obs=n_obs,
            )
        )
    return points


def correlation_points(
    *,
    asset: str,
    benchmark: str,
    asset_prices: Sequence[tuple[date, float]],
    benchmark_prices: Sequence[tuple[date, float]],
    windows: Sequence[int],
    min_obs_ratio: float,
) -> list[CorrelationPoint]:
    """All rolling correlation/beta points for ``asset`` vs ``benchmark`` across ``windows``.

    Converts both price series to log returns once, then evaluates each window. ``min_obs``
    per window is ``max(2, ceil(window * min_obs_ratio))`` — enough observations before a
    correlation/beta is published, so young windows don't surface a noisy value.
    """
    asset_returns = daily_log_returns(asset_prices)
    benchmark_returns = daily_log_returns(benchmark_prices)
    points: list[CorrelationPoint] = []
    for window in windows:
        min_obs = max(2, math.ceil(window * min_obs_ratio))
        points.extend(
            rolling_correlation(
                asset_returns,
                benchmark_returns,
                asset=asset,
                benchmark=benchmark,
                window=window,
                min_obs=min_obs,
            )
        )
    return points
