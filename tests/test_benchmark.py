"""Tests for the pure benchmark comparison rule."""

from __future__ import annotations

import pytest

from ssv_mqm.benchmark import evaluate_benchmark


def test_no_target_is_not_evaluated():
    r = evaluate_benchmark(0.3, None, lower_is_better=True)
    assert r.target is None
    assert r.delta is None
    assert r.met is None
    assert r.status is None


def test_spread_below_target_overperforms():
    # Spread: lower is better, target is a ceiling.
    r = evaluate_benchmark(0.10, 0.15, lower_is_better=True)
    assert r.met is True
    assert r.status == "over"
    assert r.delta == pytest.approx(-0.05)


def test_spread_above_target_underperforms():
    r = evaluate_benchmark(0.25, 0.15, lower_is_better=True)
    assert r.met is False
    assert r.status == "under"
    assert r.delta == pytest.approx(0.10)


def test_depth_above_target_overperforms():
    # Depth: higher is better, target is a floor.
    r = evaluate_benchmark(60000, 50000, lower_is_better=False)
    assert r.met is True
    assert r.status == "over"
    assert r.delta == pytest.approx(10000)


def test_depth_below_target_underperforms():
    r = evaluate_benchmark(40000, 50000, lower_is_better=False)
    assert r.met is False
    assert r.status == "under"
    assert r.delta == pytest.approx(-10000)


@pytest.mark.parametrize("lower_is_better", [True, False])
def test_exact_equality_counts_as_met(lower_is_better):
    r = evaluate_benchmark(0.15, 0.15, lower_is_better=lower_is_better)
    assert r.met is True
    assert r.status == "over"
    assert r.delta == 0.0
