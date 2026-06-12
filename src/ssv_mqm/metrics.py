"""Pure metric computation — the auditable core of the system (PRD P0-2).

No DB, no network, no clocks beyond the timestamp passed in. Every behaviour here is
covered by unit tests so the published numbers are deterministic and reproducible.

Definitions (per PRD):
    mid    = (best_ask + best_bid) / 2
    spread = (best_ask - best_bid) / mid                      # stored as a fraction
    depth(band) on the bid side  = Sum(price * size) for bids with price >= mid*(1 - band)
    depth(band) on the ask side  = Sum(price * size) for asks with price <= mid*(1 + band)

USDT/USDC are treated as ~= USD, so price*size is the USD notional directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .models import SampleMetrics

# An order-book side is a sequence of [price, size] pairs.
Level = Sequence[float]
Side = Sequence[Level]


class EmptyBookError(ValueError):
    """Raised when a book has no levels on one or both sides — cannot form a sample."""


def _band_depth(levels: Side, threshold: float, *, is_bid: bool) -> float:
    """Sum price*size for levels within ``threshold`` of mid.

    For bids we keep levels with ``price >= threshold``; for asks ``price <= threshold``.
    A legitimately thin/empty band returns 0.0 — never an error (PRD edge case).
    """
    total = 0.0
    for level in levels:
        price = float(level[0])
        size = float(level[1])
        if is_bid:
            if price >= threshold:
                total += price * size
        else:
            if price <= threshold:
                total += price * size
    return total


def compute_sample(
    exchange: str,
    symbol: str,
    time: datetime,
    bids: Side,
    asks: Side,
    bands_bps: Sequence[int] = (100, 200),
) -> SampleMetrics:
    """Compute one :class:`SampleMetrics` from an order-book snapshot.

    ``bids`` must be sorted best (highest) first; ``asks`` best (lowest) first — this is
    what CCXT returns. A crossed/locked book (best_bid >= best_ask) is flagged via
    ``is_crossed`` so it can be excluded from spread averaging downstream (PRD P0-2),
    rather than producing a negative spread.
    """
    if not bids or not asks:
        raise EmptyBookError(
            f"{exchange} {symbol}: empty book side (bids={len(bids)}, asks={len(asks)})"
        )

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2.0
    is_crossed = best_bid >= best_ask
    spread = (best_ask - best_bid) / mid  # may be <= 0 when crossed; excluded later

    depth: dict[int, tuple[float, float]] = {}
    for band in bands_bps:
        frac = band / 10_000.0
        bid_depth = _band_depth(bids, mid * (1.0 - frac), is_bid=True)
        ask_depth = _band_depth(asks, mid * (1.0 + frac), is_bid=False)
        depth[int(band)] = (bid_depth, ask_depth)

    return SampleMetrics(
        exchange=exchange,
        symbol=symbol,
        time=time,
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread=spread,
        depth=depth,
        is_crossed=is_crossed,
    )
