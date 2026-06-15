"""Daily reference-price collection for the correlation/beta KPIs.

Fetches daily UTC close prices via CCXT REST ``fetch_ohlcv(timeframe='1d')`` from one
canonical venue per asset — SSV plus reference projects (ETH as the benchmark, RPL as the
first comparison). Unlike the live order-book collector this needs only one closed candle
per day, and the ``limit`` argument lets us **backfill** history so correlation/beta are
meaningful from day one rather than after weeks of accumulation.

Security (P0-8): exchanges are constructed with NO apiKey/secret — public data only. This
uses ``ccxt.async_support`` (REST); the websocket ``ccxt.pro`` is for the live order-book
path in :mod:`ssv_mqm.collector`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import ccxt.async_support as ccxt

from .config import AppConfig
from .log import get_logger
from .models import AssetClose

log = get_logger(__name__)


def ohlcv_to_closes(
    asset: str, source: str, rows: list[list[float]], today_utc: date
) -> list[AssetClose]:
    """Map CCXT OHLCV rows ``[ts_ms, o, h, l, c, v]`` to :class:`AssetClose` daily closes.

    The current UTC day's candle is still in progress, so any row on/after ``today_utc`` is
    dropped — only closed days are stored (the same "publish yesterday, not today" discipline
    as the daily aggregator). A row with a missing close is skipped.
    """
    out: list[AssetClose] = []
    for row in rows:
        ts_ms, close = row[0], row[4]
        if close is None:
            continue
        day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
        if day >= today_utc:
            continue
        out.append(AssetClose(day=day, asset=asset, close_usd=float(close), source=source))
    return out


def _build_exchange(exchange_id: str) -> ccxt.Exchange:
    klass = getattr(ccxt, exchange_id)
    return klass({"enableRateLimit": True})


async def fetch_daily_closes(config: AppConfig, *, limit: int) -> list[AssetClose]:
    """Fetch up to ``limit`` recent daily closes for every configured price asset.

    Assets are grouped by venue so one client serves all assets on it. A symbol not listed on
    its venue is logged and skipped (delisting-safe, like the order-book collector); a venue
    or fetch error skips that asset rather than fabricating data.
    """
    by_exchange: dict[str, list] = {}
    for a in config.prices.assets:
        by_exchange.setdefault(a.exchange, []).append(a)

    today = datetime.now(timezone.utc).date()
    closes: list[AssetClose] = []
    for exchange_id, venue_assets in by_exchange.items():
        ex = _build_exchange(exchange_id)
        try:
            await ex.load_markets()
            for a in venue_assets:
                if a.symbol not in ex.markets:
                    log.warning(
                        "prices.symbol_unavailable",
                        exchange=exchange_id,
                        symbol=a.symbol,
                        asset=a.asset,
                    )
                    continue
                try:
                    rows = await ex.fetch_ohlcv(a.symbol, timeframe="1d", limit=limit)
                except Exception as exc:  # noqa: BLE001 - skip this asset, never fabricate
                    log.warning(
                        "prices.fetch_failed",
                        exchange=exchange_id,
                        symbol=a.symbol,
                        asset=a.asset,
                        error=str(exc),
                    )
                    continue
                closes.extend(ohlcv_to_closes(a.asset, f"{exchange_id}:{a.symbol}", rows, today))
        finally:
            await ex.close()

    log.info("prices.fetched", assets=len(config.prices.assets), closes=len(closes))
    return closes
