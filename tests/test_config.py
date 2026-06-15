"""Tests for config loading + validation."""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from ssv_mqm.config import AppConfig, load_config


def _write(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_defaults_applied(tmp_path):
    path = _write(
        tmp_path,
        """
        markets:
          - { exchange: binance, symbol: "SSV/USDT" }
        """,
    )
    cfg = load_config(path)
    assert cfg.sampling.cadence_seconds == 5
    assert cfg.sampling.max_book_age_seconds == 60.0
    assert cfg.depth.bands_bps == [100, 200]
    assert cfg.coverage.threshold_pct == 90.0
    assert cfg.samples_per_day == 86_400 // 5
    assert cfg.exchange_ids() == ["binance"]


def test_full_config_parsed(tmp_path):
    path = _write(
        tmp_path,
        """
        sampling: { cadence_seconds: 10 }
        depth: { book_limit: 25, bands_bps: [50, 100] }
        coverage: { threshold_pct: 95 }
        markets:
          - { exchange: binance, symbol: "SSV/USDT" }
          - { exchange: okx, symbol: "SSV/USDC" }
        """,
    )
    cfg = load_config(path)
    assert cfg.sampling.cadence_seconds == 10
    assert cfg.depth.bands_bps == [50, 100]
    assert cfg.samples_per_day == 8_640
    assert set(cfg.exchange_ids()) == {"binance", "okx"}


def test_empty_markets_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"markets": []})


def test_bad_bands_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {"depth": {"bands_bps": []}, "markets": [{"exchange": "binance", "symbol": "SSV/USDT"}]}
        )


def test_database_url_requires_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = load_config(_write(tmp_path, "markets:\n  - { exchange: binance, symbol: 'SSV/USDT' }\n"))
    with pytest.raises(RuntimeError):
        _ = cfg.database_url


def test_benchmarks_default_empty(tmp_path):
    cfg = load_config(_write(tmp_path, "markets:\n  - { exchange: binance, symbol: 'SSV/USDT' }\n"))
    assert cfg.benchmarks == []


def test_benchmarks_parsed(tmp_path):
    path = _write(
        tmp_path,
        """
        markets:
          - { exchange: binance, symbol: "SSV/USDT" }
        benchmarks:
          - exchange: binance
            symbol: "SSV/USDT"
            max_spread_pct: 0.15
            min_depth_100_usd: 50000
        """,
    )
    cfg = load_config(path)
    assert len(cfg.benchmarks) == 1
    b = cfg.benchmarks[0]
    assert b.key == ("binance", "SSV/USDT")
    assert b.max_spread_pct == 0.15
    assert b.min_depth_100_usd == 50000
    assert b.min_depth_200_usd is None


def test_benchmark_unknown_market_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "markets": [{"exchange": "binance", "symbol": "SSV/USDT"}],
                "benchmarks": [{"exchange": "okx", "symbol": "SSV/USDT", "max_spread_pct": 0.2}],
            }
        )


def test_benchmark_without_target_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                "markets": [{"exchange": "binance", "symbol": "SSV/USDT"}],
                "benchmarks": [{"exchange": "binance", "symbol": "SSV/USDT"}],
            }
        )
