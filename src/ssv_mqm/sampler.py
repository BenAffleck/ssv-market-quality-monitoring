"""Periodic sampling task (PRD P0-2).

Every ``cadence_seconds`` it snapshots each live book from the :class:`BookStore`,
computes per-sample metrics, and batch-inserts them. Markets without a current book
(disconnected/resyncing) are simply skipped for that tick — the missing samples show up
as reduced coverage in the daily aggregate (PRD P0-6), never as fabricated data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .collector import BookSnapshot, BookStore
from .config import USD_QUOTES, AppConfig, Market
from .db import Database
from .log import get_logger
from .metrics import EmptyBookError, compute_sample, resolve_rate
from .models import SampleMetrics

log = get_logger(__name__)


class Sampler:
    def __init__(self, config: AppConfig, store: BookStore, db: Database) -> None:
        self._config = config
        self._store = store
        self._db = db
        self._bands = tuple(config.depth.bands_bps)
        self._max_book_age = config.sampling.max_book_age_seconds

    def _fresh(self, book: BookSnapshot | None, now: datetime) -> bool:
        """A book is usable this tick if present and updated within max_book_age."""
        if book is None:
            return False
        return (now - book.received_at).total_seconds() <= self._max_book_age

    def _quote_to_usd(self, market: Market, now: datetime) -> float | None:
        """Resolve the depth multiplier for ``market`` (None => skip this tick).

        USD-equivalent quotes map to 1.0. A fiat quote uses its configured live FX cross;
        a missing/stale cross returns None so the sample is skipped (coverage gap), exactly
        like a stale primary book.
        """
        if market.quote in USD_QUOTES:
            return 1.0
        src = self._config.fx[market.quote]  # presence guaranteed by config validation
        fx_book = self._store.get(src.exchange, src.symbol)
        if not self._fresh(fx_book, now) or not fx_book.bids or not fx_book.asks:
            log.warning(
                "sampler.fx_unavailable",
                exchange=market.exchange,
                symbol=market.symbol,
                fx_exchange=src.exchange,
                fx_symbol=src.symbol,
            )
            return None
        mid = (float(fx_book.bids[0][0]) + float(fx_book.asks[0][0])) / 2.0
        return resolve_rate(mid, invert=src.invert)

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        samples: list[SampleMetrics] = []
        crossed = 0
        for market in self._config.markets:
            book = self._store.get(market.exchange, market.symbol)
            if book is None:
                continue  # no live book this tick -> counts as a coverage gap
            age = (now - book.received_at).total_seconds()
            if age > self._max_book_age:
                # Silently stalled feed (no update, no raised error): drop the book so
                # this and subsequent ticks register as a coverage gap rather than
                # re-publishing stale liquidity (PRD P0-6). The collector repopulates
                # the store as soon as the feed delivers again.
                self._store.drop(market.exchange, market.symbol)
                log.warning(
                    "sampler.stale_book_dropped",
                    exchange=market.exchange,
                    symbol=market.symbol,
                    age_s=round(age, 1),
                )
                continue
            quote_to_usd = self._quote_to_usd(market, now)
            if quote_to_usd is None:
                continue  # FX cross missing/stale -> coverage gap, never fabricated
            try:
                sample = compute_sample(
                    market.exchange,
                    market.symbol,
                    now,
                    book.bids,
                    book.asks,
                    self._bands,
                    quote_to_usd=quote_to_usd,
                )
            except EmptyBookError:
                continue
            if sample.is_crossed:
                crossed += 1
            samples.append(sample)

        if samples:
            await self._db.insert_samples(samples)
        log.debug("sampler.tick", captured=len(samples), crossed=crossed)

    async def run(self) -> None:
        cadence = self._config.sampling.cadence_seconds
        log.info("sampler.started", cadence_seconds=cadence, markets=len(self._config.markets))
        # Align ticks to the wall clock so sample timestamps are stable across restarts.
        while True:
            start = asyncio.get_event_loop().time()
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001 - never let one tick kill the loop
                log.error("sampler.tick_failed", error=str(exc))
            elapsed = asyncio.get_event_loop().time() - start
            await asyncio.sleep(max(0.0, cadence - elapsed))
