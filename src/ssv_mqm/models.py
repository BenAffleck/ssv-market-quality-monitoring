"""Dataclasses for the metric pipeline.

These are intentionally plain (no DB or network concerns) so the metric math in
``metrics.py`` stays pure and trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SampleMetrics:
    """One point-in-time measurement computed from a single order-book snapshot.

    Depth is stored as separate bid/ask components per band so that the published
    total depth (``bid + ask``) is fully reproducible (PRD P0-2).
    """

    exchange: str
    symbol: str
    time: datetime  # UTC timestamp of the sample
    best_bid: float
    best_ask: float
    mid: float
    spread: float  # fraction: (ask - bid) / mid
    # Depth in USD (quote-currency notional x fx_rate), keyed by band in bps:
    # {100: (bid, ask), 200: (...)}.
    depth: dict[int, tuple[float, float]] = field(default_factory=dict)
    is_crossed: bool = False
    # Quote-currency -> USD multiplier applied to depth (1.0 for USDT/USDC; live FX rate
    # for a fiat quote like EUR). Recorded for auditability.
    fx_rate: float = 1.0

    @property
    def spread_pct(self) -> float:
        """Spread expressed as a percentage."""
        return self.spread * 100.0

    def depth_total(self, band_bps: int) -> float:
        bid, ask = self.depth[band_bps]
        return bid + ask


@dataclass(frozen=True)
class DailyAggregate:
    """Daily average metrics for one (exchange, symbol) over one UTC day (PRD P0-3)."""

    day: str  # ISO date, e.g. "2026-06-09"
    exchange: str
    symbol: str
    avg_spread_pct: float  # rounded to 2 decimals
    avg_depth_100_usd: float
    avg_depth_200_usd: float
    avg_depth_100_bid: float
    avg_depth_100_ask: float
    avg_depth_200_bid: float
    avg_depth_200_ask: float
    samples_captured: int
    samples_expected: int
    coverage_pct: float
    crossed_samples_excluded: int
    low_coverage: bool
