"""Configuration loading + validation.

Config comes from ``config.yaml`` (path via ``SSV_MQM_CONFIG``) with a few values
overridable by environment variables. Validation is via pydantic so a malformed config
fails fast and loudly at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# Quote currencies whose notional is treated as ~= USD directly (multiplier 1.0). Any
# other quote (a fiat like EUR) must declare an ``fx`` source so depth can be converted.
USD_QUOTES = frozenset({"USDT", "USDC", "USD"})


def quote_currency(symbol: str) -> str:
    """The quote leg of a CCXT unified ``BASE/QUOTE`` symbol (e.g. SSV/EUR -> EUR)."""
    return symbol.split("/", 1)[1]


class Market(BaseModel):
    exchange: str
    symbol: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.exchange, self.symbol)

    @property
    def quote(self) -> str:
        return quote_currency(self.symbol)


class FxSource(BaseModel):
    """A live venue cross used to convert a fiat quote currency to USD.

    The collector watches ``(exchange, symbol)`` like any other book; the sampler reads
    its mid as the rate. ``invert`` is True when the cross is quoted the other way round
    (e.g. ``USDC/EUR`` gives EUR-per-USD, so USD-per-EUR = 1/mid).
    """

    exchange: str
    symbol: str
    invert: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.exchange, self.symbol)


class SamplingConfig(BaseModel):
    cadence_seconds: int = Field(5, gt=0)
    # Books with no update for longer than this are treated as a coverage gap,
    # not sampled — guards against a silently stalled feed (PRD P0-6).
    max_book_age_seconds: float = Field(60.0, gt=0)


class DepthConfig(BaseModel):
    book_limit: int = Field(50, gt=0)
    bands_bps: list[int] = Field(default_factory=lambda: [100, 200])

    @field_validator("bands_bps")
    @classmethod
    def _non_empty_positive(cls, v: list[int]) -> list[int]:
        if not v or any(b <= 0 for b in v):
            raise ValueError("bands_bps must be a non-empty list of positive integers")
        return v


class CoverageConfig(BaseModel):
    threshold_pct: float = Field(90.0, ge=0, le=100)


class AggregatorConfig(BaseModel):
    run_hour_utc: int = Field(0, ge=0, le=23)
    run_minute_utc: int = Field(30, ge=0, le=59)


class BenchmarkTarget(BaseModel):
    """A per-(exchange, symbol) target the collected metrics are compared against.

    Each metric target is optional; an unset target means that metric is simply not
    benchmarked for this market. Spread is a *max* (lower = better, like ``avg_spread_pct``,
    in percent); depth targets are *mins* in USD (higher = better).
    """

    exchange: str
    symbol: str
    max_spread_pct: float | None = Field(default=None, ge=0)
    min_depth_100_usd: float | None = Field(default=None, ge=0)
    min_depth_200_usd: float | None = Field(default=None, ge=0)

    @property
    def key(self) -> tuple[str, str]:
        return (self.exchange, self.symbol)

    @model_validator(mode="after")
    def _at_least_one_target(self) -> BenchmarkTarget:
        if (
            self.max_spread_pct is None
            and self.min_depth_100_usd is None
            and self.min_depth_200_usd is None
        ):
            raise ValueError(
                f"benchmark for {self.exchange} {self.symbol} sets no metric target"
            )
        return self


class AppConfig(BaseModel):
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    depth: DepthConfig = Field(default_factory=DepthConfig)
    coverage: CoverageConfig = Field(default_factory=CoverageConfig)
    aggregator: AggregatorConfig = Field(default_factory=AggregatorConfig)
    markets: list[Market]
    benchmarks: list[BenchmarkTarget] = Field(default_factory=list)
    # FX conversion sources keyed by fiat quote currency (e.g. "EUR").
    fx: dict[str, FxSource] = Field(default_factory=dict)

    @field_validator("markets")
    @classmethod
    def _non_empty(cls, v: list[Market]) -> list[Market]:
        if not v:
            raise ValueError("at least one market must be configured")
        return v

    @model_validator(mode="after")
    def _benchmarks_match_markets(self) -> AppConfig:
        market_keys = {m.key for m in self.markets}
        for b in self.benchmarks:
            if b.key not in market_keys:
                raise ValueError(
                    f"benchmark references unconfigured market {b.exchange} {b.symbol}"
                )
        return self

    @model_validator(mode="after")
    def _fiat_quotes_have_fx(self) -> AppConfig:
        """Every non-USD-quote market must declare an fx source for its quote currency."""
        for m in self.markets:
            if m.quote not in USD_QUOTES and m.quote not in self.fx:
                raise ValueError(
                    f"market {m.exchange} {m.symbol} has non-USD quote {m.quote!r} "
                    f"but no fx['{m.quote}'] conversion source is configured"
                )
        return self

    def fx_sources(self) -> list[FxSource]:
        """Distinct FX-cross books to watch, deduplicated by (exchange, symbol)."""
        seen: dict[tuple[str, str], FxSource] = {}
        for src in self.fx.values():
            seen.setdefault(src.key, src)
        return list(seen.values())

    @property
    def database_url(self) -> str:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL environment variable is required")
        return url

    @property
    def samples_per_day(self) -> int:
        return 86_400 // self.sampling.cadence_seconds

    def exchange_ids(self) -> list[str]:
        """Distinct CCXT exchange ids across configured markets, preserving order."""
        seen: dict[str, None] = {}
        for m in self.markets:
            seen.setdefault(m.exchange, None)
        return list(seen)


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load and validate the application config from YAML."""
    cfg_path = Path(path or os.environ.get("SSV_MQM_CONFIG") or DEFAULT_CONFIG_PATH)
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return AppConfig.model_validate(raw)
