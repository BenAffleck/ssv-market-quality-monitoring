"""Order-book ingestion via CCXT Pro (PRD P0-1, P0-7).

One supervised asyncio task per (exchange, symbol). Each task runs CCXT Pro's unified
``watch_order_book`` in a loop, keeping the latest book in a shared in-memory store that
the sampler reads. CCXT Pro itself handles the snapshot + incremental-diff merge, the
per-exchange sequence/update-id continuity, and KuCoin's public-token fetch/refresh; on a
raised desync/disconnect we reset the cached book and reconnect with exponential backoff.

Security (P0-8): exchanges are constructed with NO apiKey/secret — public feeds only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import ccxt.pro as ccxtpro

from .config import AppConfig
from .log import get_logger

log = get_logger(__name__)

_MAX_BACKOFF_SECONDS = 60.0

# Per-exchange override of the watch_order_book ``limit`` argument, where the configured
# default would otherwise select a non-public channel. OKX maps limit==50 to the
# VIP4/auth-only "books50-l2-tbt" channel; passing None keeps the public full-depth
# "books" channel (~400 levels — ample for the narrow +/-2% SSV band).
_EXCHANGE_BOOK_LIMIT: dict[str, int | None] = {
    "okx": None,
}


@dataclass
class BookSnapshot:
    """Latest order book for one market, plus when it was received."""

    bids: list[list[float]]
    asks: list[list[float]]
    received_at: datetime


class BookStore:
    """Thread-free shared store of the latest book per (exchange, symbol).

    Single-threaded asyncio: plain dict assignment/reads are atomic between awaits.
    """

    def __init__(self) -> None:
        self._books: dict[tuple[str, str], BookSnapshot] = {}

    def update(self, exchange: str, symbol: str, snapshot: BookSnapshot) -> None:
        self._books[(exchange, symbol)] = snapshot

    def get(self, exchange: str, symbol: str) -> BookSnapshot | None:
        return self._books.get((exchange, symbol))

    def drop(self, exchange: str, symbol: str) -> None:
        self._books.pop((exchange, symbol), None)


class Collector:
    """Builds CCXT Pro exchanges and runs one watch loop per validated market."""

    def __init__(self, config: AppConfig, store: BookStore) -> None:
        self._config = config
        self._store = store
        self._exchanges: dict[str, ccxtpro.Exchange] = {}

    async def _build_exchange(self, exchange_id: str) -> ccxtpro.Exchange:
        klass = getattr(ccxtpro, exchange_id)
        ex = klass({"enableRateLimit": True, "newUpdates": False})
        return ex

    async def _validate_markets(self, ex: ccxtpro.Exchange, symbols: list[str]) -> list[str]:
        """Load markets and keep only symbols that the venue actually lists.

        A delisted/renamed symbol is logged and skipped rather than emitting empty data
        as if healthy (PRD edge case).
        """
        await ex.load_markets()
        valid: list[str] = []
        for symbol in symbols:
            if symbol in ex.markets:
                valid.append(symbol)
            else:
                log.warning("collector.symbol_unavailable", exchange=ex.id, symbol=symbol)
        return valid

    async def _watch_market(self, ex: ccxtpro.Exchange, symbol: str) -> None:
        """Watch one market forever, reconnecting on error with backoff."""
        backoff = 1.0
        limit = _EXCHANGE_BOOK_LIMIT.get(ex.id, self._config.depth.book_limit)
        while True:
            try:
                book = await ex.watch_order_book(symbol, limit)
                snapshot = BookSnapshot(
                    bids=[[float(p), float(s)] for p, s in book["bids"]],
                    asks=[[float(p), float(s)] for p, s in book["asks"]],
                    received_at=datetime.now(timezone.utc),
                )
                self._store.update(ex.id, symbol, snapshot)
                backoff = 1.0  # healthy update resets backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any feed error
                # Drop the stale book so the sampler reports a gap, then resync.
                self._store.drop(ex.id, symbol)
                log.warning(
                    "collector.reconnect",
                    exchange=ex.id,
                    symbol=symbol,
                    error=str(exc),
                    backoff_s=round(backoff, 1),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    async def _run_exchange(self, exchange_id: str, symbols: list[str]) -> None:
        """Init one exchange (retrying on transient failure) and watch its markets.

        A failed ``load_markets`` (DNS blip, venue maintenance) must not silence the
        venue until the next deploy — the process stays alive, so the outer supervisor
        never kicks in. Retry init forever with backoff instead (PRD P0-7).
        """
        backoff = 1.0
        while True:
            ex = None
            try:
                ex = await self._build_exchange(exchange_id)
                valid_symbols = await self._validate_markets(ex, symbols)
                break
            except Exception as exc:  # noqa: BLE001 - retry any init error
                if ex is not None:
                    try:
                        await ex.close()
                    except Exception:  # noqa: BLE001
                        pass
                log.warning(
                    "collector.exchange_init_retry",
                    exchange=exchange_id,
                    error=str(exc),
                    backoff_s=round(backoff, 1),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

        self._exchanges[exchange_id] = ex
        if not valid_symbols:
            log.error("collector.no_valid_symbols", exchange=exchange_id, configured=symbols)
            return
        for symbol in valid_symbols:
            log.info("collector.watching", exchange=exchange_id, symbol=symbol)
        await asyncio.gather(*(self._watch_market(ex, symbol) for symbol in valid_symbols))

    async def run(self) -> None:
        """Start one runner task per exchange and run until cancelled."""
        # Group configured symbols by exchange.
        by_exchange: dict[str, list[str]] = {}
        for m in self._config.markets:
            by_exchange.setdefault(m.exchange, []).append(m.symbol)

        tasks = [
            asyncio.create_task(self._run_exchange(exchange_id, symbols), name=exchange_id)
            for exchange_id, symbols in by_exchange.items()
        ]
        try:
            await asyncio.gather(*tasks)
            # Watch loops never return, so reaching here means every exchange came up
            # with zero valid symbols — a config/listing problem, not a feed problem.
            raise RuntimeError("no valid markets to watch — check config and venue listings")
        finally:
            await self.close()

    async def close(self) -> None:
        for ex in self._exchanges.values():
            try:
                await ex.close()
            except Exception:  # noqa: BLE001
                pass
