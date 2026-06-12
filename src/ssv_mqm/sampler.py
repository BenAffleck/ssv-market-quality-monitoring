"""Periodic sampling task (PRD P0-2).

Every ``cadence_seconds`` it snapshots each live book from the :class:`BookStore`,
computes per-sample metrics, and batch-inserts them. Markets without a current book
(disconnected/resyncing) are simply skipped for that tick — the missing samples show up
as reduced coverage in the daily aggregate (PRD P0-6), never as fabricated data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .collector import BookStore
from .config import AppConfig
from .db import Database
from .log import get_logger
from .metrics import EmptyBookError, compute_sample
from .models import SampleMetrics

log = get_logger(__name__)


class Sampler:
    def __init__(self, config: AppConfig, store: BookStore, db: Database) -> None:
        self._config = config
        self._store = store
        self._db = db
        self._bands = tuple(config.depth.bands_bps)
        self._max_book_age = config.sampling.max_book_age_seconds

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
            try:
                sample = compute_sample(
                    market.exchange,
                    market.symbol,
                    now,
                    book.bids,
                    book.asks,
                    self._bands,
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
