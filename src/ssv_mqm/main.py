"""Entrypoint for the collector + sampler service (PRD P0-1, P0-2).

Builds the CCXT Pro collectors and the periodic sampler over a shared in-memory book
store, then runs them concurrently until interrupted. Outer process supervision (restart
on crash) is provided by Docker Compose's ``restart: unless-stopped`` (PRD P0-7).
"""

from __future__ import annotations

import asyncio
import signal

from .collector import BookStore, Collector
from .config import load_config
from .db import Database
from .log import configure_logging, get_logger
from .sampler import Sampler

log = get_logger(__name__)


async def main() -> None:
    configure_logging()
    config = load_config()

    db = Database(config.database_url)
    await db.connect()
    await db.bootstrap_schema()
    await db.seed_benchmark_targets(config.benchmarks)

    store = BookStore()
    collector = Collector(config, store)
    sampler = Sampler(config, store, db)

    collector_task = asyncio.create_task(collector.run(), name="collector")
    sampler_task = asyncio.create_task(sampler.run(), name="sampler")
    tasks = [collector_task, sampler_task]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # e.g. on platforms without signal handlers
            pass

    done_waiter = asyncio.create_task(asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED))
    stop_waiter = asyncio.create_task(stop.wait())
    await asyncio.wait({done_waiter, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)

    log.info("main.shutting_down")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await collector.close()
    await db.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
