"""Structured logging setup (structlog -> stdout JSON-ish console)."""

from __future__ import annotations

import logging
import os

import structlog


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
